"""Tests for harness.verifiers.builtin module."""
from __future__ import annotations

import pytest

from harness.core.types import Verdict
from harness.verifiers.builtin import ExactVerifier, FuzzyVerifier, JsonSchemaVerifier


class TestExactVerifier:
    """Test ExactVerifier."""

    def test_exact_pass_equal_strings(self):
        v = ExactVerifier()
        result = v.verify("hello", "hello")
        assert result.verdict == Verdict.PASS
        assert result.score == 1.0

    def test_exact_pass_equal_ints(self):
        v = ExactVerifier()
        result = v.verify(42, 42)
        assert result.verdict == Verdict.PASS

    def test_exact_fail_different_strings(self):
        v = ExactVerifier()
        result = v.verify("hello", "world")
        assert result.verdict == Verdict.FAIL
        assert result.score == 0.0

    def test_exact_fail_different_types(self):
        v = ExactVerifier()
        result = v.verify("42", 42)
        assert result.verdict == Verdict.FAIL

    def test_exact_pass_equal_dicts(self):
        v = ExactVerifier()
        result = v.verify({"a": 1}, {"a": 1})
        assert result.verdict == Verdict.PASS

    def test_exact_fail_different_dicts(self):
        v = ExactVerifier()
        result = v.verify({"a": 1}, {"a": 2})
        assert result.verdict == Verdict.FAIL


class TestFuzzyVerifier:
    """Test FuzzyVerifier."""

    def test_fuzzy_pass_identical(self):
        v = FuzzyVerifier()
        result = v.verify("hello world", "hello world")
        assert result.verdict == Verdict.PASS
        assert result.score == 1.0

    def test_fuzzy_pass_similar(self):
        v = FuzzyVerifier(config={"threshold": 0.3})
        result = v.verify("hello world", "hello world extra")
        # High similarity strings should pass with low threshold
        assert result.score > 0.5

    def test_fuzzy_fail_different(self):
        v = FuzzyVerifier()
        result = v.verify("completely different", "nothing alike")
        assert result.verdict == Verdict.FAIL

    def test_fuzzy_empty_strings(self):
        v = FuzzyVerifier()
        result = v.verify("", "")
        assert result.score == 1.0

    def test_fuzzy_case_insensitive(self):
        v = FuzzyVerifier(config={"case_sensitive": False})
        result = v.verify("Hello", "hello")
        assert result.score == 1.0

    def test_fuzzy_configure_threshold(self):
        v = FuzzyVerifier(config={"threshold": 0.3})
        assert v.threshold == 0.3


class TestJsonSchemaVerifier:
    """Test JsonSchemaVerifier."""

    def test_schema_valid_object(self):
        v = JsonSchemaVerifier(config={"schema": {"type": "object", "required": ["name"]}})
        result = v.verify(None, {"name": "Alice", "age": 30})
        assert result.verdict == Verdict.PASS

    def test_schema_invalid_type(self):
        v = JsonSchemaVerifier(config={"schema": {"type": "string"}})
        result = v.verify(None, 123)
        assert result.verdict == Verdict.FAIL

    def test_schema_missing_required_fallback(self):
        v = JsonSchemaVerifier(config={"schema": {"type": "object", "required": ["name"]}})
        result = v.verify(None, {})
        assert result.verdict == Verdict.FAIL

    def test_schema_valid_number(self):
        v = JsonSchemaVerifier(config={"schema": {"type": "number"}})
        result = v.verify(None, 3.14)
        assert result.verdict == Verdict.PASS

    def test_schema_valid_boolean(self):
        v = JsonSchemaVerifier(config={"schema": {"type": "boolean"}})
        result = v.verify(None, True)
        assert result.verdict == Verdict.PASS

    def test_schema_invalid_boolean(self):
        v = JsonSchemaVerifier(config={"schema": {"type": "boolean"}})
        result = v.verify(None, "true")
        assert result.verdict == Verdict.FAIL

    def test_feedback_present(self):
        v = ExactVerifier()
        result = v.verify("a", "b")
        assert result.feedback != ""
        assert "details" in result.details or result.details is not None

    def test_details_present(self):
        v = ExactVerifier()
        result = v.verify("a", "a")
        assert result.details is not None

    def test_verifier_name(self):
        v = ExactVerifier(name="my_verifier")
        assert v.name == "my_verifier"

    def test_verifier_config(self):
        v = FuzzyVerifier(config={"threshold": 0.5})
        assert v.config["threshold"] == 0.5
