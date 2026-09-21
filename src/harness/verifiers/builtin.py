"""Built-in verifier implementations."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from harness.core.types import Verdict, VerificationResult
from harness.verifiers.base import Verifier


class ExactVerifier(Verifier):
    """Verifier that checks for strict equality between *expected* and *actual*.

    Score is ``1.0`` when values are equal, ``0.0`` otherwise.
    """

    def __init__(self, name: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(name=name or "exact", config=config)

    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Return PASS with score 1.0 if *expected* == *actual*, else FAIL."""
        if expected == actual:
            return VerificationResult(
                verdict=Verdict.PASS,
                score=1.0,
                details={"match": True},
                feedback="Values match exactly.",
            )
        return VerificationResult(
            verdict=Verdict.FAIL,
            score=0.0,
            details={"match": False, "expected": expected, "actual": actual},
            feedback="Values do not match exactly.",
        )


class FuzzyVerifier(Verifier):
    """Verifier that uses fuzzy string matching.

    Configuration keys:
        * ``threshold`` (float, default ``0.8``) — minimum score for PASS.
        * ``case_sensitive`` (bool, default ``False``) — whether matching
          is case-sensitive.

    Uses ``rapidfuzz`` when available, falling back to ``difflib.SequenceMatcher``.
    """

    def __init__(self, name: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(name=name or "fuzzy", config=config)
        self.threshold: float = float(self.config.get("threshold", 0.8))
        self.case_sensitive: bool = bool(self.config.get("case_sensitive", False))

    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Compute a fuzzy similarity score and return a verdict."""
        expected_str = str(expected) if expected is not None else ""
        actual_str = str(actual) if actual is not None else ""

        if not self.case_sensitive:
            expected_str = expected_str.lower()
            actual_str = actual_str.lower()

        score = self._compute_score(expected_str, actual_str)
        verdict = Verdict.PASS if score >= self.threshold else Verdict.FAIL

        return VerificationResult(
            verdict=verdict,
            score=score,
            details={
                "threshold": self.threshold,
                "case_sensitive": self.case_sensitive,
            },
            feedback=f"Fuzzy similarity score: {score:.4f} "
            f"(threshold: {self.threshold})",
        )

    def _compute_score(self, expected: str, actual: str) -> float:
        """Return a similarity score in ``[0.0, 1.0]``."""
        try:
            from rapidfuzz import fuzz  # type: ignore[import-untyped]
            return fuzz.ratio(expected, actual) / 100.0
        except Exception:
            pass

        try:
            import difflib
            return difflib.SequenceMatcher(None, expected, actual).ratio()
        except Exception:
            return 0.0


class JsonSchemaVerifier(Verifier):
    """Verifier that validates a JSON *actual* value against a JSON Schema.

    Configuration keys:
        * ``schema`` (dict) — the JSON Schema to validate against.

    Uses ``jsonschema`` when available.  Falls back to a primitive check
    that merely verifies *actual* is a ``dict`` and contains all required
    keys listed in the schema (if any ``required`` field is present).
    """

    def __init__(self, name: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(name=name or "json_schema", config=config)
        self.schema: Dict[str, Any] = self.config.get("schema", {})

    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Validate *actual* against the configured JSON Schema."""
        # Try jsonschema first
        try:
            import jsonschema  # type: ignore[import-untyped]
            jsonschema.validate(instance=actual, schema=self.schema)
            return VerificationResult(
                verdict=Verdict.PASS,
                score=1.0,
                details={"schema_validated": True, "validator": "jsonschema"},
                feedback="JSON instance is valid against the schema.",
            )
        except Exception as exc:
            if type(exc).__module__ == "jsonschema.exceptions":
                return VerificationResult(
                    verdict=Verdict.FAIL,
                    score=0.0,
                    details={
                        "schema_validated": False,
                        "validator": "jsonschema",
                        "error": str(exc),
                    },
                    feedback=f"Schema validation failed: {exc}",
                )
            # jsonschema not available — use fallback
            pass

        # Fallback: type check for primitives + dict + required keys
        schema_type = self.schema.get("type")
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        if schema_type in type_map:
            expected_type = type_map[schema_type]
            if not isinstance(actual, expected_type):
                return VerificationResult(
                    verdict=Verdict.FAIL,
                    score=0.0,
                    details={"validator": "fallback", "reason": f"expected {schema_type}, got {type(actual).__name__}"},
                    feedback=f"Expected {schema_type}, got {type(actual).__name__} (fallback check).",
                )

        if not isinstance(actual, dict):
            # Primitive type matched (or no type specified) — pass for primitives
            if schema_type in type_map and isinstance(actual, type_map[schema_type]):
                return VerificationResult(
                    verdict=Verdict.PASS,
                    score=1.0,
                    details={"validator": "fallback", "type_matched": schema_type},
                    feedback=f"Primitive type '{schema_type}' matched (fallback check).",
                )
            return VerificationResult(
                verdict=Verdict.FAIL,
                score=0.0,
                details={"validator": "fallback", "reason": "actual is not a dict"},
                feedback="JSON instance is not a dictionary (fallback check).",
            )

        required_keys = self.schema.get("required", [])
        if required_keys:
            missing = [k for k in required_keys if k not in actual]
            if missing:
                return VerificationResult(
                    verdict=Verdict.FAIL,
                    score=0.0,
                    details={
                        "validator": "fallback",
                        "missing_keys": missing,
                    },
                    feedback=f"Missing required keys (fallback): {missing}",
                )

        return VerificationResult(
            verdict=Verdict.PASS,
            score=1.0,
            details={"schema_validated": True, "validator": "fallback"},
            feedback="JSON instance passes fallback schema check.",
        )


class LLMJudgeVerifier(Verifier):
    """Verifier that delegates judgment to an external LLM judge function.

    Configuration keys:
        * ``judge_fn`` (Callable) — a callable with signature
          ``(expected, actual, context) -> VerificationResult``.

    Raises:
        ValueError: If ``judge_fn`` is not provided or is not callable.
    """

    def __init__(self, name: str = "", config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(name=name or "llm_judge", config=config)
        judge_fn = self.config.get("judge_fn")
        if judge_fn is None:
            raise ValueError(
                "LLMJudgeVerifier requires 'judge_fn' in config"
            )
        if not callable(judge_fn):
            raise ValueError(
                f"LLMJudgeVerifier 'judge_fn' must be callable, got {type(judge_fn).__name__}"
            )
        self._judge_fn: Callable = judge_fn

    def verify(
        self,
        expected: Any,
        actual: Any,
        context: Optional[Dict[str, Any]] = None,
    ) -> VerificationResult:
        """Delegate to the configured judge function."""
        return self._judge_fn(expected, actual, context)
