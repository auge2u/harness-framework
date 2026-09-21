"""Reflex skill/tool router built on the choice primitive.

A System 1 proxy that picks the single best skill for an incoming prompt
in milliseconds, injecting only that skill's instructions into the
System 2 context window and saving the remaining instruction tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from harness.reflex.primitives import ReflexPrimitives

__all__ = ["RoutableSkill", "RoutingDecision", "ReflexRouter"]

#: The Jev choice primitive supports at most 255 options per request.
MAX_SKILLS = 255


@dataclass
class RoutableSkill:
    """A skill registered in the reflex router catalog.

    Attributes:
        name: Unique skill name.
        description: Short natural-language description used for routing.
        instructions: Full system instructions injected when the skill is
            selected.
        trust_level: Provenance trust level (e.g. ``"core"``,
            ``"community"``).
    """

    name: str
    description: str
    instructions: str
    trust_level: str = "community"

    def to_option_dict(self) -> Dict[str, str]:
        """Format the skill for the choice primitive."""
        return {"name": self.name, "description": self.description}


@dataclass
class RoutingDecision:
    """Outcome of routing a prompt to a skill.

    Attributes:
        skill_name: The selected skill name (``"none"`` when the catalog
            is empty).
        probability: Probability of the selected skill.
        escalate: ``True`` when the top probability fell below the
            confidence floor and the prompt should go to System 2.
        token_savings_estimate: Estimated instruction tokens saved by
            injecting only the selected skill.
        latency_ms: System 1 routing latency.
        probabilities: Full probability distribution over the catalog.
    """

    skill_name: str
    probability: float
    escalate: bool
    token_savings_estimate: int
    latency_ms: float
    probabilities: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Normalise numeric fields and mutable defaults."""
        if self.probabilities is None:
            self.probabilities = {}
        self.probability = float(self.probability)
        self.latency_ms = float(self.latency_ms)
        self.token_savings_estimate = int(self.token_savings_estimate)
        self.escalate = bool(self.escalate)


class ReflexRouter:
    """System 1 skill/tool router using the choice primitive.

    Args:
        primitives: The reflex primitives wrapper used to evaluate the
            choice primitive.
        confidence_floor: Minimum top probability before the routing
            decision escalates to System 2.
    """

    def __init__(
        self,
        primitives: ReflexPrimitives,
        confidence_floor: float = 0.5,
    ) -> None:
        self.primitives = primitives
        self.confidence_floor = float(confidence_floor)
        self.skills: Dict[str, RoutableSkill] = {}

    def register_skill(self, skill: RoutableSkill) -> None:
        """Register *skill* in the router catalog.

        Raises:
            ValueError: If the catalog already holds 255 skills (the
                choice primitive's option limit).
        """
        if skill.name not in self.skills and len(self.skills) >= MAX_SKILLS:
            raise ValueError(
                f"The choice primitive supports up to {MAX_SKILLS} options "
                "per request; the skill catalog is full."
            )
        self.skills[skill.name] = skill

    def route(self, prompt: str, context_history: str = "") -> RoutingDecision:
        """Route *prompt* to the best-matching registered skill.

        Args:
            prompt: The incoming user prompt.
            context_history: Optional conversation history used as routing
                context.

        Returns:
            A :class:`RoutingDecision`.  When no skills are registered the
            decision is ``skill_name="none"`` with ``escalate=True``.
        """
        if not self.skills:
            return RoutingDecision(
                skill_name="none",
                probability=0.0,
                escalate=True,
                token_savings_estimate=0,
                latency_ms=0.0,
                probabilities={},
            )

        options = [skill.to_option_dict() for skill in self.skills.values()]
        routing_input = f"{prompt} {context_history}".strip()
        result = self.primitives.route(
            routing_input, options, confidence_floor=self.confidence_floor
        )

        selected = self.skills.get(str(result.value))
        all_tokens = sum(
            len(skill.instructions.split()) * 1.3 for skill in self.skills.values()
        )
        injected_tokens = (
            len(selected.instructions.split()) * 1.3 if selected else 0.0
        )
        tokens_saved = max(0, round(all_tokens - injected_tokens))

        return RoutingDecision(
            skill_name=selected.name if selected else "none",
            probability=result.confidence,
            escalate=result.escalate,
            token_savings_estimate=tokens_saved,
            latency_ms=result.latency_ms,
            probabilities=dict(result.detail.get("probabilities", {})),
        )
