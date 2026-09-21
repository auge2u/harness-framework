"""Tool abstraction with registry, dispatch, and sandboxed built-ins."""

from __future__ import annotations

import os
import subprocess
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ToolSchema:
    """JSON-schema-like description of a tool's inputs.

    Attributes:
        name: Unique tool identifier.
        description: Human-readable description of what the tool does.
        parameters: JSON Schema ``properties`` dict describing each
            parameter.
        required: List of required parameter names.
        side_effects: List of side-effect categories -- ``"read"``,
            ``"write"``, ``"network"``, ``"execute"``.
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    side_effects: List[str] = field(default_factory=list)


class Tool(ABC):
    """Abstract base for all tools.

    Every tool declares a :class:`ToolSchema` and implements
    :meth:`execute`.
    """

    schema: ToolSchema

    @abstractmethod
    def execute(
        self,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Execute the tool with given parameters.

        Args:
            params: Dictionary of parameter names to values, validated
                against :attr:`schema`.
            context: Optional execution context (e.g., tenant, run_id,
                sandbox boundaries).

        Returns:
            A dictionary with at minimum a ``status`` key (``"ok"``,
            ``"error"``, ``"denied"``, etc.) and tool-specific output.
        """
        ...

    def get_schema(self) -> ToolSchema:
        """Return this tool's schema."""
        return self.schema


