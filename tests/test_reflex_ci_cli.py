"""Tests for the reflex CI entry point: diff parsing, sourcing, verdicts, CLI.

All tests are offline: no network access and no real git dependency
(``git`` is only exercised in a temporary non-repository directory where
it is expected to fail gracefully).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from harness.reflex.ci import (
    RULE_N_PLUS_ONE,
    RULE_OWASP,
    RULE_SECRET_LEAK,
    AuditFinding,
    get_diff_snippets,
    is_blocking_finding,
    parse_git_diff,
    phase_verdict,
    scan_files,
)
from harness.reflex.__main__ import build_parser, main

SECRET_SNIPPET = "logger.debug(f'token: {secret}')"
OWASP_SNIPPET = "query = f\"SELECT * FROM users WHERE email = '{req.query['email']}'\""

SINGLE_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1234567..89abcde 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,3 @@
 import os
+logger.debug(f'token: {secret}')
 print('hi')
"""

MULTI_FILE_DIFF = """\
diff --git a/src/app.py b/src/app.py
index 1234567..89abcde 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
 import os
+print('one')
diff --git a/src/util.py b/src/util.py
index 1111111..2222222 100644
--- a/src/util.py
+++ b/src/util.py
@@ -0,0 +1 @@
+print('two')
"""

BINARY_DIFF = """\
diff --git a/logo.png b/logo.png
index 1234567..89abcde 100644
Binary files a/logo.png and b/logo.png differ
"""

RENAME_DIFF = """\
diff --git a/old_name.py b/new_name.py
similarity index 90%
rename from old_name.py
rename to new_name.py
index 1234567..89abcde 100644
--- a/old_name.py
+++ b/new_name.py
@@ -1 +1,2 @@
 import os
+print('renamed')
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Force the mock backend and default phase for deterministic tests."""
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("REFLEX_AUTONOMY_PHASE", raising=False)


def _finding(rule: str, severity: float, file: str = "src/x.py") -> AuditFinding:
    """Build a minimal finding for verdict tests."""
    return AuditFinding(
        file=file,
        rule=rule,
        severity=severity,
        snippet="snippet",
        recommendation="recommendation",
    )


# ---------------------------------------------------------------------------
# parse_git_diff
# ---------------------------------------------------------------------------


class TestParseGitDiff:
    def test_single_file(self):
        snippets = parse_git_diff(SINGLE_FILE_DIFF)
        assert len(snippets) == 1
        assert snippets[0]["file"] == "src/app.py"
        assert snippets[0]["type"] == "diff"
        assert "token" in snippets[0]["content"]

    def test_multiple_files(self):
        snippets = parse_git_diff(MULTI_FILE_DIFF)
        assert [s["file"] for s in snippets] == ["src/app.py", "src/util.py"]
        assert "print('one')" in snippets[0]["content"]
        assert "print('two')" in snippets[1]["content"]

    def test_skips_plus_plus_plus_header(self):
        snippets = parse_git_diff(SINGLE_FILE_DIFF)
        assert "+++" not in snippets[0]["content"]
        assert "src/app.py" not in snippets[0]["content"]

    def test_added_lines_only_not_context(self):
        snippets = parse_git_diff(SINGLE_FILE_DIFF)
        # Context lines (space prefix) are not collected.
        assert "import os" not in snippets[0]["content"]

    def test_binary_marker_skipped(self):
        assert parse_git_diff(BINARY_DIFF) == []

    def test_rename_uses_destination_path(self):
        snippets = parse_git_diff(RENAME_DIFF)
        assert len(snippets) == 1
        assert snippets[0]["file"] == "new_name.py"
        assert "print('renamed')" in snippets[0]["content"]

    def test_empty_string(self):
        assert parse_git_diff("") == []
        assert parse_git_diff("   \n  ") == []

    def test_malformed_header_does_not_raise(self):
        diff = "diff --git a/only\n+added line\n"
        snippets = parse_git_diff(diff)
        assert len(snippets) == 1
        assert snippets[0]["file"] == "unknown"
        assert "added line" in snippets[0]["content"]

    def test_header_without_additions_produces_no_snippet(self):
        diff = "diff --git a/x.py b/x.py\nindex 1..2 100644\n--- a/x.py\n+++ b/x.py\n"
        assert parse_git_diff(diff) == []


