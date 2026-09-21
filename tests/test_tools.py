"""Tests for the harness.tools module."""
from __future__ import annotations

import os
import tempfile

import pytest

from harness.tools import (
    ReadFileTool,
    RunCommandTool,
    Tool,
    ToolRegistry,
    ToolSchema,
    WriteFileTool,
)


# ---------------------------------------------------------------------------
# ToolSchema
# ---------------------------------------------------------------------------

def test_tool_schema_creation():
    """ToolSchema can be created with all fields."""
    schema = ToolSchema(
        name="read_file",
        description="Read a file",
        parameters={"path": {"type": "string"}},
        required=["path"],
        side_effects=["read"],
    )
    assert schema.name == "read_file"
    assert schema.description == "Read a file"
    assert schema.parameters == {"path": {"type": "string"}}
    assert schema.required == ["path"]
    assert schema.side_effects == ["read"]


def test_tool_schema_defaults():
    """ToolSchema has correct defaults."""
    schema = ToolSchema(name="test", description="A test tool")
    assert schema.parameters == {}
    assert schema.required == []
    assert schema.side_effects == []


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------

def test_registry_empty():
    """New ToolRegistry has no tools."""
    reg = ToolRegistry()
    assert reg.list_tools() == []
    assert len(reg) == 0


def test_registry_register_and_get():
    """ToolRegistry can register and retrieve tools."""
    reg = ToolRegistry()
    tool = ReadFileTool(allowed_paths=["/tmp"])
    reg.register("read_file", tool)
    assert "read_file" in reg.list_tools()
    retrieved = reg.get("read_file")
    assert isinstance(retrieved, ReadFileTool)


def test_registry_register_multiple():
    """ToolRegistry can hold multiple tools."""
    reg = ToolRegistry()
    reg.register("read_file", ReadFileTool())
    reg.register("write_file", WriteFileTool())
    assert len(reg.list_tools()) == 2


def test_registry_get_missing():
    """Getting a missing tool raises KeyError."""
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="not found"):
        reg.get("missing")


def test_registry_unregister():
    """ToolRegistry can unregister tools."""
    reg = ToolRegistry()
    reg.register("read_file", ReadFileTool())
    reg.unregister("read_file")
    assert reg.list_tools() == []


def test_registry_dispatch():
    """ToolRegistry.dispatch() calls the tool's execute method."""
    reg = ToolRegistry()
    reg.register("read_file", ReadFileTool(allowed_paths=["/tmp"]))
    with tempfile.NamedTemporaryFile(mode="w", dir="/tmp", delete=False, suffix=".txt") as f:
        f.write("hello from dispatch")
        tmp_path = f.name
    try:
        result = reg.dispatch("read_file", {"path": tmp_path})
        assert result["status"] == "ok"
        assert "hello from dispatch" in result["content"]
    finally:
        os.unlink(tmp_path)


def test_registry_override_false():
    """Registering without override fails for duplicate."""
    reg = ToolRegistry()
    reg.register("read_file", ReadFileTool())
    with pytest.raises(ValueError, match="already registered"):
        reg.register("read_file", ReadFileTool())


def test_registry_override_true():
    """Registering with override=True replaces existing."""
    reg = ToolRegistry()
    t1 = ReadFileTool(allowed_paths=["/tmp"])
    t2 = ReadFileTool(allowed_paths=["/var"])
    reg.register("read_file", t1)
    reg.register("read_file", t2, override=True)
    retrieved = reg.get("read_file")
    assert retrieved.allowed_paths == ["/var"]


def test_registry_list_tools_sorted():
    """list_tools returns sorted names."""
    reg = ToolRegistry()
    reg.register("read_file", ReadFileTool())
    reg.register("run_command", RunCommandTool())
    reg.register("write_file", WriteFileTool())
    assert reg.list_tools() == ["read_file", "run_command", "write_file"]


def test_registry_contains():
    """ToolRegistry supports 'in' operator."""
    reg = ToolRegistry()
    reg.register("read_file", ReadFileTool())
    assert "read_file" in reg
    assert "missing" not in reg


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------

