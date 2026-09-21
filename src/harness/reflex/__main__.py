"""Standalone CLI entry point for the reflex CI runner.

Usage::

    python3 -m harness.reflex [--diff BASE HEAD | --files PATH [PATH...]]
        [--phase {shadow,advisory,gating}] [--summary PATH]
        [--json] [--remediate]

The dogfood workflow (``.github/workflows/reflex_ci.yml``) invokes the
equivalent ``python3 -m harness.reflex.ci`` form, which delegates here.
"""

from __future__ import annotations

import argparse
import dataclasses
import glob
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence

from harness.reflex.backend import JevBackend, MockReflexBackend, ReflexBackend
from harness.reflex.ci import (
    AuditFinding,
    ReflexCIRunner,
    default_remediate,
    get_diff_snippets,
    is_blocking_finding,
    phase_verdict,
    scan_files,
)
from harness.reflex.linter import QualitativeLinter
from harness.reflex.primitives import ReflexPrimitives

__all__ = ["PHASES", "build_parser", "main"]

#: Supported autonomy phases (see REFLEX_PLAN.md §6).
PHASES = ("shadow", "advisory", "gating")

_DEFAULT_PHASE = "advisory"
_DOGFOOD_GLOB = os.path.join("src", "harness", "**", "*.py")
_DOGFOOD_CAP = 40
_DOGFOOD_MAX_BYTES = 50 * 1024


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the reflex CI entry point."""
    parser = argparse.ArgumentParser(
        prog="harness.reflex.ci",
        description=(
            "Reflex System 1 CI pre-screen: millisecond qualitative audit "
            "of git diffs or explicit files, with phased autonomy "
            "(shadow / advisory / gating)."
        ),
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--diff",
        nargs=2,
        metavar=("BASE", "HEAD"),
        help="scan `git diff BASE HEAD` (e.g. --diff HEAD~1 HEAD)",
    )
    source.add_argument(
        "--files",
        nargs="+",
        metavar="PATH",
        help="scan explicit files instead of a git diff",
    )
    parser.add_argument(
        "--phase",
        choices=PHASES,
        default=None,
        help=(
            "autonomy phase; defaults to the REFLEX_AUTONOMY_PHASE "
            "environment variable or 'advisory'"
        ),
    )
    parser.add_argument(
        "--summary",
        metavar="PATH",
        default=None,
        help="append a GitHub Step Summary markdown report to PATH",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON on stdout instead of a table",
    )
    parser.add_argument(
        "--remediate",
        action="store_true",
        help="apply default_remediate and re-verify patches (default: scan only)",
    )
    return parser


def _resolve_phase(cli_phase: Optional[str]) -> str:
    """Resolve the autonomy phase from the CLI flag or environment."""
    phase = cli_phase or os.environ.get("REFLEX_AUTONOMY_PHASE", _DEFAULT_PHASE)
    phase = phase.strip().lower()
    return phase if phase in PHASES else _DEFAULT_PHASE


def _default_backend() -> ReflexBackend:
    """Pick the reflex backend: Jev when an API key is present, else mock."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return JevBackend()  # falls back to the mock internally on failure
    return MockReflexBackend()


def _dogfood_snippets() -> List[Dict[str, str]]:
    """Scan ``src/harness/**/*.py`` relative to the cwd (dogfood default).

    Caps the scan at 40 files and skips files larger than 50 KB.
    """
    paths: List[str] = []
    for path in sorted(glob.glob(_DOGFOOD_GLOB, recursive=True)):
        try:
            if os.path.getsize(path) > _DOGFOOD_MAX_BYTES:
                continue
        except OSError:
            continue
        paths.append(path)
        if len(paths) >= _DOGFOOD_CAP:
            break
    return scan_files(paths)


def _collect_snippets(args: argparse.Namespace) -> List[Dict[str, str]]:
    """Source snippets from --diff, --files, or the dogfood default."""
    if args.diff:
        return get_diff_snippets(args.diff[0], args.diff[1])
    if args.files:
        return scan_files(list(args.files))
    return _dogfood_snippets()