# ---------------------------------------------------------------------------
# get_diff_snippets
# ---------------------------------------------------------------------------


class TestGetDiffSnippets:
    def test_returns_empty_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert get_diff_snippets("HEAD~1", "HEAD") == []

    def test_git_missing_returns_empty(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise FileNotFoundError("git not installed")

        monkeypatch.setattr(subprocess, "run", _boom)
        assert get_diff_snippets() == []

    def test_nonzero_exit_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 128, "", "fatal"),
        )
        assert get_diff_snippets() == []

    def test_empty_diff_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
        )
        assert get_diff_snippets() == []

    def test_real_diff_is_parsed(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, SINGLE_FILE_DIFF, ""),
        )
        snippets = get_diff_snippets()
        assert len(snippets) == 1
        assert snippets[0]["file"] == "src/app.py"


# ---------------------------------------------------------------------------
# scan_files
# ---------------------------------------------------------------------------


class TestScanFiles:
    def test_readable_file_becomes_code_snippet(self, tmp_path):
        target = tmp_path / "a.py"
        target.write_text("print('hello')\n", encoding="utf-8")
        snippets = scan_files([str(target)])
        assert len(snippets) == 1
        assert snippets[0]["type"] == "code"
        assert snippets[0]["file"] == str(target)
        assert "hello" in snippets[0]["content"]

    def test_missing_file_skipped(self, tmp_path):
        assert scan_files([str(tmp_path / "nope.py")]) == []

    def test_binary_file_skipped(self, tmp_path):
        target = tmp_path / "blob.bin"
        target.write_bytes(b"\x00\x01\x02binary")
        assert scan_files([str(target)]) == []

    def test_mixed_inputs_keep_only_readable(self, tmp_path):
        good = tmp_path / "good.py"
        good.write_text("x = 1\n", encoding="utf-8")
        bad = tmp_path / "bad.bin"
        bad.write_bytes(b"\x00\xff")
        snippets = scan_files([str(good), str(bad), str(tmp_path / "gone.py")])
        assert len(snippets) == 1
        assert snippets[0]["file"] == str(good)


# ---------------------------------------------------------------------------
# phase_verdict / is_blocking_finding
# ---------------------------------------------------------------------------


class TestPhaseVerdict:
    def test_shadow_passes_with_critical_findings(self):
        findings = [_finding(RULE_SECRET_LEAK, 9.6), _finding(RULE_OWASP, 9.9)]
        passed, reason = phase_verdict(findings, "shadow")
        assert passed is True
        assert "shadow" in reason

    def test_advisory_passes_with_critical_findings(self):
        findings = [_finding(RULE_SECRET_LEAK, 9.6)]
        passed, reason = phase_verdict(findings, "advisory")
        assert passed is True
        assert "1 findings" in reason

    def test_gating_blocks_on_secret_rule(self):
        passed, reason = phase_verdict([_finding(RULE_SECRET_LEAK, 9.6)], "gating")
        assert passed is False
        assert RULE_SECRET_LEAK in reason

    def test_gating_blocks_on_owasp_severity_above_7(self):
        passed, _ = phase_verdict([_finding(RULE_OWASP, 8.5)], "gating")
        assert passed is False

    def test_gating_ignores_low_severity_owasp(self):
        passed, _ = phase_verdict([_finding(RULE_OWASP, 7.0)], "gating")
        assert passed is True

    def test_gating_passes_on_n_plus_one_only(self):
        findings = [_finding(RULE_N_PLUS_ONE, 9.0)]
        passed, reason = phase_verdict(findings, "gating")
        assert passed is True
        assert "non-blocking" in reason

    def test_gating_passes_with_no_findings(self):
        passed, _ = phase_verdict([], "gating")
        assert passed is True

    def test_phase_is_case_insensitive(self):
        passed, _ = phase_verdict([_finding(RULE_SECRET_LEAK, 9.6)], "GATING")
        assert passed is False

    def test_is_blocking_finding(self):
        assert is_blocking_finding(_finding(RULE_SECRET_LEAK, 1.0)) is True
        assert is_blocking_finding(_finding(RULE_OWASP, 7.1)) is True
        assert is_blocking_finding(_finding(RULE_OWASP, 7.0)) is False
        assert is_blocking_finding(_finding(RULE_N_PLUS_ONE, 10.0)) is False


