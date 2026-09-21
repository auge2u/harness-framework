"""CLI for the Harness framework.

Commands::

    python -m harness.cli run <scenario> [--config path.yaml]
    python -m harness.cli evolve <scenario> [--config path.yaml] [--max-cycles N]
    python -m harness.cli variants <scenario> [--config path.yaml] [--count N] [--parallel P]
    python -m harness.cli validate <config.yaml> [--strict] [--json] [--no-color]

Legacy flag style is also supported::

    python -m harness.cli --harness-run <scenario>
    python -m harness.cli --harness-evolve <scenario>
    python -m harness.cli --harness-variants <scenario>
    python -m harness.cli --harness-validate <config.yaml>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Console helper
# ---------------------------------------------------------------------------


class Console:
    """Rich console output helper with TTY auto-detection and JSON mode."""

    _COLORS: Dict[str, str] = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "cyan": "\033[96m",
        "reset": "\033[0m",
    }

    def __init__(
        self, use_color: bool = True, json_mode: bool = False
    ) -> None:
        self.json_mode = json_mode
        self.use_color = (
            use_color and sys.stdout.isatty() and not json_mode
        )
        self._json_data: List[Dict[str, Any]] = []

    def color(self, text: str, color: str) -> str:
        if not self.use_color:
            return text
        c = self._COLORS.get(color, "")
        return f"{c}{text}{self._COLORS['reset']}"

    def section(self, title: str) -> None:
        if self.json_mode:
            return
        width = 60
        print(
            f"\n{'=' * width}\n{title.center(width)}\n{'=' * width}"
        )

    def table(self, headers: List[str], rows: List[List[Any]]) -> None:
        if self.json_mode:
            self._json_data.append(
                {"type": "table", "headers": headers, "rows": rows}
            )
            return
        if not rows:
            return
        widths = [len(str(h)) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], len(str(cell)))
        header_line = " | ".join(
            str(h).ljust(widths[i]) for i, h in enumerate(headers)
        )
        print(f"\n{header_line}")
        print("-" * len(header_line))
        for row in rows:
            print(
                " | ".join(
                    str(cell).ljust(widths[i])
                    for i, cell in enumerate(row)
                )
            )

    def progress(self, iterable, desc: str = "Processing"):
        if self.json_mode:
            yield from iterable
            return
        items = list(iterable)
        total = len(items)
        for i, item in enumerate(items):
            pct = (i + 1) / total * 100 if total else 0
            print(
                f"\r{desc}: [{i + 1}/{total}] {pct:.0f}%",
                end="",
                flush=True,
            )
            yield item
        print()

    def success(self, msg: str) -> None:
        if self.json_mode:
            self._json_data.append(
                {"type": "success", "message": msg}
            )
            return
        print(
            self.color(f"✓ {msg}", "green")
            if self.use_color
            else f"[OK] {msg}"
        )

    def error(self, msg: str) -> None:
        if self.json_mode:
            self._json_data.append(
                {"type": "error", "message": msg}
            )
            return
        print(
            self.color(f"✗ {msg}", "red")
            if self.use_color
            else f"[ERR] {msg}"
        )

    def warning(self, msg: str) -> None:
        if self.json_mode:
            self._json_data.append(
                {"type": "warning", "message": msg}
            )
            return
        print(
            self.color(f"⚠ {msg}", "yellow")
            if self.use_color
            else f"[WARN] {msg}"
        )

    def info(self, msg: str) -> None:
        if self.json_mode:
            self._json_data.append(
                {"type": "info", "message": msg}
            )
            return
        print(
            self.color(f"ℹ {msg}", "blue")
            if self.use_color
            else f"[INFO] {msg}"
        )

    def emit_json(self) -> None:
        if self.json_mode:
            print(
                json.dumps(
                    self._json_data, indent=2, default=str
                )
            )


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    """Run a scenario through the harness.

    Loads the configuration, sets up the plugin registry with built-in
    verifiers, and executes the scenario using :class:`SingleRunner`.

    Returns
    -------
    int
        Exit code — ``0`` on success.
    """
    from harness.core.config import HarnessConfig
    from harness.core.registry import PluginRegistry
    from harness.runners.runner import SingleRunner
    from harness.scenarios.loader import ScenarioLoader
    from harness.verifiers.builtin import ExactVerifier, FuzzyVerifier

    console = Console(
        use_color=not args.no_color, json_mode=args.json
    )

    console.section("Harness Run")
    console.info(f"scenario={args.scenario}")

    # Load config
    if args.config and Path(args.config).exists():
        config = HarnessConfig.from_yaml(args.config)
    else:
        config = HarnessConfig(
            version="0.2.0",
            name="cli_run",
            surfaces=[],
            scenarios={},
            verifiers=[],
        )

    # Validate config
    validation_errors = config.validate()
    if validation_errors:
        console.warning(
            f"Config validation warnings: {validation_errors}"
        )

    # Setup registry with built-in verifiers
    registry = PluginRegistry()
    registry.register_verifier("exact", ExactVerifier)
    registry.register_verifier("fuzzy", FuzzyVerifier)

    # Load scenario
    loader = ScenarioLoader()
    try:
        scenario = loader.load(args.scenario)
        scenarios = [scenario]
    except Exception as exc:
        console.warning(
            f"Could not load scenario '{args.scenario}': {exc}"
        )
        scenarios = []

    # Config info table
    console.table(
        ["Property", "Value"],
        [
            ["Name", config.name],
            ["Version", config.version],
            ["Surfaces", len(config.surfaces)],
            ["Scenarios", len(config.scenarios)],
            ["Verifiers", len(config.verifiers)],
        ],
    )

    if scenarios:
        console.table(
            ["Scenario", "ID", "Surfaces"],
            [
                [
                    getattr(s, "name", str(s)),
                    getattr(s, "scenario_id", "-"),
                    getattr(s, "surfaces", []),
                ]
                for s in scenarios
            ],
        )

    # Run
    runner = SingleRunner(version_tag=config.version)

    if scenarios:
        result = runner.run(config, scenarios, registry)
        agg_score = result.get("aggregate_score", 0.0)
        duration = result.get("duration_ms", 0.0)
        cost = result.get("cost_usd", 0.0)
        verdicts = result.get("verdict_counts", {})

        console.section("Results")
        console.table(
            ["Metric", "Value"],
            [
                ["Aggregate score", f"{agg_score:.4f}"],
                ["Duration", f"{duration:.1f} ms"],
                ["Cost", f"${cost:.4f}"],
            ],
        )

        # Verdict summary
        verdict_rows = []
        for verdict, count in verdicts.items():
            verdict_rows.append([str(verdict), count])
        if verdict_rows:
            console.table(["Verdict", "Count"], verdict_rows)

        if verdicts.get("FAIL", 0) > 0:
            console.error(
                f"{verdicts.get('FAIL', 0)} scenario(s) failed"
            )
            console.emit_json()
            return 1
        if verdicts.get("PASS", 0) == len(scenarios):
            console.success("All scenarios passed")
        else:
            console.warning(
                "Some scenarios had non-pass verdicts"
            )

        if args.output:
            output_dir = Path(args.output)
            output_dir.mkdir(parents=True, exist_ok=True)
            console.info(f"Artifacts written to: {output_dir}")

    console.success("Run complete.")
    console.emit_json()
    return 0


# ---------------------------------------------------------------------------
# cmd_evolve
# ---------------------------------------------------------------------------


def cmd_evolve(args: argparse.Namespace) -> int:
    """Run the self-harness evolution loop.

    Iteratively proposes, evaluates, and decides on configuration
    improvements until *max_cycles* is reached or convergence.

    Returns
    -------
    int
        Exit code — ``0`` on success.
    """
    from harness.analysis.clusterer import FailureClusterer
    from harness.analysis.lineage import HarnessLineage
    from harness.core.config import HarnessConfig
    from harness.core.registry import PluginRegistry
    from harness.loops.self_harness import SelfHarnessLoop
    from harness.scenarios.loader import ScenarioLoader
    from harness.store.trace_store import TraceStore
    from harness.verifiers.builtin import ExactVerifier, FuzzyVerifier

    console = Console(
        use_color=not args.no_color, json_mode=args.json
    )

    console.section("Harness Evolution")
    console.info(
        f"scenario={args.scenario}, max_cycles={args.max_cycles}"
    )

    # Load config
    if args.config and Path(args.config).exists():
        config = HarnessConfig.from_yaml(args.config)
    else:
        config = HarnessConfig(
            version="0.2.0",
            name="cli_evolve",
            surfaces=[],
            scenarios={},
            verifiers=[],
        )

    validation_errors = config.validate()
    if validation_errors:
        console.warning(
            f"Config validation warnings: {validation_errors}"
        )

    # Setup registry
    registry = PluginRegistry()
    registry.register_verifier("exact", ExactVerifier)
    registry.register_verifier("fuzzy", FuzzyVerifier)

    # Setup supporting infrastructure
    trace_store = TraceStore()
    lineage = HarnessLineage()
    clusterer = FailureClusterer()

    # Load scenarios if available
    loader = ScenarioLoader()
    held_in_scenarios = []
    try:
        scenario = loader.load(args.scenario)
        held_in_scenarios = [scenario]
    except Exception as exc:
        console.warning(
            f"Could not load scenario '{args.scenario}': {exc}"
        )

    # Run the evolution loop
    loop = SelfHarnessLoop(
        config, registry, trace_store, lineage, clusterer
    )
    results = loop.run(
        max_cycles=args.max_cycles,
        held_in_scenarios=held_in_scenarios
        if held_in_scenarios
        else None,
    )

    console.info(f"Evolution complete: {len(results)} cycles")

    accepted = 0
    rejected = 0
    review = 0
    cycle_rows: List[List[Any]] = []
    for i, (proposal, decision, metrics) in enumerate(
        console.progress(results, desc="Cycles")
    ):
        score = metrics.get("held_in_score", 0.0)
        risk = metrics.get("risk_assessment", "unknown")
        gates = metrics.get("gates_passed", "-")
        cycle_rows.append(
            [f"Cycle {i + 1}", decision, f"{score:.4f}", gates]
        )
        if decision == "accept":
            accepted += 1
        elif decision == "reject":
            rejected += 1
        else:
            review += 1

    if cycle_rows:
        console.table(
            ["Cycle", "Decision", "Score", "Gates"], cycle_rows
        )

    console.section("Final Summary")
    console.table(
        ["Metric", "Value"],
        [
            ["Total cycles", len(results)],
            ["Accepted", accepted],
            ["Rejected", rejected],
            ["Review", review],
        ],
    )

    if accepted > rejected:
        console.success(f"{accepted} proposals accepted")
    elif rejected > accepted:
        console.warning(f"{rejected} proposals rejected")
    else:
        console.info("Mixed results — review recommended")

    console.emit_json()
    return 0


# ---------------------------------------------------------------------------
# cmd_variants
# ---------------------------------------------------------------------------


def cmd_variants(args: argparse.Namespace) -> int:
    """Run multiple harness variants in parallel.

    Uses :class:`CircuitRunner` to evaluate *count* configuration variants
    with bounded parallelism.

    Returns
    -------
    int
        Exit code — ``0`` on success.
    """
    from harness.core.config import HarnessConfig
    from harness.core.registry import PluginRegistry
    from harness.core.types import HarnessProposal
    from harness.runners.circuit_runner import CircuitRunner
    from harness.scenarios.loader import ScenarioLoader
    from harness.scenarios.split import ScenarioSplit
    from harness.store.trace_store import TraceStore
    from harness.verifiers.builtin import ExactVerifier

    console = Console(
        use_color=not args.no_color, json_mode=args.json
    )

    console.section("Harness Variants")
    console.info(
        f"scenario={args.scenario}, count={args.count}, "
        f"parallel={args.parallel}"
    )

    # Load config
    if args.config and Path(args.config).exists():
        config = HarnessConfig.from_yaml(args.config)
    else:
        config = HarnessConfig(
            version="0.2.0",
            name="cli_variants",
            surfaces=[],
            scenarios={},
            verifiers=[],
        )

    validation_errors = config.validate()
    if validation_errors:
        console.warning(
            f"Config validation warnings: {validation_errors}"
        )

    # Setup registry
    registry = PluginRegistry()
    registry.register_verifier("exact", ExactVerifier)
    trace_store = TraceStore()

    # Load scenario and create split
    loader = ScenarioLoader()
    loaded_scenarios: List[Any] = []
    try:
        scenario = loader.load(args.scenario)
        loaded_scenarios = [scenario]
    except Exception as exc:
        console.warning(
            f"Could not load scenario '{args.scenario}': {exc}"
        )
    scenarios = ScenarioSplit(
        loaded_scenarios, held_out_ratio=0.0
    )

    # Build proposals — generate N variants by slightly modifying config
    proposals: List[HarnessProposal] = []
    for i in range(args.count):
        proposals.append(
            HarnessProposal(
                proposal_id=f"variant-{i + 1}",
                parent_version=config.version,
                rationale=f"CLI variant {i + 1} of {args.count}",
                changes={
                    "_scopes": [
                        {
                            "scope": "diagnostic",
                            "name": f"variant_{i + 1}",
                        }
                    ]
                },
                estimated_impact={
                    "variant": i + 1,
                    "parallelism": args.parallel,
                },
            )
        )

    # Run variants
    runner = CircuitRunner(
        registry, trace_store, max_parallel=args.parallel
    )

    if scenarios.held_in:
        try:
            results = runner.run_variants(
                config,
                proposals,
                scenarios,
                run_held_out_on_best=False,
            )
            console.info(f"Completed {len(results)} variants")

            variant_rows: List[List[Any]] = []
            for r in results:
                variant_rows.append(
                    [
                        r.variant_id,
                        f"{r.aggregate_score:.4f}",
                        f"${r.cost_usd:.4f}",
                        f"{r.duration_ms:.1f}ms",
                    ]
                )
            if variant_rows:
                console.table(
                    ["Variant", "Score", "Cost", "Duration"],
                    variant_rows,
                )

            # Compare and rank
            comparison = runner.compare(results)
            best_id = comparison.get("best_variant_id")
            if best_id:
                console.success(f"Best variant: {best_id}")
        except Exception as exc:
            console.error(f"Error running variants: {exc}")
    else:
        console.warning(
            "No scenarios loaded — running in dry-run mode."
        )
        console.info(
            f"Would run {args.count} variants with "
            f"parallelism={args.parallel}"
        )

    console.success("Variants complete.")
    console.emit_json()
    return 0


# ---------------------------------------------------------------------------
# cmd_validate
# ---------------------------------------------------------------------------


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a harness configuration file.

    Loads the config with :meth:`HarnessConfig.from_yaml`, runs
    :meth:`HarnessConfig.validate_detailed`, and prints the structured
    errors/warnings using the standard console style.

    Returns
    -------
    int
        Exit code — ``0`` if valid (warnings allowed unless ``--strict``),
        ``1`` if validation errors are present (or warnings with
        ``--strict``), ``2`` if the file cannot be loaded or parsed.
    """
    from harness.core.config import HarnessConfig

    strict = getattr(args, "strict", False)
    console = Console(
        use_color=not args.no_color, json_mode=args.json
    )

    # Load config — missing file or unparseable YAML => exit 2
    try:
        config = HarnessConfig.from_yaml(args.config)
    except Exception as exc:
        if args.json:
            print(
                json.dumps(
                    {
                        "valid": False,
                        "errors": [str(exc)],
                        "warnings": [],
                        "strict": strict,
                        "config": None,
                    },
                    indent=2,
                    default=str,
                )
            )
        else:
            console.error(f"Cannot load config: {exc}")
        return 2

    result = config.validate_detailed()

    if args.json:
        # Machine-readable output only — no human text in JSON mode.
        print(
            json.dumps(
                {
                    "valid": result["valid"],
                    "errors": result["errors"],
                    "warnings": result["warnings"],
                    "strict": strict,
                    "config": {
                        "name": config.name,
                        "version": config.version,
                    },
                },
                indent=2,
                default=str,
            )
        )
    else:
        if result["errors"]:
            console.section("Errors")
            for err in result["errors"]:
                console.error(err)

        if result["warnings"]:
            console.section("Warnings")
            for warn in result["warnings"]:
                console.warning(warn)

        console.section("Config Summary")
        console.table(
            ["Property", "Value"],
            [
                ["Version", config.version],
                ["Name", config.name],
                ["Surfaces", result["surface_count"]],
                ["Verifiers", result["verifier_count"]],
                ["Scenarios", result["scenario_count"]],
                ["Held-out ratio", config.held_out_ratio],
                ["Cost budget", f"${config.cost_budget_usd}"],
            ],
        )

        if result["risk_assessment"]:
            console.section("Risk Assessment")
            risk_rows = [
                [name, score]
                for name, score in result["risk_assessment"].items()
            ]
            console.table(["Surface", "Risk Score"], risk_rows)

        if result["valid"]:
            console.success(
                f"Config valid: {config.name} v{config.version} — "
                f"{result['surface_count']} surfaces, "
                f"{result['verifier_count']} verifiers"
            )
            if strict and result["warnings"]:
                console.error(
                    "--strict: warnings treated as failures"
                )
        else:
            console.error(
                f"Config invalid: {len(result['errors'])} "
                "error(s) found"
            )

    failed = bool(result["errors"]) or (
        strict and bool(result["warnings"])
    )
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the Harness CLI.

    Supports both subcommand-style and legacy flag-style invocations.
    """
    parser = argparse.ArgumentParser(
        prog="harness",
        description="Harness Framework — Reflective Agent Runtime",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s run my_scenario --config harness.yaml\n"
            "  %(prog)s evolve my_scenario --max-cycles 20\n"
            "  %(prog)s variants my_scenario --count 5 --parallel 8\n"
            "  %(prog)s validate harness.yaml\n"
            "  %(prog)s --harness-run my_scenario\n"
        ),
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s 0.2.0"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output structured JSON instead of human-readable text",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        help="Disable ANSI colors in output",
    )

    subparsers = parser.add_subparsers(
        dest="command", help="Available commands"
    )

    # --- run ---
    run_parser = subparsers.add_parser(
        "run", help="Run a scenario through the harness"
    )
    run_parser.add_argument(
        "scenario", help="Scenario name or path"
    )
    run_parser.add_argument(
        "--config", "-c", help="Path to harness config YAML"
    )
    run_parser.add_argument(
        "--output", "-o", help="Output directory for artifacts"
    )

    # --- evolve ---
    evolve_parser = subparsers.add_parser(
        "evolve", help="Run self-harness evolution loop"
    )
    evolve_parser.add_argument(
        "scenario", help="Scenario name or path"
    )
    evolve_parser.add_argument(
        "--config", "-c", help="Path to harness config YAML"
    )
    evolve_parser.add_argument(
        "--max-cycles",
        type=int,
        default=10,
        help="Maximum evolution cycles (default: 10)",
    )

    # --- variants ---
    variants_parser = subparsers.add_parser(
        "variants", help="Run multiple harness variants in parallel"
    )
    variants_parser.add_argument(
        "scenario", help="Scenario name or path"
    )
    variants_parser.add_argument(
        "--config", "-c", help="Path to harness config YAML"
    )
    variants_parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="Number of variants to run (default: 3)",
    )
    variants_parser.add_argument(
        "--parallel",
        type=int,
        default=4,
        help="Maximum parallel variants (default: 4)",
    )

    # --- validate ---
    validate_parser = subparsers.add_parser(
        "validate", help="Validate a harness configuration file"
    )
    validate_parser.add_argument(
        "config", help="Path to harness config YAML"
    )
    # NOTE: defaults are SUPPRESS so that top-level --json/--no-color
    # (parsed before the subcommand) are not clobbered by subparser
    # defaults; cmd_validate falls back to False via getattr.
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Treat warnings as failures (exit 1 when warnings present)",
    )
    validate_parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Output structured JSON instead of human-readable text",
    )
    validate_parser.add_argument(
        "--no-color",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Disable ANSI colors in output",
    )

    # Legacy flag-style arguments (also supported)
    parser.add_argument(
        "--harness-run",
        dest="run_scenario",
        help="(Legacy) Run a scenario",
    )
    parser.add_argument(
        "--harness-evolve",
        dest="evolve_scenario",
        help="(Legacy) Evolve a scenario",
    )
    parser.add_argument(
        "--harness-variants",
        dest="variants_scenario",
        help="(Legacy) Run variants",
    )
    parser.add_argument(
        "--harness-validate",
        dest="validate_config",
        help="(Legacy) Validate a harness config file",
    )
    # These must be added at the top level for legacy compat but only apply
    # when the legacy flags are used.
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=10,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=4,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )

    return parser


# ---------------------------------------------------------------------------
# main entrypoint
# ---------------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point for the Harness CLI.

    Parameters
    ----------
    argv:
        Command-line arguments.  If ``None``, ``sys.argv[1:]`` is used.

    Returns
    -------
    int
        Process exit code — ``0`` for success, ``1`` for errors or no command.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv else None)

    # Handle legacy --harness-* flags by mapping them to subcommands.
    if getattr(args, "run_scenario", None):
        args.scenario = args.run_scenario
        args.command = "run"
        args.config = getattr(args, "config", None)
        args.output = getattr(args, "output", None)
    elif getattr(args, "evolve_scenario", None):
        args.scenario = args.evolve_scenario
        args.command = "evolve"
        args.config = getattr(args, "config", None)
        args.max_cycles = getattr(args, "max_cycles", 10)
    elif getattr(args, "variants_scenario", None):
        args.scenario = args.variants_scenario
        args.command = "variants"
        args.config = getattr(args, "config", None)
        args.count = 3
        args.parallel = getattr(args, "parallel", 4)
    elif getattr(args, "validate_config", None):
        args.config = args.validate_config
        args.command = "validate"
        args.strict = getattr(args, "strict", False)

    if not args.command:
        parser.print_help()
        return 1

    commands = {
        "run": cmd_run,
        "evolve": cmd_evolve,
        "variants": cmd_variants,
        "validate": cmd_validate,
    }

    handler = commands.get(args.command)
    if handler is None:
        console = Console()
        console.error(f"Unknown command: {args.command}")
        parser.print_help()
        return 1

    # Ensure all commands have json and no_color attrs
    if not hasattr(args, "json"):
        args.json = False
    if not hasattr(args, "no_color"):
        args.no_color = False

    try:
        return handler(args)
    except Exception as exc:
        console = Console()
        console.error(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
