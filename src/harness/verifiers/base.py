"""Abstract base class for all Harness output verifiers."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from harness.core.types import VerificationResult


class Verifier(ABC):
    """Abstract base class for a verifier that compares expected and actual
    outputs and returns a :class:`VerificationResult`.

    Subclasses must override :meth:`verify`.
    """

    def __init__(self, name: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        """Initialise the verifier.

        Args:
            name: Human-readable name for this verifier instance.
            config: Arbitrary configuration dictionary.
        """
        self.name: str = name or self.__class__.__name__
        self.config: Dict[str, Any] = config or {}

    @abstractmethod
    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Compare *expected* against *actual* and return a verdict.

        Args:
            expected: The ground-truth or reference value.
            actual: The value produced by the agent under test.
            context: Optional additional context (e.g. scenario metadata).

        Returns:
            A :class:`VerificationResult` describing the outcome.
        """
        ...