# ---------------------------------------------------------------------------
# main() CLI
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_files_scan_secret_json_findings(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        code = main(["--files", str(target), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0  # default phase is advisory
        assert payload["findings"], "expected at least one finding"
        rules = {f["rule"] for f in payload["findings"]}
        assert RULE_SECRET_LEAK in rules

    def test_json_output_shape(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        main(["--files", str(target), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert set(payload) == {"passed", "phase", "findings", "counts"}
        assert payload["phase"] == "advisory"
        assert payload["counts"]["snippets"] == 1
        assert payload["counts"]["findings"] == len(payload["findings"])

    def test_summary_writes_markdown_table(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        summary = tmp_path / "summary.md"
        code = main(["--files", str(target), "--summary", str(summary)])
        assert code == 0
        text = summary.read_text(encoding="utf-8")
        assert "| File | Rule | Severity | Escalate |" in text
        assert RULE_SECRET_LEAK in text

    def test_gating_phase_exits_1_on_secret(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        code = main(["--files", str(target), "--phase", "gating", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["passed"] is False

    def test_shadow_phase_exits_0_on_secret(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        code = main(["--files", str(target), "--phase", "shadow"])
        assert code == 0

    def test_phase_from_environment(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("REFLEX_AUTONOMY_PHASE", "gating")
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        code = main(["--files", str(target), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["phase"] == "gating"

    def test_diff_mode_with_mocked_git(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(a[0], 0, SINGLE_FILE_DIFF, ""),
        )
        code = main(["--diff", "HEAD~1", "HEAD", "--phase", "gating", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["findings"][0]["file"] == "src/app.py"

    def test_no_snippets_exits_0(self, tmp_path, capsys):
        code = main(["--files", str(tmp_path / "missing.py")])
        out = capsys.readouterr().out
        assert code == 0
        assert "no snippets" in out

    def test_no_snippets_json(self, tmp_path, capsys):
        code = main(["--files", str(tmp_path / "missing.py"), "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["passed"] is True
        assert payload["findings"] == []

    def test_remediate_flow_applies_patches(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        code = main(["--files", str(target), "--remediate", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["counts"]["patches_applied"] >= 1

    def test_default_dogfood_scan_runs(self, capsys):
        project_root = Path(__file__).resolve().parents[1]
        cwd = Path.cwd()
        try:
            import os

            os.chdir(project_root)
            code = main(["--json"])
        finally:
            os.chdir(cwd)
        payload = json.loads(capsys.readouterr().out)
        assert code in (0, 1)
        assert payload["counts"]["snippets"] > 0

    def test_unknown_args_raise_system_exit_2(self):
        with pytest.raises(SystemExit) as excinfo:
            main(["--bogus-flag"])
        assert excinfo.value.code == 2

    def test_diff_and_files_are_mutually_exclusive(self, tmp_path):
        with pytest.raises(SystemExit) as excinfo:
            main(["--diff", "a", "b", "--files", str(tmp_path)])
        assert excinfo.value.code == 2

    def test_parser_defaults(self):
        args = build_parser().parse_args([])
        assert args.phase is None
        assert args.json is False
        assert args.remediate is False
        assert args.summary is None

    def test_table_output_when_not_json(self, tmp_path, capsys):
        target = tmp_path / "leaky.py"
        target.write_text(SECRET_SNIPPET + "\n", encoding="utf-8")
        code = main(["--files", str(target)])
        out = capsys.readouterr().out
        assert code == 0
        assert "rule" in out and "severity" in out and "escalate" in out
        assert RULE_SECRET_LEAK in out
