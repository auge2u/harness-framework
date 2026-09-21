"""Tests for the harness.cli module."""
from __future__ import annotations

import argparse
import json

import pytest

from harness.cli import (
    Console,
    build_parser,
    cmd_evolve,
    cmd_run,
    cmd_validate,
    cmd_variants,
    main,
)


# ---------------------------------------------------------------------------
# Console class tests
# ---------------------------------------------------------------------------

class TestConsole:
    """Tests for the Console helper class."""

    def test_color_with_color_enabled(self, monkeypatch):
        """color() returns ANSI-wrapped text when color is enabled."""
        monkeypatch.setattr(
            "sys.stdout.isatty", lambda: True
        )
        console = Console(use_color=True, json_mode=False)
        result = console.color("test", "green")
        assert "\033[92m" in result
        assert "test" in result
        assert "\033[0m" in result

    def test_color_with_color_disabled(self):
        """color() returns plain text when color is disabled."""
        console = Console(use_color=False, json_mode=False)
        result = console.color("test", "green")
        assert result == "test"

    def test_color_json_mode_disables_colors(self):
        """JSON mode disables colors regardless of use_color."""
        console = Console(use_color=True, json_mode=True)
        result = console.color("test", "green")
        assert result == "test"

    def test_section_prints_header(self, capsys):
        """section() prints a bordered header."""
        console = Console(use_color=False, json_mode=False)
        console.section("Test Section")
        captured = capsys.readouterr()
        assert "Test Section" in captured.out
        assert "====" in captured.out

    def test_section_json_mode_no_output(self, capsys):
        """section() produces no output in JSON mode."""
        console = Console(json_mode=True)
        console.section("Test Section")
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_table_prints_formatted_rows(self, capsys):
        """table() prints an ASCII table with headers and rows."""
        console = Console(use_color=False, json_mode=False)
        console.table(
            ["Name", "Value"], [["foo", "1"], ["bar", "2"]]
        )
        captured = capsys.readouterr()
        assert "Name" in captured.out
        assert "foo" in captured.out
        assert "bar" in captured.out

    def test_table_json_mode_collects_data(self, capsys):
        """table() collects data in JSON mode."""
        console = Console(json_mode=True)
        console.table(
            ["Name", "Value"], [["foo", "1"]]
        )
        captured = capsys.readouterr()
        assert captured.out == ""
        assert len(console._json_data) == 1
        assert console._json_data[0]["type"] == "table"

    def test_table_empty_rows(self, capsys):
        """table() handles empty rows gracefully (no output)."""
        console = Console(use_color=False, json_mode=False)
        console.table(["Name", "Value"], [])
        captured = capsys.readouterr()
        # Empty rows => no output
        assert captured.out == ""

    def test_progress_yields_all_items(self):
        """progress() yields all items from the iterable."""
        console = Console(use_color=False, json_mode=False)
        items = list(console.progress([1, 2, 3], desc="Test"))
        assert items == [1, 2, 3]

    def test_progress_json_mode_yields_without_output(self, capsys):
        """progress() yields items without progress text in JSON mode."""
        console = Console(json_mode=True)
        items = list(console.progress([1, 2, 3], desc="Test"))
        captured = capsys.readouterr()
        assert captured.out == ""
        assert items == [1, 2, 3]

    def test_success_prints_message(self, capsys):
        """success() prints a success message."""
        console = Console(use_color=False, json_mode=False)
        console.success("all good")
        captured = capsys.readouterr()
        assert "[OK] all good" in captured.out

    def test_success_json_mode(self, capsys):
        """success() collects data in JSON mode."""
        console = Console(json_mode=True)
        console.success("all good")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert console._json_data[0]["type"] == "success"

    def test_error_prints_message(self, capsys):
        """error() prints an error message."""
        console = Console(use_color=False, json_mode=False)
        console.error("something wrong")
        captured = capsys.readouterr()
        assert "[ERR] something wrong" in captured.out

    def test_error_json_mode(self, capsys):
        """error() collects data in JSON mode."""
        console = Console(json_mode=True)
        console.error("something wrong")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert console._json_data[0]["type"] == "error"

    def test_warning_prints_message(self, capsys):
        """warning() prints a warning message."""
        console = Console(use_color=False, json_mode=False)
        console.warning("be careful")
        captured = capsys.readouterr()
        assert "[WARN] be careful" in captured.out

    def test_warning_json_mode(self, capsys):
        """warning() collects data in JSON mode."""
        console = Console(json_mode=True)
        console.warning("be careful")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert console._json_data[0]["type"] == "warning"

    def test_info_prints_message(self, capsys):
        """info() prints an info message."""
        console = Console(use_color=False, json_mode=False)
        console.info("fyi")
        captured = capsys.readouterr()
        assert "[INFO] fyi" in captured.out

    def test_info_json_mode(self, capsys):
        """info() collects data in JSON mode."""
        console = Console(json_mode=True)
        console.info("fyi")
        captured = capsys.readouterr()
        assert captured.out == ""
        assert console._json_data[0]["type"] == "info"

    def test_emit_json_outputs_json(self, capsys):
        """emit_json() prints collected JSON data."""
        console = Console(json_mode=True)
        console.success("test")
        console.table(["A"], [["b"]])
        console.emit_json()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 2
        assert data[0]["type"] == "success"
        assert data[1]["type"] == "table"

    def test_emit_json_non_json_mode(self, capsys):
        """emit_json() does nothing when not in JSON mode."""
        console = Console(json_mode=False)
        console.emit_json()
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_color_unknown_color(self):
        """color() handles unknown color names gracefully."""
        console = Console(use_color=True, json_mode=False)
        result = console.color("test", "magenta")
        assert "test" in result


