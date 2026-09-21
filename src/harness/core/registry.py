"""Plugin registry for verifiers, proposers, runners, and hooks."""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any, Callable, Dict, List, Type

from harness.core.exceptions import PluginAlreadyRegisteredError, PluginNotFoundError


class PluginRegistry:
    """Central registry for Harness plugins.

    The registry stores references to verifier, proposer, and runner
    **classes** (not instances) by a human-readable name.  It also
    implements a lightweight event hook system so that external code can
    subscribe to lifecycle events.
    """

    def __init__(self) -> None:
        self._verifiers: Dict[str, Type] = {}
        self._proposers: Dict[str, Type] = {}
        self._runners: Dict[str, Type] = {}
        self._hooks: Dict[str, List[Callable]] = {}

    # ------------------------------------------------------------------
    # Verifiers
    # ------------------------------------------------------------------

    def register_verifier(
        self, name: str, cls: Type, override: bool = False
    ) -> None:
        """Register a verifier class under *name*.

        Args:
            name: Unique identifier for the verifier.
            cls: The verifier class (must be callable to produce an instance).
            override: If ``True``, silently replace an existing registration.

        Raises:
            PluginAlreadyRegisteredError: If *name* is already taken and
                *override* is ``False``.
        """
        if name in self._verifiers and not override:
            raise PluginAlreadyRegisteredError(
                f"Verifier '{name}' is already registered. "
                f"Use override=True to replace it."
            )
        self._verifiers[name] = cls

    def get_verifier(self, name: str) -> Type:
        """Retrieve the verifier class registered under *name*.

        Raises:
            PluginNotFoundError: If no verifier is registered under *name*.
        """
        try:
            return self._verifiers[name]
        except KeyError as exc:
            raise PluginNotFoundError(f"Verifier '{name}' not found in registry") from exc

    def list_verifiers(self) -> List[str]:
        """Return a sorted list of registered verifier names."""
        return sorted(self._verifiers.keys())

    # ------------------------------------------------------------------
    # Proposers
    # ------------------------------------------------------------------

    def register_proposer(
        self, name: str, cls: Type, override: bool = False
    ) -> None:
        """Register a proposer class under *name*.

        Args:
            name: Unique identifier for the proposer.
            cls: The proposer class.
            override: If ``True``, silently replace an existing registration.

        Raises:
            PluginAlreadyRegisteredError: If *name* is already taken and
                *override* is ``False``.
        """
        if name in self._proposers and not override:
            raise PluginAlreadyRegisteredError(
                f"Proposer '{name}' is already registered. "
                f"Use override=True to replace it."
            )
        self._proposers[name] = cls

    def get_proposer(self, name: str) -> Type:
        """Retrieve the proposer class registered under *name*.

        Raises:
            PluginNotFoundError: If no proposer is registered under *name*.
        """
        try:
            return self._proposers[name]
        except KeyError as exc:
            raise PluginNotFoundError(f"Proposer '{name}' not found in registry") from exc

    def list_proposers(self) -> List[str]:
        """Return a sorted list of registered proposer names."""
        return sorted(self._proposers.keys())

    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------

    def register_runner(
        self, name: str, cls: Type, override: bool = False
    ) -> None:
        """Register a runner class under *name*.

        Args:
            name: Unique identifier for the runner.
            cls: The runner class.
            override: If ``True``, silently replace an existing registration.

        Raises:
            PluginAlreadyRegisteredError: If *name* is already taken and
                *override* is ``False``.
        """
        if name in self._runners and not override:
            raise PluginAlreadyRegisteredError(
                f"Runner '{name}' is already registered. "
                f"Use override=True to replace it."
            )
        self._runners[name] = cls

    def get_runner(self, name: str) -> Type:
        """Retrieve the runner class registered under *name*.

        Raises:
            PluginNotFoundError: If no runner is registered under *name*.
        """
        try:
            return self._runners[name]
        except KeyError as exc:
            raise PluginNotFoundError(f"Runner '{name}' not found in registry") from exc

    def list_runners(self) -> List[str]:
        """Return a sorted list of registered runner names."""
        return sorted(self._runners.keys())

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def register_hook(self, event: str, callback: Callable) -> None:
        """Subscribe *callback* to an *event*.

        Multiple callbacks may be registered for the same event; they are
        invoked in registration order.
        """
        self._hooks.setdefault(event, []).append(callback)

    def emit(self, event: str, **kwargs: Any) -> None:
        """Invoke all callbacks registered for *event*.

        Exceptions raised by individual callbacks are caught so that one
        failing listener does not break the others.
        """
        for callback in self._hooks.get(event, []):
            try:
                callback(**kwargs)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self, package_prefix: str = "harness.plugins") -> int:
        """Auto-discover plugins by scanning *package_prefix*.

        Every module found underneath the package is imported and, if it
        exposes a callable named ``register``, that function is called with
        this registry instance.

        Args:
            package_prefix: Dotted Python package path to scan.

        Returns:
            The number of modules that provided a ``register`` callable.
        """
        discovered = 0
        try:
            root = importlib.import_module(package_prefix)
        except ImportError:
            return discovered

        if not hasattr(root, "__path__"):
            return discovered

        for _importer, modname, _ispkg in pkgutil.iter_modules(
            root.__path__, prefix=package_prefix + "."
        ):
            try:
                mod = importlib.import_module(modname)
            except Exception:
                continue
            if hasattr(mod, "register") and callable(mod.register):
                try:
                    mod.register(self)
                    discovered += 1
                except Exception:
                    continue
        return discovered
