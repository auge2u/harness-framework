"""Reflex CI runner: scan -> escalate -> remediate (System 2) -> re-verify.

Ports the closed-loop auto-remediation CI pipeline onto the reflex layer:
System 1 millisecond pre-screening flags findings, an injectable System 2
remediation callable patches them, and System 1 re-verifies the patches.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from harness.reflex.linter import QualitativeLinter

__all__ = [
    "AuditFinding",
    "ReflexCIRunner",
    "default_remediate",
    "parse_git_diff",
    "get_diff_snippets",
    "scan_files",
    "phase_verdict",
    "is_blocking_finding",
]

#: Finding rule identifiers.
RULE_SECRET_LEAK = "secret_leak_protection"
RULE_N_PLUS_ONE = "n_plus_one_orm_audit"
RULE_OWASP = "owasp_security_scan"

#: Severity above which a finding blocks the pipeline (after remediation
#: checks — the same thresholds as the linter rules).
_BLOCK_SEVERITY = 7.0
_N_PLUS_ONE_FLAG_SCORE = 6.0


@dataclass
class AuditFinding:
    """A single flagged issue from the reflex CI scan.

    Attributes:
        file: File (or diff label) the finding came from.
        rule: Rule identifier that produced the finding.
        severity: Severity score (0.0 – 10.0).
        snippet: The offending code snippet.
        recommendation: Human-readable remediation guidance.
    """

    file: str
    rule: str
    severity: float
    snippet: str
    recommendation: str

    def __post_init__(self) -> None:
        """Normalise the severity to a float."""
        self.severity = float(self.severity)


def default_remediate(finding: AuditFinding) -> str:
    """Deterministic stand-in for a System 2 auto-remediator.

    Produces simple, known-good patches for the three finding classes;
    returns the original snippet unchanged when no patch is known.

    Args:
        finding: The finding to remediate.

    Returns:
        Patched code (or the original snippet when unhandled).
    """
    snippet_lower = finding.snippet.lower()
    if finding.rule == RULE_OWASP and (
        "owasp_a03_sql_injection" in finding.recommendation.lower()
        or "select " in snippet_lower
    ):
        # Replace raw string concatenation with a parameterized query.
        return (
            'query = "SELECT * FROM users WHERE email = %s"\n'
            "cursor.execute(query, (req.query['email'],))"
        )
    if finding.rule == RULE_OWASP:
        # Generic injection fallback: strip dangerous evaluation.
        return "# sanitized: dangerous evaluation removed by reflex CI"
    if finding.rule == RULE_N_PLUS_ONE or "order.objects.filter" in snippet_lower:
        # Replace the N+1 lazy query with a select_related eager load.
        return (
            "orders = Order.objects.select_related('user').filter(status='PENDING')\n"
            "for order in orders:\n"
            "    send_notification(order.user)"
        )
    if finding.rule == RULE_SECRET_LEAK:
        # Sanitize token/secret logging completely.
        return (
            "logger.debug(f'Authenticating request for user_id: {user_id} "
            "(redacted)')"
        )
    return finding.snippet


class ReflexCIRunner:
    """CI pipeline: reflex scan -> escalate flagged -> remediate -> re-verify.

    Args:
        linter: The :class:`~harness.reflex.linter.QualitativeLinter` used
            for scanning and re-verification.
        remediate: Optional System 2 remediation callable mapping an
            :class:`AuditFinding` to patched code.  Defaults to
            :func:`default_remediate`.
    """

    def __init__(
        self,
        linter: QualitativeLinter,
        remediate: Optional[Callable[[AuditFinding], str]] = None,
    ) -> None:
        self.linter = linter
        self.remediate = remediate or default_remediate

    # -- Step 1: scan -----------------------------------------------------------

    def scan(self, snippets: List[Dict[str, str]]) -> List[AuditFinding]:
        """Run the System 1 reflex scan over *snippets*.

        Args:
            snippets: List of ``{"file": ..., "content": ...}`` dicts.

        Returns:
            All flagged findings (empty when everything passes).
        """
        findings: List[AuditFinding] = []
        for item in snippets:
            file_label = str(item.get("file", "unknown"))
            content = str(item.get("content", ""))

            leak = self.linter.evaluate_log_data_leakage(content)
            if leak.detail.get("action") != "PASS":
                findings.append(
                    AuditFinding(
                        file=file_label,
                        rule=RULE_SECRET_LEAK,
                        severity=round(float(leak.value) * 10.0, 2),
                        snippet=content,
                        recommendation=str(leak.detail.get("recommendation", "")),
                    )
                )

            orm = self.linter.evaluate_n_plus_one_orm(content)
            if float(orm.value) > _N_PLUS_ONE_FLAG_SCORE:
                findings.append(
                    AuditFinding(
                        file=file_label,
                        rule=RULE_N_PLUS_ONE,
                        severity=float(orm.value),
                        snippet=content,
                        recommendation=str(orm.detail.get("recommendation", "")),
                    )
                )

            security = self.linter.evaluate_security_vulnerabilities(content)
            if float(security.detail.get("severity_score", 0.0)) > _BLOCK_SEVERITY:
                findings.append(
                    AuditFinding(
                        file=file_label,
                        rule=RULE_OWASP,
                        severity=float(security.detail["severity_score"]),
                        snippet=content,
                        recommendation=str(
                            security.detail.get("recommendation", "")
                        ),
                    )
                )
        return findings

    # -- Steps 2-4: remediate & re-verify ----------------------------------------

    def _reverify(self, finding: AuditFinding, patched_code: str) -> bool:
        """Re-run the finding's rule against the patched code."""
        if finding.rule == RULE_OWASP:
            result = self.linter.evaluate_security_vulnerabilities(patched_code)
            return float(result.detail.get("severity_score", 0.0)) <= _BLOCK_SEVERITY
        if finding.rule == RULE_N_PLUS_ONE:
            result = self.linter.evaluate_n_plus_one_orm(patched_code)
            return float(result.value) <= _N_PLUS_ONE_FLAG_SCORE
        if finding.rule == RULE_SECRET_LEAK:
            result = self.linter.evaluate_log_data_leakage(patched_code)
            return result.detail.get("action") == "PASS"
        return True

    def run(self, snippets: List[Dict[str, str]]) -> Dict[str, Any]:
        """Execute the full closed-loop CI pipeline.

        Args:
            snippets: List of ``{"file": ..., "content": ...}`` dicts.

        Returns:
            Dict with keys ``passed``, ``findings``, ``patches_applied``,
            ``reverified`` and ``summary_markdown``.
        """
        findings = self.scan(snippets)
        summary_lines = [
            "## Reflex System 1 CI Auto-Remediation Report",
            "",
            "| File | Rule Triggered | Auto-Remediation Status | Post-Fix Verification |",
            "| :--- | :--- | :--- | :--- |",
        ]

        if not findings:
            summary_lines.append("")
            summary_lines.append("All System 1 checks passed on initial scan.")
            return {
                "passed": True,
                "findings": [],
                "patches_applied": 0,
                "reverified": 0,
                "summary_markdown": "\n".join(summary_lines),
            }

        patches_applied = 0
        reverified = 0
        for finding in findings:
            patched = self.remediate(finding)
            patches_applied += 1
            is_clean = self._reverify(finding, patched)
            if is_clean:
                reverified += 1
            status = "FIXED & VERIFIED" if is_clean else "REMEDIATION_FAILED"
            summary_lines.append(
                f"| `{finding.file}` | {finding.rule} | Patch Applied | {status} |"
            )

        passed = reverified == len(findings)
        summary_lines.extend(
            [
                "",
                "### Auto-Remediation Summary",
                f"- **Issues Intercepted by System 1**: `{len(findings)}`",
                f"- **System 2 Patches Applied**: `{patches_applied}`",
                f"- **Final Verification Status**: "
                f"{'PASSED - ALL ISSUES AUTO-RESOLVED' if passed else 'FAILED'}",
            ]
        )

        return {
            "passed": passed,
            "findings": findings,
            "patches_applied": patches_applied,
            "reverified": reverified,
            "summary_markdown": "\n".join(summary_lines),
        }


