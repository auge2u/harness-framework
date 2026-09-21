"""Signal handling and graceful shutdown for the Harness framework."""

from __future__ import annotations

import signal
import sys
from typing import Callable, List, Optional


class GracefulShutdown:
    """Manages graceful shutdown on SIGINT/SIGTERM.

    This is a singleton class — only one instance exists per process.
    Registered cleanup handlers are called in reverse order when a
    shutdown signal is received.
    """

    _instance = None
    _handlers: List[Callable] = []
    _shutdown_requested = False
    _original_sigint = None
    _original_sigterm = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, handler: Callable) -> None:
        """Register a cleanup handler.

        The handler will be called (in reverse registration order) when
        a shutdown signal is received.
        """
        cls._handlers.append(handler)

    @classmethod
    def unregister(cls, handler: Callable) -> None:
        """Remove a cleanup handler."""
        if handler in cls._handlers:
            cls._handlers.remove(handler)

    @classmethod
    def install(cls) -> None:
        """Install signal handlers for SIGINT and SIGTERM."""
        cls._original_sigint = signal.getsignal(signal.SIGINT)
        cls._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, cls._handle_signal)
        signal.signal(signal.SIGTERM, cls._handle_signal)

    @classmethod
    def restore(cls) -> None:
        """Restore original signal handlers."""
        if cls._original_sigint is not None:
            signal.signal(signal.SIGINT, cls._original_sigint)
        if cls._original_sigterm is not None:
            signal.signal(signal.SIGTERM, cls._original_sigterm)

    @classmethod
    def _handle_signal(cls, signum, frame) -> None:
        """Handle shutdown signals.

        Runs all registered handlers in reverse order, then exits with
        the conventional ``128 + signum`` exit code.
        """
        try:
            sig_name = signal.Signals(signum).name
        except (AttributeError, ValueError):
            sig_name = str(signum)
        print(f"\nReceived {sig_name}, shutting down gracefully...", file=sys.stderr)
        cls._shutdown_requested = True
        for handler in reversed(cls._handlers):
            try:
                handler()
            except Exception as e:
                print(f"Error in shutdown handler: {e}", file=sys.stderr)
        sys.exit(128 + signum)

    @classmethod
    def is_shutdown_requested(cls) -> bool:
        """Return whether a shutdown has been requested."""
        return cls._shutdown_requested