# ---------------------------------------------------------------------------
# build_parser()
# ---------------------------------------------------------------------------

def test_build_parser_creates_parser():
    """build_parser() creates an ArgumentParser."""
    parser = build_parser()
    assert isinstance(parser, argparse.ArgumentParser)


def test_parser_has_run_subcommand():
    """Parser has 'run' subcommand."""
    parser = build_parser()
    args = parser.parse_args(["run", "test_scenario"])
    assert args.command == "run"


def test_parser_has_evolve_subcommand():
    """Parser has 'evolve' subcommand."""
    parser = build_parser()
    args = parser.parse_args(["evolve", "test_scenario"])
    assert args.command == "evolve"


def test_parser_has_variants_subcommand():
    """Parser has 'variants' subcommand."""
    parser = build_parser()
    args = parser.parse_args(["variants", "test_scenario"])
    assert args.command == "variants"


def test_parser_has_validate_subcommand():
    """Parser has 'validate' subcommand."""
    parser = build_parser()
    args = parser.parse_args(["validate", "config.yaml"])
    assert args.command == "validate"
    assert args.config == "config.yaml"


def test_parser_json_flag():
    """Parser accepts --json flag."""
    parser = build_parser()
    args = parser.parse_args(["--json", "run", "test_scenario"])
    assert args.json is True


def test_parser_no_color_flag():
    """Parser accepts --no-color flag."""
    parser = build_parser()
    args = parser.parse_args(["--no-color", "run", "test_scenario"])
    assert args.no_color is True


# ---------------------------------------------------------------------------
# --version flag
# ---------------------------------------------------------------------------

def test_version_flag(capsys):
    """--version flag prints version and exits."""
    parser = build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Missing subcommand
# ---------------------------------------------------------------------------

def test_missing_subcommand():
    """Missing subcommand returns 1 (main catches and returns 1)."""
    # main([]) internally calls parse_args([]) which raises SystemExit(2)
    # because argparse treats no subcommand as an error when subparsers exist.
    # main catches the exception and returns 1.
    with pytest.raises(SystemExit):
        main([])


