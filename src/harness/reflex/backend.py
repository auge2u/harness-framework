"""Reflex (System 1) classification backends.

A :class:`ReflexBackend` is a zero-text-generation classifier: it returns
probabilities and scores only, never natural language.  This mirrors the
"Jev primitives" pattern (bool / score / choice) inside the Harness
Framework, following the same ABC + deterministic-mock + HTTP-stub shape
as :mod:`harness.agent_backend`.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set

if TYPE_CHECKING:  # pragma: no cover - typing only
    from harness.reflex.rubrics import Rubric

__all__ = ["ReflexBackend", "MockReflexBackend", "JevBackend"]


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str, min_len: int = 3) -> Set[str]:
    """Split *text* into a lowercase alphanumeric token set.

    Underscores and other non-alphanumeric characters act as separators
    (so ``sanitize_input`` yields ``sanitize`` and ``input``).  Tokens
    shorter than *min_len* characters are dropped to avoid noise from
    punctuation and single-letter identifiers.
    """
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) >= min_len}


class ReflexBackend(ABC):
    """System 1 classification backend. Zero text generated — probabilities only.

    Concrete subclasses implement the three Jev primitives:

    * :meth:`bool_check` — binary probability (0.0 – 1.0).
    * :meth:`score_check` — rubric score (0.0 – 10.0).
    * :meth:`choice_check` — probability distribution over named options.
    """

    name: str = ""
    #: Latency of the most recent primitive call, in milliseconds.
    last_latency_ms: float = 0.0

    @abstractmethod
    def bool_check(self, question: str, context: str) -> float:
        """Return the probability (0.0 – 1.0) that *question* is true of *context*.

        Args:
            question: Natural-language yes/no question.
            context: Text the question is evaluated against.

        Returns:
            Probability in ``[0.0, 1.0]``.
        """
        ...

    @abstractmethod
    def score_check(self, input_text: str, rubric: "Rubric") -> float:
        """Score *input_text* against *rubric* on a 0.0 – 10.0 spectrum.

        Args:
            input_text: Text to evaluate.
            rubric: The :class:`~harness.reflex.rubrics.Rubric` to apply.

        Returns:
            Score in ``[0.0, 10.0]``.
        """
        ...

    @abstractmethod
    def choice_check(
        self,
        input_text: str,
        options: List[Dict[str, str]],
        context: str = "",
    ) -> Dict[str, float]:
        """Return a probability distribution over *options*.

        Args:
            input_text: Text to classify.
            options: List of ``{"name": ..., "description": ...}`` dicts
                (maximum 255, per the Jev choice primitive).
            context: Optional surrounding context.

        Returns:
            Mapping of option name to probability; values sum to 1.0
            (empty mapping when *options* is empty).
        """
        ...


# ---------------------------------------------------------------------------
# Mock backend (deterministic, for testing)
# ---------------------------------------------------------------------------


class MockReflexBackend(ReflexBackend):
    """Deterministic keyword-heuristic reflex backend for testing.

    * ``bool_check`` scans the context for suspicious keyword sets
      (``secret``, ``password``, ``token``, ``eval(``, ``exec(``,
      ``drop table``, ``bearer``, ``api_key``, ``ssn``, ``private_key``,
      ``credit_card``) and returns ``0.96`` when any is found, else
      ``0.02``.
    * ``score_check`` applies ``rubric.keyword_signals`` — a mapping of
      signal substring to score delta — and clamps the summed score to
      the ``[0.0, 10.0]`` rubric range.
    * ``choice_check`` computes token-overlap scores between the input
      and each option's name + description, normalized to sum to 1.0.

    All methods are fully deterministic: identical inputs always produce
    identical outputs.  Every call simulates a fixed ``115.0`` ms
    System 1 latency via :attr:`last_latency_ms`.
    """

    name = "mock"

    #: Keyword sets that trip the bool primitive.
    SUSPICIOUS_KEYWORDS = (
        "secret",
        "password",
        "token",
        "eval(",
        "exec(",
        "drop table",
        "bearer",
        "api_key",
        "ssn",
        "private_key",
        "credit_card",
    )

    #: Probability returned by bool_check when a suspicious keyword is found.
    SUSPICIOUS_PROBABILITY = 0.96
    #: Probability returned by bool_check for clean contexts.
    CLEAN_PROBABILITY = 0.02
    #: Simulated System 1 latency in milliseconds.
    SIMULATED_LATENCY_MS = 115.0

    def __init__(self) -> None:
        self.last_latency_ms = self.SIMULATED_LATENCY_MS
        self.call_count = 0

    def bool_check(self, question: str, context: str) -> float:
        """Scan *context* for suspicious keywords and return a probability."""
        self.call_count += 1
        self.last_latency_ms = self.SIMULATED_LATENCY_MS
        context_lower = (context or "").lower()
        if any(kw in context_lower for kw in self.SUSPICIOUS_KEYWORDS):
            return self.SUSPICIOUS_PROBABILITY
        return self.CLEAN_PROBABILITY

    def score_check(self, input_text: str, rubric: "Rubric") -> float:
        """Apply ``rubric.keyword_signals`` to *input_text* and clamp to [0, 10].

        Rubric schema v2: when ``rubric.required_signals`` is non-empty and
        any required signal is absent from *input_text*, the rubric does not
        apply and the score is ``0.0``.
        """
        self.call_count += 1
        self.last_latency_ms = self.SIMULATED_LATENCY_MS
        text_lower = (input_text or "").lower()
        required = getattr(rubric, "required_signals", None) or []
        if required and not all(
            str(signal).lower() in text_lower for signal in required
        ):
            return 0.0
        score = 0.0
        for signal, delta in rubric.keyword_signals.items():
            if signal.lower() in text_lower:
                score += float(delta)
        score = max(0.0, min(10.0, score))
        return round(score, 4)

    def choice_check(
        self,
        input_text: str,
        options: List[Dict[str, str]],
        context: str = "",
    ) -> Dict[str, float]:
        """Score options by token overlap with the input, normalized to 1.0."""
        self.call_count += 1
        self.last_latency_ms = self.SIMULATED_LATENCY_MS
        if not options:
            return {}
        input_tokens = _tokenize(input_text or "")
        scores: Dict[str, float] = {}
        for option in options:
            option_text = f"{option.get('name', '')} {option.get('description', '')}"
            option_tokens = _tokenize(option_text)
            overlap = len(input_tokens & option_tokens)
            scores[str(option.get("name", ""))] = 0.05 + 0.35 * overlap
        total = sum(scores.values()) or 1.0
        probabilities = {k: round(v / total, 4) for k, v in scores.items()}
        # Fix rounding drift on the highest-probability entry so the
        # distribution sums to exactly 1.0.
        drift = round(1.0 - sum(probabilities.values()), 4)
        if drift and probabilities:
            top = max(probabilities, key=lambda k: (probabilities[k],))
            probabilities[top] = round(probabilities[top] + drift, 4)
        return probabilities


# ---------------------------------------------------------------------------
# Jev HTTP backend (TypeSafe-Jev-compatible API)
# ---------------------------------------------------------------------------


class JevBackend(ReflexBackend):
    """HTTP client for a TypeSafe-Jev-compatible API.

    Uses only :mod:`urllib` from the standard library (no new
    dependencies) with a 3 second default timeout.  On any network or API
    failure the call degrades gracefully to an internal
    :class:`MockReflexBackend` and sets :attr:`used_fallback` to ``True``
    on the instance.

    Args:
        api_key: TypeSafe API key.  Falls back to the
            ``TYPESAFE_API_KEY`` environment variable when omitted.
        base_url: Base URL of the Jev API (``/bool``, ``/score`` and
            ``/choice`` endpoints are appended).
        timeout: Request timeout in seconds (default ``3.0``).
    """

    name = "jev"
    DEFAULT_BASE_URL = "https://console.typesafe.ai/api/v1"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: float = 3.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        self.base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self.used_fallback = False
        self.last_latency_ms = 0.0
        self._fallback = MockReflexBackend()

    # -- HTTP plumbing -------------------------------------------------------

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST *payload* as JSON to ``{base_url}/{path}`` and parse the reply."""
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/{path.lstrip('/')}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    # -- Primitives ----------------------------------------------------------

    def bool_check(self, question: str, context: str) -> float:
        """Call the Jev ``/bool`` endpoint, falling back to the mock on failure."""
        start = time.monotonic()
        try:
            data = self._post("bool", {"question": question, "context": context})
            probability = float(data["probability"])
            self.last_latency_ms = (time.monotonic() - start) * 1000.0
            return max(0.0, min(1.0, probability))
        except Exception:
            self.used_fallback = True
            probability = self._fallback.bool_check(question, context)
            self.last_latency_ms = self._fallback.last_latency_ms
            return probability

    def score_check(self, input_text: str, rubric: "Rubric") -> float:
        """Call the Jev ``/score`` endpoint, falling back to the mock on failure."""
        start = time.monotonic()
        try:
            data = self._post(
                "score",
                {
                    "input": input_text,
                    "rubric": {
                        "rubric_id": rubric.rubric_id,
                        "name": rubric.name,
                        "version": rubric.version,
                        "criteria": list(rubric.criteria),
                    },
                },
            )
            score = float(data["score"])
            self.last_latency_ms = (time.monotonic() - start) * 1000.0
            return max(0.0, min(10.0, score))
        except Exception:
            self.used_fallback = True
            score = self._fallback.score_check(input_text, rubric)
            self.last_latency_ms = self._fallback.last_latency_ms
            return score

    def choice_check(
        self,
        input_text: str,
        options: List[Dict[str, str]],
        context: str = "",
    ) -> Dict[str, float]:
        """Call the Jev ``/choice`` endpoint, falling back to the mock on failure."""
        start = time.monotonic()
        try:
            data = self._post(
                "choice",
                {"input": input_text, "options": options, "context": context},
            )
            choices = data["choices"]
            self.last_latency_ms = (time.monotonic() - start) * 1000.0
            return {str(c["name"]): float(c["probability"]) for c in choices}
        except Exception:
            self.used_fallback = True
            probabilities = self._fallback.choice_check(input_text, options, context)
            self.last_latency_ms = self._fallback.last_latency_ms
            return probabilities
