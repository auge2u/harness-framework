"""Abstract base class for all Harness test scenarios."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class Scenario(ABC):
    """Abstract base class for a single test scenario.

    A scenario describes a specific testing situation: it defines the
    surfaces involved, how to set up the test context, how to execute the
    agent under test, and how to tear everything down afterwards.

    Subclasses **must** override :meth:`setup`, :meth:`run`, and
    :meth:`teardown`.  They may also override :meth:`get_expected` to
    provide a reference output for verifiers.
    """

    scenario_id: str = ""
    name: str = ""
    description: str = ""
    tags: List[str] = None  # type: ignore
    surfaces: List[str] = None  # type: ignore
    difficulty: float = 0.5

    def __init__(self):
        # Per-instance mutable defaults to avoid shared-state footgun
        if self.tags is None:
            self.tags = []
        if self.surfaces is None:
            self.surfaces = []

    @abstractmethod
    def setup(self) -> Dict[str, Any]:
        """Prepare the environment and return an initial context dict.

        Returns:
            A dictionary containing any state needed by :meth:`run` and
            :meth:`teardown`.
        """
        ...

    @abstractmethod
    def run(self, agent: Any, context: Dict[str, Any]) -> Dict[str, Any]:
        """Execute the agent under test.

        Args:
            agent: The object/agent being evaluated.
            context: The context dictionary returned by :meth:`setup`.

        Returns:
            A dictionary of outputs produced by the agent.
        """
        ...

    @abstractmethod
    def teardown(self, context: Dict[str, Any]) -> None:
        """Clean up any resources allocated during :meth:`setup`.

        Args:
            context: The context dictionary returned by :meth:`setup`.
        """
        ...

    def get_expected(self) -> Optional[Dict[str, Any]]:
        """Return the expected output for this scenario, or ``None``.

        The default implementation returns ``None``, which signals to
        verifiers that no ground-truth comparison is available.
        """
        return None