def test_read_file_allowed():
    """ReadFileTool reads allowed files."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("test content")
        tmp_path = f.name
    tool = ReadFileTool(allowed_paths=[os.path.dirname(tmp_path)])
    try:
        result = tool.execute({"path": tmp_path})
        assert result["status"] == "ok"
        assert result["content"] == "test content"
    finally:
        os.unlink(tmp_path)


def test_read_file_blocks_outside_allowed():
    """ReadFileTool blocks paths outside allowed_paths."""
    tool = ReadFileTool(allowed_paths=["/tmp"])
    result = tool.execute({"path": "/etc/passwd"})
    assert result["status"] == "denied"


def test_read_file_not_found():
    """ReadFileTool returns not_found for missing files."""
    tool = ReadFileTool(allowed_paths=["/tmp"])
    result = tool.execute({"path": "/tmp/nonexistent_file_12345.txt"})
    assert result["status"] == "not_found"


def test_read_file_default_allowed_paths():
    """ReadFileTool uses cwd as default allowed_paths."""
    tool = ReadFileTool()
    assert tool.allowed_paths == [os.getcwd()]


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------

def test_write_file_success():
    """WriteFileTool writes files within sandbox."""
    with tempfile.TemporaryDirectory() as sandbox:
        tool = WriteFileTool(allowed_paths=[sandbox])
        result = tool.execute({"path": f"{sandbox}/test.txt", "content": "hello"})
        assert result["status"] == "ok"
        assert os.path.exists(f"{sandbox}/test.txt")
        with open(f"{sandbox}/test.txt") as f:
            assert f.read() == "hello"


def test_write_file_creates_dirs():
    """WriteFileTool creates intermediate directories."""
    with tempfile.TemporaryDirectory() as sandbox:
        tool = WriteFileTool(allowed_paths=[sandbox])
        result = tool.execute({"path": f"{sandbox}/a/b/c/test.txt", "content": "nested"})
        assert result["status"] == "ok"
        assert os.path.exists(f"{sandbox}/a/b/c/test.txt")


def test_write_file_blocks_outside_sandbox():
    """WriteFileTool blocks paths outside sandbox."""
    with tempfile.TemporaryDirectory() as sandbox:
        tool = WriteFileTool(allowed_paths=[sandbox])
        result = tool.execute({"path": "/tmp/escape.txt", "content": "bad"})
        assert result["status"] == "denied"


# ---------------------------------------------------------------------------
# RunCommandTool
# ---------------------------------------------------------------------------

def test_run_command_allowed():
    """RunCommandTool runs allowed commands."""
    tool = RunCommandTool(allowed_commands=["echo"])
    result = tool.execute({"command": "echo hello"})
    assert result["status"] == "ok"
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]


def test_run_command_blocks_not_allowed():
    """RunCommandTool blocks commands not in allowlist."""
    tool = RunCommandTool(allowed_commands=["echo"])
    result = tool.execute({"command": "rm -rf /"})
    assert result["status"] == "denied"


def test_run_command_blocked_patterns():
    """RunCommandTool blocks commands matching blocked patterns."""
    tool = RunCommandTool()
    result = tool.execute({"command": "sudo apt-get install something"})
    assert result["status"] == "denied"


def test_run_command_timeout():
    """RunCommandTool handles timeouts."""
    tool = RunCommandTool(allowed_commands=["sleep"])
    result = tool.execute({"command": "sleep 5", "timeout": 0.1})
    assert result["status"] == "timeout"
    assert "timed out" in result["error"]


def test_run_command_no_allowlist():
    """RunCommandTool with no allowlist allows safe commands."""
    tool = RunCommandTool()
    result = tool.execute({"command": "echo free"})
    assert result["status"] == "ok"
    assert "free" in result["stdout"]


def test_run_command_no_command():
    """RunCommandTool handles empty command."""
    tool = RunCommandTool()
    result = tool.execute({"command": ""})
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Tool ABC
# ---------------------------------------------------------------------------

def test_tool_abc_cannot_instantiate():
    """Tool ABC cannot be instantiated directly."""
    with pytest.raises(TypeError):
        Tool()


def test_tool_get_schema():
    """Tool.get_schema() returns the tool's schema."""
    tool = ReadFileTool()
    schema = tool.get_schema()
    assert isinstance(schema, ToolSchema)
    assert schema.name == "read_file"