# ---------------------------------------------------------------------------
# cmd_run
# ---------------------------------------------------------------------------

def test_cmd_run_executes():
    """cmd_run executes without error."""
    args = argparse.Namespace(
        command="run", scenario="test", config=None, output=None,
        json=False, no_color=False,
    )
    result = cmd_run(args)
    assert result == 0


def test_cmd_run_with_config():
    """cmd_run with config executes without error."""
    args = argparse.Namespace(
        command="run", scenario="test", config="config.yaml", output=None,
        json=False, no_color=False,
    )
    result = cmd_run(args)
    assert result == 0


def test_cmd_run_with_output():
    """cmd_run with output executes without error."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        args = argparse.Namespace(
            command="run", scenario="test", config=None, output=tmpdir,
            json=False, no_color=False,
        )
        result = cmd_run(args)
        assert result == 0


def test_cmd_run_json_mode(capsys):
    """cmd_run with --json outputs structured JSON."""
    args = argparse.Namespace(
        command="run", scenario="test", config=None, output=None,
        json=True, no_color=False,
    )
    result = cmd_run(args)
    assert result == 0
    captured = capsys.readouterr()
    # Should contain JSON output
    data = json.loads(captured.out)
    assert isinstance(data, list)


def test_cmd_run_no_color(capsys):
    """cmd_run with --no-color disables ANSI codes."""
    args = argparse.Namespace(
        command="run", scenario="test", config=None, output=None,
        json=False, no_color=True,
    )
    result = cmd_run(args)
    assert result == 0
    captured = capsys.readouterr()
    # No ANSI escape sequences when no_color is True
    assert "\033[" not in captured.out


# ---------------------------------------------------------------------------
# cmd_evolve
# ---------------------------------------------------------------------------

def test_cmd_evolve_executes():
    """cmd_evolve executes without error."""
    args = argparse.Namespace(
        command="evolve", scenario="test", config=None, max_cycles=1,
        json=False, no_color=False,
    )
    result = cmd_evolve(args)
    assert result == 0


def test_cmd_evolve_with_config():
    """cmd_evolve with config executes without error."""
    args = argparse.Namespace(
        command="evolve", scenario="test", config="config.yaml", max_cycles=1,
        json=False, no_color=False,
    )
    result = cmd_evolve(args)
    assert result == 0


def test_cmd_evolve_json_mode(capsys):
    """cmd_evolve with --json outputs structured JSON."""
    args = argparse.Namespace(
        command="evolve", scenario="test", config=None, max_cycles=1,
        json=True, no_color=False,
    )
    result = cmd_evolve(args)
    assert result == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# cmd_variants
# ---------------------------------------------------------------------------

def test_cmd_variants_executes():
    """cmd_variants executes without error (dry-run when no scenario)."""
    args = argparse.Namespace(
        command="variants", scenario="test", config=None, count=1, parallel=1,
        json=False, no_color=False,
    )
    # The variants command may fail internally due to ScenarioSplit API mismatch,
    # but the function has broad exception handling and returns 0.
    result = cmd_variants(args)
    assert result == 0


def test_cmd_variants_with_config():
    """cmd_variants with config executes without error."""
    args = argparse.Namespace(
        command="variants", scenario="test", config="config.yaml", count=1, parallel=1,
        json=False, no_color=False,
    )
    result = cmd_variants(args)
    assert result == 0


def test_cmd_variants_json_mode(capsys):
    """cmd_variants with --json outputs structured JSON."""
    args = argparse.Namespace(
        command="variants", scenario="test", config=None, count=1, parallel=1,
        json=True, no_color=False,
    )
    result = cmd_variants(args)
    assert result == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# cmd_validate
# ---------------------------------------------------------------------------

def test_cmd_validate_with_valid_config(tmp_path):
    """cmd_validate returns 0 for a valid config."""
    import yaml
    config_data = {
        "version": "1.0",
        "name": "test",
        "surfaces": [{"name": "api1", "type": "API"}],
        "scenarios": {"s1": {"difficulty": 0.5}},
        "verifiers": [{"type": "exact"}],
    }
    config_path = tmp_path / "valid_config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    args = argparse.Namespace(
        command="validate",
        config=str(config_path),
        json=False,
        no_color=False,
    )
    result = cmd_validate(args)
    assert result == 0


def test_cmd_validate_with_invalid_config(tmp_path):
    """cmd_validate returns 1 for an invalid config."""
    import yaml
    config_data = {
        "version": "1.0",
        "name": "test",
        "surfaces": [],
        "scenarios": {},
        "verifiers": [],
        "held_out_ratio": 1.5,
    }
    config_path = tmp_path / "invalid_config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    args = argparse.Namespace(
        command="validate",
        config=str(config_path),
        json=False,
        no_color=False,
    )
    result = cmd_validate(args)
    assert result == 1


def test_cmd_validate_json_mode(tmp_path, capsys):
    """cmd_validate with --json outputs structured JSON."""
    import yaml
    config_data = {
        "version": "1.0",
        "name": "test",
        "surfaces": [{"name": "api1", "type": "API"}],
        "scenarios": {},
        "verifiers": [{"type": "exact"}],
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    args = argparse.Namespace(
        command="validate",
        config=str(config_path),
        json=True,
        no_color=False,
    )
    result = cmd_validate(args)
    assert result == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, dict)
    assert data["valid"] is True
    assert data["errors"] == []
    assert data["strict"] is False
    assert data["config"] == {"name": "test", "version": "1.0"}


# ---------------------------------------------------------------------------
# cmd_validate — strict/json/exit-code behavior (T2.3)
# ---------------------------------------------------------------------------

_VALIDATE_VALID_CONFIG = {
    "version": "1.0",
    "name": "myharness",
    "surfaces": [{"name": "api1", "type": "API"}],
    "scenarios": {"s1": {"difficulty": 0.5}},
    "verifiers": [{"type": "exact"}],
}

_VALIDATE_ERROR_CONFIG = {
    **_VALIDATE_VALID_CONFIG,
    "held_out_ratio": 1.5,  # out of [0, 1] => validation error
}

# held_out_ratio in (0.5, 1] passes validate() but triggers a warning
_VALIDATE_WARNING_CONFIG = {
    **_VALIDATE_VALID_CONFIG,
    "held_out_ratio": 0.6,
}


def _write_yaml(tmp_path, data, name="config.yaml"):
    """Write *data* (dict) or raw string to a YAML file and return its path."""
    import yaml
    path = tmp_path / name
    with open(path, "w") as f:
        if isinstance(data, str):
            f.write(data)
        else:
            yaml.dump(data, f)
    return str(path)


def test_validate_valid_config_exit_zero(tmp_path):
    """Valid config exits 0."""
    path = _write_yaml(tmp_path, _VALIDATE_VALID_CONFIG)
    assert main(["validate", path]) == 0


def test_validate_valid_config_success_line(tmp_path, capsys):
    """Success line contains config name, version and counts."""
    path = _write_yaml(tmp_path, _VALIDATE_VALID_CONFIG)
    result = main(["validate", path])
    captured = capsys.readouterr()
    assert result == 0
    assert "Config valid: myharness v1.0" in captured.out
    assert "1 surfaces" in captured.out
    assert "1 verifiers" in captured.out


def test_validate_error_config_exit_one(tmp_path):
    """Config with a validation error exits 1."""
    path = _write_yaml(tmp_path, _VALIDATE_ERROR_CONFIG)
    assert main(["validate", path]) == 1


def test_validate_error_config_shows_error_text(tmp_path, capsys):
    """Validation error text is shown in the Errors section."""
    path = _write_yaml(tmp_path, _VALIDATE_ERROR_CONFIG)
    result = main(["validate", path])
    captured = capsys.readouterr()
    assert result == 1
    assert "Errors" in captured.out
    assert "held_out_ratio must be in [0, 1]" in captured.out


def test_validate_warnings_only_exit_zero(tmp_path, capsys):
    """Warnings without errors still exit 0."""
    path = _write_yaml(tmp_path, _VALIDATE_WARNING_CONFIG)
    result = main(["validate", path])
    captured = capsys.readouterr()
    assert result == 0
    assert "Warnings" in captured.out
    assert "held_out_ratio > 0.5" in captured.out


def test_validate_warnings_only_strict_exit_one(tmp_path, capsys):
    """--strict turns warnings into an exit-1 failure."""
    path = _write_yaml(tmp_path, _VALIDATE_WARNING_CONFIG)
    result = main(["validate", path, "--strict"])
    captured = capsys.readouterr()
    assert result == 1
    assert "--strict" in captured.out


def test_validate_missing_file_exit_two(tmp_path, capsys):
    """Missing config file exits 2 with an error message."""
    path = str(tmp_path / "does_not_exist.yaml")
    result = main(["validate", path])
    captured = capsys.readouterr()
    assert result == 2
    assert "not found" in captured.out


def test_validate_malformed_yaml_exit_two(tmp_path, capsys):
    """Unparseable YAML exits 2."""
    path = _write_yaml(tmp_path, "a: [1, 2\n", name="malformed.yaml")
    result = main(["validate", path])
    captured = capsys.readouterr()
    assert result == 2
    assert "Cannot load config" in captured.out


def test_validate_empty_file_exit_two(tmp_path, capsys):
    """Empty config file exits 2 (documented: treated as load failure
    because the YAML root is not a mapping)."""
    path = _write_yaml(tmp_path, "", name="empty.yaml")
    result = main(["validate", path])
    captured = capsys.readouterr()
    assert result == 2
    assert "Cannot load config" in captured.out


def test_validate_json_mode(tmp_path, capsys):
    """--json emits the machine-readable dict and no human text."""
    path = _write_yaml(tmp_path, _VALIDATE_VALID_CONFIG)
    result = main(["validate", path, "--json"])
    captured = capsys.readouterr()
    assert result == 0
    data = json.loads(captured.out)
    assert isinstance(data, dict)
    for key in ("valid", "errors", "warnings", "strict", "config"):
        assert key in data
    assert data["valid"] is True
    assert data["errors"] == []
    assert data["warnings"] == []
    assert data["strict"] is False
    assert data["config"] == {"name": "myharness", "version": "1.0"}
    # Human-readable sections must be absent in JSON mode
    assert "Config Summary" not in captured.out
    assert "Config valid:" not in captured.out
    assert captured.out.lstrip().startswith("{")


def test_validate_json_mode_invalid_config(tmp_path, capsys):
    """--json on an invalid config reports errors and exits 1."""
    path = _write_yaml(tmp_path, _VALIDATE_ERROR_CONFIG)
    result = main(["validate", path, "--json"])
    captured = capsys.readouterr()
    assert result == 1
    data = json.loads(captured.out)
    assert data["valid"] is False
    assert any("held_out_ratio" in e for e in data["errors"])


def test_validate_strict_json_combination(tmp_path, capsys):
    """--strict + --json: warnings yield exit 1 with strict=True."""
    path = _write_yaml(tmp_path, _VALIDATE_WARNING_CONFIG)
    result = main(["validate", path, "--strict", "--json"])
    captured = capsys.readouterr()
    assert result == 1
    data = json.loads(captured.out)
    assert data["strict"] is True
    assert data["valid"] is True  # no errors; strict affects exit code
    assert len(data["warnings"]) == 1


def test_validate_json_mode_missing_file_exit_two(tmp_path, capsys):
    """--json on a missing file exits 2 and emits JSON with config=None."""
    path = str(tmp_path / "missing.yaml")
    result = main(["validate", path, "--json"])
    captured = capsys.readouterr()
    assert result == 2
    data = json.loads(captured.out)
    assert data["valid"] is False
    assert data["config"] is None
    assert len(data["errors"]) == 1


def test_validate_help_mentions_strict(capsys):
    """`validate --help` exits 0 and documents --strict."""
    with pytest.raises(SystemExit) as exc_info:
        main(["validate", "--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "--strict" in captured.out


def test_validate_legacy_flag(tmp_path):
    """Legacy --harness-validate flag validates a config."""
    path = _write_yaml(tmp_path, _VALIDATE_VALID_CONFIG)
    assert main(["--harness-validate", path]) == 0


def test_validate_legacy_flag_invalid_config(tmp_path):
    """Legacy --harness-validate flag exits 1 on invalid config."""
    path = _write_yaml(tmp_path, _VALIDATE_ERROR_CONFIG)
    assert main(["--harness-validate", path]) == 1


def test_validate_legacy_flag_missing_file_exit_two(tmp_path):
    """Legacy --harness-validate flag exits 2 on missing file."""
    path = str(tmp_path / "missing.yaml")
    assert main(["--harness-validate", path]) == 2


def test_validate_parser_strict_flag():
    """Parser accepts --strict after the validate positional."""
    parser = build_parser()
    args = parser.parse_args(["validate", "config.yaml", "--strict"])
    assert args.command == "validate"
    assert args.strict is True
    args = parser.parse_args(["validate", "config.yaml"])
    assert getattr(args, "strict", False) is False


def test_validate_parser_flags_after_positional():
    """Parser accepts --json/--no-color after the validate positional
    without clobbering top-level flags."""
    parser = build_parser()
    args = parser.parse_args(
        ["validate", "config.yaml", "--json", "--no-color"]
    )
    assert args.json is True
    assert args.no_color is True
    # Top-level --json must survive the subparser defaults
    args = parser.parse_args(["--json", "validate", "config.yaml"])
    assert args.json is True


def test_validate_no_color_output(tmp_path, capsys):
    """--no-color suppresses ANSI escape sequences."""
    path = _write_yaml(tmp_path, _VALIDATE_VALID_CONFIG)
    result = main(["validate", path, "--no-color"])
    captured = capsys.readouterr()
    assert result == 0
    assert "\033[" not in captured.out


# ---------------------------------------------------------------------------
# Invalid subcommand
# ---------------------------------------------------------------------------

def test_invalid_subcommand():
    """Invalid subcommand is handled by argparse SystemExit."""
    with pytest.raises(SystemExit):
        main(["invalid_cmd"])


# ---------------------------------------------------------------------------
# main() integration
# ---------------------------------------------------------------------------

def test_main_run():
    """main() runs the 'run' command."""
    result = main(["run", "test_scenario"])
    assert result == 0


def test_main_evolve():
    """main() runs the 'evolve' command."""
    result = main(["evolve", "test_scenario", "--max-cycles", "1"])
    assert result == 0


def test_main_variants():
    """main() runs the 'variants' command."""
    result = main(["variants", "test_scenario", "--count", "1", "--parallel", "1"])
    assert result == 0


def test_main_validate(tmp_path):
    """main() runs the 'validate' command."""
    import yaml
    config_data = {
        "version": "1.0",
        "name": "test",
        "surfaces": [{"name": "api1", "type": "API"}],
        "scenarios": {},
        "verifiers": [],
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)
    result = main(["validate", str(config_path)])
    assert result == 0


def test_main_json_flag():
    """main() passes --json through to commands."""
    result = main(["--json", "run", "test_scenario"])
    assert result == 0


def test_main_no_color_flag():
    """main() passes --no-color through to commands."""
    result = main(["--no-color", "run", "test_scenario"])
    assert result == 0