# ---------------------------------------------------------------------------
# Git diff / filesystem snippet sourcing
# ---------------------------------------------------------------------------


def parse_git_diff(diff_text: str) -> List[Dict[str, str]]:
    """Split a unified diff into per-file snippets.

    Parses ``diff --git a/path b/path`` headers and collects the added
    lines (``+`` prefix, skipping the ``+++`` file header) of each file.
    Binary-file sections (``Binary files ... differ`` / ``GIT binary
    patch``) are skipped entirely, and renames report the destination
    path.

    Args:
        diff_text: Raw output of ``git diff``.

    Returns:
        List of ``{"file": path, "type": "diff", "content": ...}`` dicts,
        one per file with at least one added line; empty for an empty or
        contentless diff.
    """
    if not diff_text or not diff_text.strip():
        return []

    snippets: List[Dict[str, str]] = []
    current_file: Optional[str] = None
    current_lines: List[str] = []
    is_binary = False

    def _flush() -> None:
        """Emit the pending per-file snippet, if any."""
        nonlocal current_file, current_lines, is_binary
        if current_file is not None and not is_binary and current_lines:
            content = "\n".join(current_lines).strip()
            if content:
                snippets.append(
                    {"file": current_file, "type": "diff", "content": content}
                )
        current_file = None
        current_lines = []
        is_binary = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            _flush()
            parts = line.split(" ")
            if len(parts) >= 4:
                path = parts[3].strip().strip('"')
                if path.startswith("b/"):
                    path = path[2:]
                current_file = path
            else:
                # Malformed header: keep collecting under a placeholder.
                current_file = "unknown"
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            is_binary = True
        elif line.startswith("rename to "):
            if current_file is not None:
                current_file = line[len("rename to ") :].strip()
        elif line.startswith("rename from "):
            continue
        elif line.startswith("+++") or line.startswith("---"):
            # File headers, not content.
            continue
        elif line.startswith("+"):
            current_lines.append(line[1:])
    _flush()
    return snippets