class ToolRegistry:
    """Registry for tools.

    Provides name-based lookup and safe dispatch with optional
    context injection.
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(
        self, name: str, tool: Tool, override: bool = False
    ) -> None:
        """Register a tool under *name*.

        Args:
            name: Unique identifier for the tool.
            tool: The :class:`Tool` instance to register.
            override: If ``True``, silently replace an existing
                registration.

        Raises:
            ValueError: If *name* is already registered and
                *override* is ``False``.
        """
        if name in self._tools and not override:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = tool

    def get(self, name: str) -> Tool:
        """Retrieve the tool registered under *name*.

        Args:
            name: Tool identifier.

        Returns:
            The registered :class:`Tool` instance.

        Raises:
            KeyError: If no tool is registered under *name*.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry")
        return self._tools[name]

    def list_tools(self) -> List[str]:
        """Return a sorted list of all registered tool names."""
        return sorted(self._tools.keys())

    def dispatch(
        self,
        name: str,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Look up and execute a tool by name.

        Args:
            name: Tool identifier.
            params: Parameters to pass to the tool.
            context: Optional execution context.

        Returns:
            The tool's execution result.

        Raises:
            KeyError: If the tool is not found.
        """
        tool = self.get(name)
        return tool.execute(params, context)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry.

        Args:
            name: Tool identifier to remove.

        Raises:
            KeyError: If the tool is not found.
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found in registry")
        del self._tools[name]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


# ---------------------------------------------------------------------------
# Built-in sandboxed tools
# ---------------------------------------------------------------------------


def _resolve_and_validate(
    path: str, allowed_paths: List[str]
) -> Tuple[str, bool, str]:
    """Resolve a path and validate it's within allowed boundaries.

    Returns: (resolved_path: str, is_valid: bool, error_msg: str)

    Protects against:
    - Directory traversal (../../../etc/passwd)
    - Symlink traversal
    - Null byte injection
    - Unicode normalization attacks
    """
    # 1. Null byte check
    if "\x00" in path:
        return "", False, "Path contains null bytes"

    # 2. Unicode normalization (NFC)
    try:
        normalized_path = unicodedata.normalize("NFC", path)
    except (TypeError, ValueError):
        return "", False, "Path contains invalid Unicode"

    # 3. Normalize path and resolve to absolute
    try:
        norm_path = os.path.normpath(normalized_path)
        abs_path = os.path.abspath(norm_path)
    except (OSError, ValueError):
        return "", False, "Path normalization failed"

    # 4. Check for path traversal patterns that escape allowed paths
    # After normalization, any '..' that remains means the path tried to escape
    resolved_real = os.path.realpath(abs_path)

    # 5. Check for symlink traversal
    # Walk each component and verify no symlink points outside allowed paths
    current = resolved_real
    checked_paths = set()
    while current != os.path.dirname(current):
        if current in checked_paths:
            break
        checked_paths.add(current)
        if os.path.islink(current):
            link_target = os.readlink(current)
            if not os.path.isabs(link_target):
                link_target = os.path.normpath(
                    os.path.join(os.path.dirname(current), link_target)
                )
            link_real = os.path.realpath(link_target)
            if not any(
                link_real.startswith(os.path.realpath(ap))
                for ap in allowed_paths
            ):
                return "", False, f"Symlink traversal detected: {current}"
        current = os.path.dirname(current)

    # 6. Final boundary check: resolved path must be within allowed paths
    for allowed in allowed_paths:
        allowed_real = os.path.realpath(allowed)
        # Ensure allowed path ends with separator for prefix matching
        if not allowed_real.endswith(os.sep):
            allowed_prefix = allowed_real + os.sep
        else:
            allowed_prefix = allowed_real
        if resolved_real == allowed_real or resolved_real.startswith(allowed_prefix):
            return resolved_real, True, ""

    return resolved_real, False, f"Path '{path}' is outside allowed sandbox"


class ReadFileTool(Tool):
    """Read a file from the filesystem (sandboxed to *allowed_paths*).

    The tool will refuse to read files outside the configured
    *allowed_paths*, returning a ``"denied"`` status.
    """

    schema = ToolSchema(
        name="read_file",
        description="Read the contents of a file",
        parameters={
            "path": {
                "type": "string",
                "description": "Absolute or relative file path to read",
            }
        },
        required=["path"],
        side_effects=["read"],
    )

    def __init__(self, allowed_paths: Optional[List[str]] = None):
        self.allowed_paths = allowed_paths or [os.getcwd()]

    def _is_allowed(self, path: str) -> bool:
        """Check if *path* is within the allowed sandbox."""
        _resolved, is_valid, _error = _resolve_and_validate(
            path, self.allowed_paths
        )
        return is_valid

    def execute(
        self,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = params.get("path", "")
        if not self._is_allowed(path):
            return {
                "error": f"Path '{path}' is outside allowed sandbox",
                "path": path,
                "status": "denied",
            }
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            return {"content": content, "path": path, "status": "ok"}
        except FileNotFoundError:
            return {
                "error": f"File not found: {path}",
                "path": path,
                "status": "not_found",
            }
        except PermissionError:
            return {
                "error": f"Permission denied: {path}",
                "path": path,
                "status": "denied",
            }
        except Exception as exc:
            return {
                "error": str(exc),
                "path": path,
                "status": "error",
            }


class WriteFileTool(Tool):
    """Write content to a file (sandboxed to *allowed_paths*).

    Automatically creates parent directories if they do not exist.
    Refuses to write outside the configured *allowed_paths*.
    """

    schema = ToolSchema(
        name="write_file",
        description="Write content to a file",
        parameters={
            "path": {"type": "string", "description": "File path to write"},
            "content": {"type": "string", "description": "Content to write"},
        },
        required=["path", "content"],
        side_effects=["write"],
    )

    def __init__(self, allowed_paths: Optional[List[str]] = None):
        self.allowed_paths = allowed_paths or [os.getcwd()]

    def _is_allowed(self, path: str) -> bool:
        """Check if *path* is within the allowed sandbox."""
        _resolved, is_valid, _error = _resolve_and_validate(
            path, self.allowed_paths
        )
        return is_valid

    def execute(
        self,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        path = params.get("path", "")
        content = params.get("content", "")

        if not self._is_allowed(path):
            return {
                "error": f"Path '{path}' is outside allowed sandbox",
                "path": path,
                "status": "denied",
            }
        try:
            dir_name = os.path.dirname(path)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(content)
            return {
                "path": path,
                "bytes_written": len(content.encode("utf-8")),
                "status": "ok",
            }
        except PermissionError:
            return {
                "error": f"Permission denied: {path}",
                "path": path,
                "status": "denied",
            }
        except Exception as exc:
            return {
                "error": str(exc),
                "path": path,
                "status": "error",
            }


class RunCommandTool(Tool):
    """Run a shell command (sandboxed -- blocked commands, timeout).

    By default blocks dangerous commands (``rm``, ``sudo``, ``chmod``,
    etc.).  Commands are run via ``subprocess.run`` with a configurable
    timeout.
    """

    schema = ToolSchema(
        name="run_command",
        description="Run a shell command",
        parameters={
            "command": {
                "type": "string",
                "description": "Shell command to execute",
            },
            "timeout": {
                "type": "integer",
                "default": 30,
                "description": "Maximum seconds to wait for completion",
            },
        },
        required=["command"],
        side_effects=["execute"],
    )

    # Patterns that are unconditionally blocked for safety.
    BLOCKED_COMMANDS: set = {
        "rm",
        "sudo",
        "chmod",
        "chown",
        "mkfs",
        "dd",
        ">",
        ">>",
        "|",
    }

    def __init__(
        self,
        allowed_commands: Optional[List[str]] = None,
        blocked_commands: Optional[set] = None,
    ):
        self.allowed_commands = allowed_commands
        self.blocked_commands = blocked_commands or self.BLOCKED_COMMANDS

    def _contains_blocked_pattern(self, command: str) -> Optional[str]:
        """Check if *command* contains any blocked pattern.

        Returns:
            The first blocked pattern found, or ``None`` if clean.
        """
        for blocked in self.blocked_commands:
            if blocked in command:
                return blocked
        return None

    def execute(
        self,
        params: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        command = params.get("command", "")
        timeout = params.get("timeout", 30)

        if not command or not isinstance(command, str):
            return {
                "error": "No command provided or command is not a string",
                "status": "error",
            }

        # Check blocked patterns.
        blocked = self._contains_blocked_pattern(command)
        if blocked is not None:
            return {
                "error": f"Command contains blocked pattern: '{blocked}'",
                "command": command,
                "status": "denied",
            }

        # If allowed_commands is set, enforce whitelist.
        if self.allowed_commands is not None:
            base_cmd = command.strip().split()[0] if command.strip() else ""
            if base_cmd not in self.allowed_commands:
                return {
                    "error": (
                        f"Command '{base_cmd}' is not in the allowed "
                        f"command list"
                    ),
                    "command": command,
                    "status": "denied",
                }

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "status": "ok" if result.returncode == 0 else "error",
            }
        except subprocess.TimeoutExpired:
            return {
                "error": f"Command timed out after {timeout} seconds",
                "command": command,
                "status": "timeout",
            }
        except FileNotFoundError:
            return {
                "error": f"Shell executable not found for command: {command}",
                "command": command,
                "status": "error",
            }
        except Exception as exc:
            return {
                "error": str(exc),
                "command": command,
                "status": "error",
            }