# ---------------------------------------------------------------------------
# Path traversal hardening (E2)
# ---------------------------------------------------------------------------

class TestResolveAndValidate:
    """Tests for _resolve_and_validate path traversal protection."""

    def test_traversal_blocked(self, tmp_path):
        """../../../etc/passwd is blocked."""
        from harness.tools import _resolve_and_validate
        allowed = [str(tmp_path)]
        resolved, is_valid, error = _resolve_and_validate("../../../etc/passwd", allowed)
        assert is_valid is False
        assert "outside" in error.lower() or "traversal" in error.lower()

    def test_normpath_traversal_blocked(self, tmp_path):
        """/allowed/../etc/passwd is blocked."""
        from harness.tools import _resolve_and_validate
        allowed = [str(tmp_path)]
        resolved, is_valid, error = _resolve_and_validate(
            str(tmp_path / ".." / "etc" / "passwd"), allowed
        )
        assert is_valid is False

    def test_deep_traversal_blocked(self, tmp_path):
        """allowed/subdir/../../../../etc/passwd is blocked."""
        from harness.tools import _resolve_and_validate
        allowed = [str(tmp_path)]
        resolved, is_valid, error = _resolve_and_validate(
            str(tmp_path / "subdir" / ".." / ".." / ".." / ".." / "etc" / "passwd"), allowed
        )
        assert is_valid is False

    def test_null_byte_blocked(self, tmp_path):
        """Null byte injection is blocked."""
        from harness.tools import _resolve_and_validate
        allowed = [str(tmp_path)]
        resolved, is_valid, error = _resolve_and_validate(
            str(tmp_path / "file.txt\x00.sh"), allowed
        )
        assert is_valid is False
        assert "null" in error.lower()

    def test_symlink_traversal_blocked(self, tmp_path):
        """Symlink to outside allowed dir is blocked."""
        from harness.tools import _resolve_and_validate
        allowed = [str(tmp_path)]
        # Create a file outside allowed path
        outside_file = tmp_path.parent / "outside_secret.txt"
        outside_file.write_text("secret")
        # Create symlink inside allowed path pointing outside
        symlink_path = tmp_path / "link_to_secret"
        symlink_path.symlink_to(outside_file)
        resolved, is_valid, error = _resolve_and_validate(str(symlink_path), allowed)
        assert is_valid is False
        assert "symlink" in error.lower()

    def test_valid_path_allowed(self, tmp_path):
        """Valid paths within allowed_paths work."""
        from harness.tools import _resolve_and_validate
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        resolved, is_valid, error = _resolve_and_validate(str(subdir / "file.txt"), [str(tmp_path)])
        assert is_valid is True
        assert error == ""

    def test_valid_absolute_path_allowed(self, tmp_path):
        """Absolute valid paths within allowed_paths work."""
        from harness.tools import _resolve_and_validate
        target = tmp_path / "data.json"
        resolved, is_valid, error = _resolve_and_validate(str(target), [str(tmp_path)])
        assert is_valid is True
        assert resolved == str(target)

    def test_unicode_normalization_allowed(self, tmp_path):
        """Unicode-normalized paths within allowed_paths work."""
        from harness.tools import _resolve_and_validate
        # Create a file with a composed Unicode name
        target = tmp_path / "caf\u00e9.txt"  # composed é
        target.write_text("test")
        # Pass the same name
        resolved, is_valid, error = _resolve_and_validate(str(target), [str(tmp_path)])
        assert is_valid is True

    def test_read_file_denies_traversal(self, tmp_path):
        """ReadFileTool.execute denies traversal attempts."""
        tool = ReadFileTool(allowed_paths=[str(tmp_path)])
        result = tool.execute({"path": "../../../etc/passwd"})
        assert result["status"] == "denied"

    def test_write_file_denies_traversal(self, tmp_path):
        """WriteFileTool.execute denies traversal attempts."""
        tool = WriteFileTool(allowed_paths=[str(tmp_path)])
        result = tool.execute({"path": "../../../etc/passwd", "content": "evil"})
        assert result["status"] == "denied"