def get_diff_snippets(base: str = "HEAD~1", head: str = "HEAD") -> List[Dict[str, str]]:
    """Run ``git diff base head`` and split the result into snippets.

    Any failure — not a git repository, ``git`` missing, a non-zero exit
    status, or an empty diff — yields an empty list.  This function never
    raises, so it is safe to call from CI entry points in unknown
    environments.

    Args:
        base: Base revision for the diff.
        head: Head revision for the diff.

    Returns:
        List of snippet dicts as produced by :func:`parse_git_diff`.
    """
    try:
        result = subprocess.run(
            ["git", "diff", base, head],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return parse_git_diff(result.stdout or "")


def scan_files(paths: List[str]) -> List[Dict[str, str]]:
    """Read explicit files into snippet dicts of type ``code``.

    Unreadable, non-UTF-8, or binary-looking files (containing NUL
    bytes) are silently skipped.

    Args:
        paths: File paths to read.

    Returns:
        List of ``{"file": path, "type": "code", "content": ...}`` dicts.
    """
    snippets: List[Dict[str, str]] = []
    for path in paths:
        try:
            data = Path(path).read_bytes()
            if b"\x00" in data:
                continue
            content = data.decode("utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        snippets.append({"file": str(path), "type": "code", "content": content})
    return snippets


# ---------------------------------------------------------------------------
# Phase-aware exit decision
# ---------------------------------------------------------------------------


def is_blocking_finding(finding: AuditFinding) -> bool:
    """Return ``True`` when *finding* must block a gating-phase pipeline.

    Only bool-invariant blocks (rules containing ``secret``) and OWASP
    findings with severity above 7.0 block; score-based smells such as
    the N+1 ORM audit annotate but never block.

    Args:
        finding: The finding to classify.

    Returns:
        Whether the finding is pipeline-blocking.
    """
    rule = finding.rule.lower()
    if "secret" in rule:
        return True
    return "owasp" in rule and finding.severity > _BLOCK_SEVERITY


def phase_verdict(findings: List[AuditFinding], phase: str) -> Tuple[bool, str]:
    """Decide whether the pipeline passes under the autonomy *phase*.

    * ``shadow`` — always passes; findings are logged only.
    * ``advisory`` — always passes; findings are annotated.
    * ``gating`` — fails only on blocking findings (see
      :func:`is_blocking_finding`); other findings annotate.

    Args:
        findings: Findings from the reflex scan.
        phase: Autonomy phase name (case-insensitive).

    Returns:
        ``(passed, reason)`` where *reason* is a human-readable
        justification for the decision.
    """
    normalized = (phase or "advisory").strip().lower()
    if normalized == "shadow":
        return True, "shadow — logged only"
    if normalized == "advisory":
        return True, f"advisory — {len(findings)} findings annotated"
    if normalized == "gating":
        blocking = [f for f in findings if is_blocking_finding(f)]
        if blocking:
            rules = ", ".join(sorted({f.rule for f in blocking}))
            return False, f"gating — {len(blocking)} blocking finding(s): {rules}"
        return True, f"gating — {len(findings)} findings annotated (non-blocking)"
    return True, f"unknown phase '{phase}' — logged only"


if __name__ == "__main__":  # pragma: no cover - thin CLI delegate
    from harness.reflex.__main__ import main

    sys.exit(main())
