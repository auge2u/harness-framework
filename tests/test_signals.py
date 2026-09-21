"""Tests for signal handling and graceful shutdown (F1)."""
from __future__ import annotations

import signal
import sys

import pytest

from harness.signals import GracefulShutdown


class TestGracefulShutdown:
    """Tests for GracefulShutdown singleton."""

    def test_singleton(self):
        """GracefulShutdown is a singleton."""
        gs1 = GracefulShutdown()
        gs2 = GracefulShutdown()
        assert gs1 is gs2

    def test_register_handler(self):
        """register adds a cleanup handler."""
        GracefulShutdown._handlers.clear()
        called = [False]

        def handler():
            called[0] = True

        GracefulShutdown.register(handler)
        assert handler in GracefulShutdown._handlers
        assert len(GracefulShutdown._handlers) == 1

    def test_unregister_handler(self):
        """unregister removes a cleanup handler."""
        GracefulShutdown._handlers.clear()

        def handler():
            pass

        GracefulShutdown.register(handler)
        GracefulShutdown.unregister(handler)
        assert handler not in GracefulShutdown._handlers
        assert len(GracefulShutdown._handlers) == 0

    def test_install_captures_signals(self):
        """install replaces SIGINT and SIGTERM handlers."""
        GracefulShutdown.restore()
        original_int = signal.getsignal(signal.SIGINT)
        original_term = signal.getsignal(signal.SIGTERM)

        GracefulShutdown.install()
        assert signal.getsignal(signal.SIGINT) != original_int
        assert signal.getsignal(signal.SIGTERM) != original_term
        # Verify it's our handler by name
        assert signal.getsignal(signal.SIGINT).__name__ == GracefulShutdown._handle_signal.__name__
        assert signal.getsignal(signal.SIGTERM).__name__ == GracefulShutdown._handle_signal.__name__

        # Cleanup
        GracefulShutdown.restore()
        assert signal.getsignal(signal.SIGINT) == original_int
        assert signal.getsignal(signal.SIGTERM) == original_term

    def test_restore_signals(self):
        """restore brings back original signal handlers."""
        GracefulShutdown.restore()
        original_int = signal.getsignal(signal.SIGINT)

        GracefulShutdown.install()
        GracefulShutdown.restore()
        assert signal.getsignal(signal.SIGINT) is original_int

    def test_handle_signal_runs_handlers(self):
        """_handle_signal runs all registered handlers in reverse order."""
        GracefulShutdown._handlers.clear()
        GracefulShutdown._shutdown_requested = False
        order = []

        def handler1():
            order.append(1)

        def handler2():
            order.append(2)

        GracefulShutdown.register(handler1)
        GracefulShutdown.register(handler2)

        # Mock sys.exit to avoid actually exiting
        old_exit = sys.exit
        exits = []

        def mock_exit(code):
            exits.append(code)

        sys.exit = mock_exit
        try:
            GracefulShutdown._handle_signal(signal.SIGINT, None)
        finally:
            sys.exit = old_exit

        # Handlers should run in reverse order: handler2 first, then handler1
        assert order == [2, 1]
        assert exits == [128 + signal.SIGINT]
        assert GracefulShutdown.is_shutdown_requested() is True

        # Cleanup state
        GracefulShutdown._shutdown_requested = False
        GracefulShutdown._handlers.clear()

    def test_handle_signal_error_in_handler(self):
        """_handle_signal continues even if a handler raises."""
        GracefulShutdown._handlers.clear()
        GracefulShutdown._shutdown_requested = False
        order = []

        def handler1():
            order.append(1)

        def bad_handler():
            order.append("error")
            raise RuntimeError("boom")

        GracefulShutdown.register(bad_handler)
        GracefulShutdown.register(handler1)

        old_exit = sys.exit
        exits = []

        def mock_exit(code):
            exits.append(code)

        sys.exit = mock_exit
        try:
            GracefulShutdown._handle_signal(signal.SIGTERM, None)
        finally:
            sys.exit = old_exit

        # Both handlers should have been called (reverse order)
        assert "error" in order
        assert 1 in order
        assert exits == [128 + signal.SIGTERM]

        # Cleanup state
        GracefulShutdown._shutdown_requested = False
        GracefulShutdown._handlers.clear()

    def test_is_shutdown_requested_initially_false(self):
        """is_shutdown_requested returns False initially."""
        GracefulShutdown._shutdown_requested = False
        assert GracefulShutdown.is_shutdown_requested() is False

    def test_is_shutdown_requested_after_signal(self):
        """is_shutdown_requested returns True after signal handling."""
        GracefulShutdown._handlers.clear()
        GracefulShutdown._shutdown_requested = False

        old_exit = sys.exit
        sys.exit = lambda code: None
        try:
            GracefulShutdown._handle_signal(signal.SIGINT, None)
        finally:
            sys.exit = old_exit

        assert GracefulShutdown.is_shutdown_requested() is True

        # Cleanup state
        GracefulShutdown._shutdown_requested = False
        GracefulShutdown._handlers.clear()