def _finding_to_dict(finding: AuditFinding) -> Dict[str, Any]:
    """Serialise an :class:`AuditFinding` for JSON output."""
    data = dataclasses.asdict(finding)
    data["blocking"] = is_blocking_finding(finding)
    return data


def _summary_markdown(
    phase: str,
    findings: List[AuditFinding],
    passed: bool,
    reason: str,
    patches_applied: int,
    reverified: int,
) -> str:
    """Render a GitHub Step Summary markdown report."""
    lines = [
        "## Reflex System 1 CI Pre-Screen Report",
        "",
        f"- **Phase**: `{phase}`",
        f"- **Verdict**: {'PASSED' if passed else 'BLOCKED'} — {reason}",
        "",
        "| File | Rule | Severity | Escalate |",
        "| :--- | :--- | :--- | :--- |",
    ]
    if findings:
        for finding in findings:
            escalate = "yes" if is_blocking_finding(finding) else "no"
            lines.append(
                f"| `{finding.file}` | {finding.rule} | "
                f"{finding.severity} | {escalate} |"
            )
    else:
        lines.append("| — | — | — | All System 1 checks passed |")
    if patches_applied:
        lines.extend(
            [
                "",
                "### Auto-Remediation Summary",
                f"- **System 2 Patches Applied**: `{patches_applied}`",
                f"- **Re-verified Clean**: `{reverified}`",
            ]
        )
    return "\n".join(lines)


def _append_summary(path: str, markdown: str) -> None:
    """Append *markdown* to the summary file, warning (not failing) on error."""
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")
    except OSError as exc:
        print(f"reflex CI: could not write summary to {path}: {exc}", file=sys.stderr)


def _print_table(findings: List[AuditFinding]) -> None:
    """Print a compact findings table to stdout."""
    headers = ["rule", "file", "severity", "escalate"]
    rows = [
        [
            finding.rule,
            finding.file,
            f"{finding.severity:.2f}",
            "yes" if is_blocking_finding(finding) else "no",
        ]
        for finding in findings
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    header_line = " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    print(header_line)
    print("-" * len(header_line))
    for row in rows:
        print(" | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the reflex CI pipeline and return the process exit code.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]`` when ``None``).

    Returns:
        ``0`` when the phase verdict passes, ``1`` when gating blocks.
    """
    args = build_parser().parse_args(argv)
    phase = _resolve_phase(args.phase)

    runner = ReflexCIRunner(
        QualitativeLinter(ReflexPrimitives(_default_backend())),
        remediate=default_remediate,
    )

    snippets = _collect_snippets(args)
    if not snippets:
        if args.json:
            print(
                json.dumps(
                    {
                        "passed": True,
                        "phase": phase,
                        "findings": [],
                        "counts": {"snippets": 0, "findings": 0, "blocking": 0},
                    }
                )
            )
        else:
            print("Reflex CI: no snippets to scan — nothing flagged.")
        return 0

    patches_applied = 0
    reverified = 0
    if args.remediate:
        result = runner.run(snippets)
        findings = list(result["findings"])
        patches_applied = int(result["patches_applied"])
        reverified = int(result["reverified"])
    else:
        findings = runner.scan(snippets)

    passed, reason = phase_verdict(findings, phase)
    blocking = sum(1 for finding in findings if is_blocking_finding(finding))

    if args.summary:
        _append_summary(
            args.summary,
            _summary_markdown(phase, findings, passed, reason, patches_applied, reverified),
        )

    if args.json:
        print(
            json.dumps(
                {
                    "passed": passed,
                    "phase": phase,
                    "findings": [_finding_to_dict(f) for f in findings],
                    "counts": {
                        "snippets": len(snippets),
                        "findings": len(findings),
                        "blocking": blocking,
                        "patches_applied": patches_applied,
                        "reverified": reverified,
                    },
                }
            )
        )
    else:
        print(f"Reflex CI pre-screen — phase: {phase}, snippets: {len(snippets)}")
        if findings:
            _print_table(findings)
        else:
            print("All System 1 checks passed.")
        print(f"Verdict: {'PASSED' if passed else 'BLOCKED'} — {reason}")

    return 0 if passed else 1


if __name__ == "__main__":  # pragma: no cover - CLI glue
    sys.exit(main())
