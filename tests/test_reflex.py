"""Tests for the harness reflex (System 1) layer.

All tests use the deterministic :class:`MockReflexBackend`; the only
network-adjacent test targets an unreachable endpoint to exercise the
:class:`JevBackend` graceful-degradation path.
"""

from __future__ import annotations

import pytest

from harness.acceptance import GateResult
from harness.core.types import Verdict
from harness.reflex import (
    AuditFinding,
    EscalationEvent,
    EscalationPolicy,
    JevBackend,
    MockReflexBackend,
    QualitativeLinter,
    ReflexCIRunner,
    ReflexGate,
    ReflexPrimitives,
    ReflexiveVerifier,
    ReflexResult,
    ReflexRouter,
    RoutableSkill,
    Rubric,
    RubricRegistry,
)
from harness.reflex.linter import (
    DIFF_RISK_RUBRIC,
    N_PLUS_ONE_RUBRIC,
    OWASP_SEVERITY_RUBRIC,
    SIDE_EFFECT_RUBRIC,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def backend() -> MockReflexBackend:
    return MockReflexBackend()


@pytest.fixture()
def primitives(backend) -> ReflexPrimitives:
    return ReflexPrimitives(backend)


@pytest.fixture()
def linter(primitives) -> QualitativeLinter:
    return QualitativeLinter(primitives)


def _make_rubric(**overrides) -> Rubric:
    fields = {
        "rubric_id": "test-rubric",
        "name": "test_rubric",
        "keyword_signals": {"alpha": 3.0, "beta": 5.0},
    }
    fields.update(overrides)
    return Rubric(**fields)


# ---------------------------------------------------------------------------
# 1. MockReflexBackend determinism
# ---------------------------------------------------------------------------


class TestMockBackendDeterminism:
    def test_bool_check_deterministic_twice(self, backend):
        first = backend.bool_check("q?", "some context with a secret")
        second = backend.bool_check("q?", "some context with a secret")
        assert first == second

    def test_score_check_deterministic_twice(self, backend):
        rubric = _make_rubric()
        assert backend.score_check("alpha beta", rubric) == backend.score_check(
            "alpha beta", rubric
        )

    def test_choice_check_deterministic_twice(self, backend):
        options = [{"name": "a", "description": "alpha"}, {"name": "b", "description": "beta"}]
        assert backend.choice_check("alpha input", options) == backend.choice_check(
            "alpha input", options
        )

    def test_latency_simulation_present(self, backend):
        backend.bool_check("q?", "ctx")
        assert backend.last_latency_ms == pytest.approx(115.0)


# ---------------------------------------------------------------------------
# 2. bool_check suspicious / clean contexts
# ---------------------------------------------------------------------------


class TestBoolCheck:
    @pytest.mark.parametrize(
        "keyword",
        ["secret", "password", "token", "eval(", "exec(", "drop table"],
    )
    def test_suspicious_keywords_return_high_probability(self, backend, keyword):
        probability = backend.bool_check("Is this risky?", f"context has {keyword} here")
        assert probability == pytest.approx(0.96)

    def test_clean_context_returns_low_probability(self, backend):
        probability = backend.bool_check("Is this risky?", "a perfectly normal log line")
        assert probability == pytest.approx(0.02)

    def test_probability_in_unit_interval(self, backend):
        for ctx in ["secret", "clean"]:
            assert 0.0 <= backend.bool_check("q?", ctx) <= 1.0


# ---------------------------------------------------------------------------
# 3. score_check rubric clamping to [0, 10]
# ---------------------------------------------------------------------------


class TestScoreCheck:
    def test_signals_sum(self, backend):
        rubric = _make_rubric()
        assert backend.score_check("alpha and beta", rubric) == pytest.approx(8.0)

    def test_clamps_to_ten(self, backend):
        rubric = _make_rubric(keyword_signals={"alpha": 8.0, "beta": 9.0})
        assert backend.score_check("alpha beta", rubric) == pytest.approx(10.0)

    def test_clamps_to_zero(self, backend):
        rubric = _make_rubric(keyword_signals={"alpha": -50.0})
        assert backend.score_check("alpha", rubric) == pytest.approx(0.0)

    def test_no_signal_scores_zero(self, backend):
        rubric = _make_rubric()
        assert backend.score_check("nothing matching", rubric) == pytest.approx(0.0)

    def test_case_insensitive_signals(self, backend):
        rubric = _make_rubric()
        assert backend.score_check("ALPHA BETA", rubric) == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# 4-5. choice_check distribution properties
# ---------------------------------------------------------------------------


class TestChoiceCheck:
    OPTIONS = [
        {"name": "sql", "description": "database query select"},
        {"name": "ui", "description": "react component render"},
        {"name": "docs", "description": "documentation writing"},
    ]

    def test_probabilities_sum_to_one(self, backend):
        probabilities = backend.choice_check("database select query", self.OPTIONS)
        assert sum(probabilities.values()) == pytest.approx(1.0)

    def test_returns_all_options(self, backend):
        probabilities = backend.choice_check("anything", self.OPTIONS)
        assert set(probabilities) == {"sql", "ui", "docs"}

    def test_best_match_wins(self, backend):
        probabilities = backend.choice_check("database select query", self.OPTIONS)
        top = max(probabilities, key=probabilities.get)
        assert top == "sql"

    def test_empty_options_returns_empty_mapping(self, backend):
        assert backend.choice_check("anything", []) == {}


# ---------------------------------------------------------------------------
# 6. ReflexResult escalation flags
# ---------------------------------------------------------------------------


class TestReflexResultEscalation:
    def test_score_above_threshold_escalates(self, primitives):
        rubric = _make_rubric(keyword_signals={"alpha": 9.0}, escalation_threshold=7.0)
        result = primitives.score_gate("alpha", rubric)
        assert result.escalate is True
        assert result.primitive == "score"
        assert result.value == pytest.approx(9.0)

    def test_score_below_threshold_does_not_escalate(self, primitives):
        rubric = _make_rubric(keyword_signals={"alpha": 1.0})
        result = primitives.score_gate("alpha", rubric)
        assert result.escalate is False

    def test_low_confidence_choice_escalates(self, primitives):
        options = [
            {"name": f"opt{i}", "description": f"unrelated{i} zzz{i}"}
            for i in range(4)
        ]
        result = primitives.route("nothing overlapping qqq", options, confidence_floor=0.5)
        assert result.confidence < 0.5
        assert result.escalate is True

    def test_high_confidence_choice_does_not_escalate(self, primitives):
        options = [
            {"name": "match", "description": "database select query from table"},
            {"name": "other", "description": "completely different zzz"},
        ]
        result = primitives.route("database select query", options)
        assert result.value == "match"
        assert result.escalate is False

    def test_bool_gate_blocks_above_threshold(self, primitives):
        result = primitives.bool_gate("Leak?", "context with password", block_threshold=0.85)
        assert result.escalate is True
        assert result.detail["action"] == "BLOCK"

    def test_bool_gate_passes_clean(self, primitives):
        result = primitives.bool_gate("Leak?", "boring context", block_threshold=0.85)
        assert result.escalate is False
        assert result.detail["action"] == "PASS"

    def test_result_fields_present(self, primitives):
        result = primitives.bool_gate("q?", "ctx")
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms > 0
        assert 0.0 <= result.confidence <= 1.0
        assert isinstance(result.detail, dict)


# ---------------------------------------------------------------------------
# 7. EscalationPolicy stats accumulation + reset
# ---------------------------------------------------------------------------


class TestEscalationPolicy:
    def test_stats_accumulate(self):
        policy = EscalationPolicy()
        primitives = ReflexPrimitives(MockReflexBackend(), escalation=policy)
        rubric = _make_rubric(keyword_signals={"alpha": 9.0})
        primitives.score_gate("alpha", rubric)  # escalates (threshold)
        primitives.bool_gate("q?", "clean context")  # no escalation
        stats = policy.stats()
        assert stats["total_checks"] == 2
        assert stats["escalations"] == 1
        assert stats["escalation_rate"] == pytest.approx(0.5)
        assert stats["by_reason"]["threshold_exceeded"] == 1

    def test_low_confidence_reason_recorded(self):
        policy = EscalationPolicy(default_confidence_floor=0.9)
        primitives = ReflexPrimitives(MockReflexBackend(), escalation=policy)
        options = [
            {"name": "a", "description": "alpha beta gamma"},
            {"name": "b", "description": "alpha beta delta"},
        ]
        primitives.route("alpha beta", options)
        stats = policy.stats()
        assert stats["by_reason"]["low_confidence"] == 1

    def test_reset_clears_stats(self):
        policy = EscalationPolicy()
        primitives = ReflexPrimitives(MockReflexBackend(), escalation=policy)
        primitives.bool_gate("q?", "secret")
        policy.reset()
        stats = policy.stats()
        assert stats["total_checks"] == 0
        assert stats["escalations"] == 0
        assert stats["escalation_rate"] == 0.0
        assert all(count == 0 for count in stats["by_reason"].values())

    def test_manual_event_record(self):
        policy = EscalationPolicy()
        policy.record(
            EscalationEvent(
                rule="custom", reason="novel_pattern", value=1.0, threshold=0.5
            )
        )
        stats = policy.stats()
        assert stats["escalations"] == 1
        assert stats["by_reason"]["novel_pattern"] == 1

    def test_event_timestamp_populated(self):
        event = EscalationEvent(rule="r", reason="low_confidence", value=0.1, threshold=0.5)
        assert event.timestamp is not None


# ---------------------------------------------------------------------------
# 8-10. Rubrics and registry
# ---------------------------------------------------------------------------


class TestRubrics:
    def test_propose_update_sets_parent_version(self):
        registry = RubricRegistry()
        registry.register(_make_rubric(name="rx", version="1.0.0"))
        updated = registry.propose_update("rx", motivation="tighten signals")
        assert updated.parent_version == "1.0.0"
        assert updated.motivation == "tighten signals"
        assert updated.version == "1.0.1"

    def test_propose_update_applies_changes(self):
        registry = RubricRegistry()
        registry.register(_make_rubric(name="rx"))
        updated = registry.propose_update(
            "rx", "raise ceiling", escalation_threshold=8.0
        )
        assert updated.escalation_threshold == pytest.approx(8.0)

    def test_registry_get_returns_latest(self):
        registry = RubricRegistry()
        registry.register(_make_rubric(name="rx", version="1.0.0"))
        registry.register(_make_rubric(name="rx", version="2.0.0"))
        assert registry.get("rx").version == "2.0.0"

    def test_registry_history_returns_all_versions(self):
        registry = RubricRegistry()
        registry.register(_make_rubric(name="rx", version="1.0.0"))
        registry.register(_make_rubric(name="rx", version="2.0.0"))
        history = registry.history("rx")
        assert [r.version for r in history] == ["1.0.0", "2.0.0"]

    def test_registry_missing_name_raises(self):
        registry = RubricRegistry()
        with pytest.raises(KeyError):
            registry.get("nope")
        with pytest.raises(KeyError):
            registry.history("nope")

    def test_criteria_max_eleven_allowed(self):
        rubric = _make_rubric(criteria=[f"c{i}" for i in range(11)])
        assert len(rubric.criteria) == 11

    def test_criteria_twelve_rejected(self):
        with pytest.raises(ValueError):
            _make_rubric(criteria=[f"c{i}" for i in range(12)])

    def test_propose_update_unknown_field_rejected(self):
        registry = RubricRegistry()
        registry.register(_make_rubric(name="rx"))
        with pytest.raises(ValueError):
            registry.propose_update("rx", "bad", not_a_field=1)


# ---------------------------------------------------------------------------
# 11. Linter rule 1: log data leakage
# ---------------------------------------------------------------------------


class TestLinterRule1:
    def test_secret_in_log_blocks(self, linter):
        result = linter.evaluate_log_data_leakage(
            "logger.debug(f'Authenticating request with Bearer Token: {user_jwt_secret}')"
        )
        assert result.primitive == "bool"
        assert result.escalate is True
        assert result.detail["action"] == "BLOCK_PR_CRITICAL_LEAK"

    def test_clean_log_passes(self, linter):
        result = linter.evaluate_log_data_leakage(
            "logger.info(f'User {user_id} logged in successfully from {ip_address}')"
        )
        assert result.escalate is False
        assert result.detail["action"] == "PASS"


# ---------------------------------------------------------------------------
# 12. Linter rule 2: function intent
# ---------------------------------------------------------------------------


class TestLinterRule2:
    def test_getter_with_delete_scores_high(self, linter):
        result = linter.evaluate_function_intent(
            "def get_user_profile(user_id):\n"
            "    delete_user_data(user_id)\n"
            "    return cache.get(user_id)"
        )
        assert result.primitive == "score"
        assert result.value >= 7.0
        assert result.detail["status"] == "REJECT_UNNAMED_SIDE_EFFECTS"

    def test_calculator_with_send_email_scores_high(self, linter):
        result = linter.evaluate_function_intent(
            "def calculate_invoice(user):\n    send_email(user)\n    return total"
        )
        assert result.value >= 7.0
        assert result.escalate is True

    def test_pure_calculation_passes(self, linter):
        result = linter.evaluate_function_intent(
            "def calculate_total_discount(cart):\n"
            "    return sum(item.price * item.discount for item in cart)"
        )
        assert result.value < 7.0
        assert result.detail["status"] == "PASS"


# ---------------------------------------------------------------------------
# 13-14. Linter rule 3: comment quality
# ---------------------------------------------------------------------------


class TestLinterRule3:
    def test_multiply_comment_is_redundant_noise(self, linter):
        result = linter.evaluate_comment_quality("# Multiply the value by 2", "val = val * 2")
        assert result.primitive == "choice"
        assert result.value == "obvious_redundant_noise"
        assert result.detail["action"] == "AUTO_STRIP"

    def test_todo_hack_comment_is_misleading(self, linter):
        result = linter.evaluate_comment_quality(
            "# TODO: this is a hack, fix later", "x = compute()"
        )
        assert result.value == "misleading_outdated"
        assert result.detail["action"] == "AUTO_STRIP"

    def test_helpful_comment_kept(self, linter):
        result = linter.evaluate_comment_quality(
            "# Re-establish DB connection pool if TCP socket timed out",
            "if not db.is_alive(): db.reconnect()",
        )
        assert result.detail["action"] == "KEEP"


# ---------------------------------------------------------------------------
# 15. Linter rule 4: diff pre-screening
# ---------------------------------------------------------------------------


class TestLinterRule4:
    def test_eval_diff_forwarded_to_system2(self, linter):
        result = linter.pre_screen_diff_security("+ result = eval(user_untrusted_input)")
        assert result.detail["forward_to_system2"] is True
        assert result.escalate is True

    def test_benign_diff_filtered(self, linter):
        result = linter.pre_screen_diff_security(
            "+ formatted_name = f'{first_name} {last_name}'"
        )
        assert result.detail["forward_to_system2"] is False


# ---------------------------------------------------------------------------
# 16-17. Linter rule 5: N+1 ORM
# ---------------------------------------------------------------------------


class TestLinterRule5:
    def test_loop_with_get_flags_n_plus_one(self, linter):
        result = linter.evaluate_n_plus_one_orm(
            "orders = Order.objects.filter(status='PENDING')\n"
            "for order in orders:\n"
            "    user = User.objects.get(id=order.user_id)"
        )
        assert result.value > 6.0
        assert result.detail["status"] == "FLAG_N_PLUS_ONE_SMELL"
        assert "eager loading" in result.detail["recommendation"]

    def test_select_related_eager_load_passes(self, linter):
        result = linter.evaluate_n_plus_one_orm(
            "orders = Order.objects.select_related('user').filter(status='PENDING')\n"
            "for order in orders:\n"
            "    print(order.user.email)"
        )
        assert result.value <= 6.0
        assert result.detail["status"] == "PASS"


# ---------------------------------------------------------------------------
# 18-20. Linter rule 6: OWASP scan
# ---------------------------------------------------------------------------


class TestLinterRule6:
    def test_fstring_sql_flagged_sqli_high_severity(self, linter):
        result = linter.evaluate_security_vulnerabilities(
            "query = f\"SELECT * FROM users WHERE email = '{req.query['email']}'\""
        )
        assert result.value == "owasp_a03_sql_injection"
        assert result.detail["severity_score"] > 7.0
        assert result.detail["status"] == "BLOCK_PR_SECURITY_VULNERABILITY"
        assert "parameterized" in result.detail["recommendation"]

    def test_eval_flagged_remote_code_execution(self, linter):
        result = linter.evaluate_security_vulnerabilities(
            "result = eval(user_untrusted_input)"
        )
        assert result.value == "owasp_a03_remote_code_execution"
        assert result.detail["severity_score"] > 7.0

    def test_admin_route_flagged_broken_access_control(self, linter):
        result = linter.evaluate_security_vulnerabilities(
            "@app.route('/admin/delete_user')\n"
            "def admin_delete_user():\n"
            "    db.delete_user(req.query['id'])"
        )
        assert result.value == "owasp_a01_broken_access_control"

    def test_clean_snippet_passes(self, linter):
        result = linter.evaluate_security_vulnerabilities(
            "def sanitize_input(user_str):\n    return html.escape(user_str)"
        )
        assert result.value == "clean"
        assert result.detail["status"] == "PASS"
        assert result.escalate is False


# ---------------------------------------------------------------------------
# 21. run_full_audit
# ---------------------------------------------------------------------------


class TestFullAudit:
    def test_returns_all_six_rule_results(self, linter):
        results = linter.run_full_audit("x = 1")
        assert len(results) == 6
        for key in (
            "log_data_leakage",
            "function_intent",
            "comment_quality",
            "diff_security_pre_screen",
            "n_plus_one_orm",
            "security_vulnerabilities",
        ):
            assert key in results
            assert isinstance(results[key], ReflexResult)

    def test_audit_on_malicious_snippet_escalates(self, linter):
        results = linter.run_full_audit("result = eval(untrusted)")
        assert results["security_vulnerabilities"].escalate is True

    def test_linter_registers_rubrics_in_registry(self, primitives):
        registry = RubricRegistry()
        QualitativeLinter(primitives, registry=registry)
        assert registry.get("n_plus_one_orm").rubric_id == N_PLUS_ONE_RUBRIC.rubric_id
        assert len(registry.names()) == 4


# ---------------------------------------------------------------------------
# 22-26. Router
# ---------------------------------------------------------------------------


def _skill(name: str, description: str, words: int = 20) -> RoutableSkill:
    return RoutableSkill(
        name=name,
        description=description,
        instructions=" ".join(["instruction"] * words),
    )


class TestReflexRouter:
    def test_register_255_skills_ok_256th_raises(self, primitives):
        router = ReflexRouter(primitives)
        for i in range(255):
            router.register_skill(_skill(f"skill-{i}", f"handler {i}"))
        assert len(router.skills) == 255
        with pytest.raises(ValueError):
            router.register_skill(_skill("skill-256", "overflow"))

    def test_matching_keywords_route_to_correct_skill(self, primitives):
        router = ReflexRouter(primitives)
        router.register_skill(
            _skill("git-workflow", "Git operations branch pull request creation")
        )
        router.register_skill(
            _skill("react-component-builder", "Builds accessible React UI components")
        )
        router.register_skill(
            _skill("database-migration", "Validates SQL database schemas migration")
        )
        decision = router.route("Build a new accessible React dropdown component")
        assert decision.skill_name == "react-component-builder"
        assert decision.probability > 0.5

    def test_token_savings_positive_with_multiple_skills(self, primitives):
        router = ReflexRouter(primitives)
        router.register_skill(_skill("a", "alpha beta", words=100))
        router.register_skill(_skill("b", "gamma delta", words=100))
        router.register_skill(_skill("c", "epsilon zeta", words=100))
        decision = router.route("alpha beta")
        assert decision.token_savings_estimate > 0

    def test_empty_catalog_escalates_with_none(self, primitives):
        router = ReflexRouter(primitives)
        decision = router.route("anything at all")
        assert decision.skill_name == "none"
        assert decision.escalate is True
        assert decision.probabilities == {}

    def test_low_confidence_routing_escalates(self, primitives):
        router = ReflexRouter(primitives, confidence_floor=0.5)
        router.register_skill(_skill("a", "alpha"))
        router.register_skill(_skill("b", "beta"))
        router.register_skill(_skill("c", "gamma"))
        router.register_skill(_skill("d", "delta"))
        decision = router.route("zzz qqq no overlap whatsoever")
        assert decision.escalate is True
        assert decision.probability < 0.5

    def test_probabilities_cover_catalog(self, primitives):
        router = ReflexRouter(primitives)
        router.register_skill(_skill("a", "alpha"))
        router.register_skill(_skill("b", "beta"))
        decision = router.route("alpha")
        assert set(decision.probabilities) == {"a", "b"}
        assert sum(decision.probabilities.values()) == pytest.approx(1.0)

    def test_routing_latency_present(self, primitives):
        router = ReflexRouter(primitives)
        router.register_skill(_skill("a", "alpha"))
        decision = router.route("alpha")
        assert decision.latency_ms > 0

    def test_trust_level_defaults_community(self):
        skill = RoutableSkill(name="x", description="d", instructions="i")
        assert skill.trust_level == "community"


# ---------------------------------------------------------------------------
# 27-28. ReflexGate
# ---------------------------------------------------------------------------


class TestReflexGate:
    def _gate(self, results) -> ReflexGate:
        checks = [
            (f"check-{i}", (lambda evidence, r=r: r))
            for i, r in enumerate(results)
        ]
        return ReflexGate(checks)

    def test_all_pass_checks_gate_passes(self, primitives):
        passing = primitives.bool_gate("q?", "clean context")
        gate = self._gate([passing])
        report = gate.evaluate(None, None, {})
        assert report.result == GateResult.PASS
        assert report.details["check_count"] == 1
        assert report.details["escalations"] == []

    def test_blocking_check_gate_fails(self, primitives):
        blocking = primitives.bool_gate("q?", "context with secret")
        passing = primitives.bool_gate("q?", "clean context")
        gate = self._gate([blocking, passing])
        report = gate.evaluate(None, None, {})
        assert report.result == GateResult.FAIL
        assert len(report.details["escalations"]) == 1
        assert report.details["escalations"][0]["check"] == "check-0"

    def test_gate_uses_acceptance_suite_interface(self, primitives):
        from harness.acceptance import AcceptanceSuite

        gate = self._gate([primitives.bool_gate("q?", "clean")])
        suite = AcceptanceSuite([gate])
        reports = suite.evaluate_all(None, None, {})
        assert suite.can_promote(reports) is True


# ---------------------------------------------------------------------------
# 29. ReflexiveVerifier
# ---------------------------------------------------------------------------


class TestReflexiveVerifier:
    def _verifier(self, primitives) -> ReflexiveVerifier:
        rubric = Rubric(
            rubric_id="smell-v1",
            name="smell_rubric",
            keyword_signals={"password": 9.0, "eval(": 9.0},
            pass_threshold=2.0,
        )
        return ReflexiveVerifier(primitives, rubric)

    def test_clean_actual_passes(self, primitives):
        verifier = self._verifier(primitives)
        result = verifier.verify(expected="anything", actual="def add(a, b): return a + b")
        assert result.verdict == Verdict.PASS
        assert result.score < 0.2

    def test_smelly_actual_fails_with_score(self, primitives):
        verifier = self._verifier(primitives)
        result = verifier.verify(
            expected="clean", actual="def login(): store(password); eval(payload)"
        )
        assert result.verdict == Verdict.FAIL
        assert result.score > 0.5
        assert result.details["raw_score"] >= 2.0
        assert "pass threshold" in result.feedback

    def test_verifier_has_name(self, primitives):
        assert self._verifier(primitives).name == "reflexive"


# ---------------------------------------------------------------------------
# 30-34. CI runner
# ---------------------------------------------------------------------------


class TestReflexCIRunner:
    SQLI_SNIPPET = {
        "file": "src/controllers/userController.py",
        "content": "query = f\"SELECT * FROM users WHERE email = '{req.query['email']}'\"",
    }
    N_PLUS_ONE_SNIPPET = {
        "file": "src/services/orderService.py",
        "content": (
            "orders = Order.objects.filter(status='PENDING')\n"
            "for order in orders:\n"
            "    user = User.objects.get(id=order.user_id)"
        ),
    }
    LEAK_SNIPPET = {
        "file": "src/utils/logger.py",
        "content": "logger.debug(f'Authenticating request with Bearer Token: {user_jwt_secret}')",
    }

    def test_clean_snippets_pass_no_findings(self, linter):
        runner = ReflexCIRunner(linter)
        result = runner.run(
            [{"file": "ok.py", "content": "def add(a, b):\n    return a + b"}]
        )
        assert result["passed"] is True
        assert result["findings"] == []
        assert result["patches_applied"] == 0

    def test_sqli_snippet_produces_finding_with_recommendation(self, linter):
        runner = ReflexCIRunner(linter)
        findings = runner.scan([self.SQLI_SNIPPET])
        owasp = [f for f in findings if f.rule == "owasp_security_scan"]
        assert len(owasp) == 1
        assert isinstance(owasp[0], AuditFinding)
        assert owasp[0].severity > 7.0
        assert "parameterized" in owasp[0].recommendation

    def test_full_loop_scan_remediate_reverify_passes(self, linter):
        runner = ReflexCIRunner(linter)
        result = runner.run(
            [self.SQLI_SNIPPET, self.N_PLUS_ONE_SNIPPET, self.LEAK_SNIPPET]
        )
        assert result["passed"] is True
        assert len(result["findings"]) == 3
        assert result["patches_applied"] == 3
        assert result["reverified"] == 3

    def test_summary_markdown_contains_table_header(self, linter):
        runner = ReflexCIRunner(linter)
        result = runner.run([self.SQLI_SNIPPET])
        assert (
            "| File | Rule Triggered | Auto-Remediation Status | Post-Fix Verification |"
            in result["summary_markdown"]
        )

    def test_failed_remediation_fails_pipeline(self, linter):
        # Remediation callable returns the unchanged bad snippet.
        runner = ReflexCIRunner(linter, remediate=lambda finding: finding.snippet)
        result = runner.run([self.SQLI_SNIPPET])
        assert result["passed"] is False
        assert result["reverified"] == 0
        assert "REMEDIATION_FAILED" in result["summary_markdown"]

    def test_scan_flags_all_three_classes(self, linter):
        runner = ReflexCIRunner(linter)
        findings = runner.scan(
            [self.SQLI_SNIPPET, self.N_PLUS_ONE_SNIPPET, self.LEAK_SNIPPET]
        )
        rules = {f.rule for f in findings}
        assert rules == {
            "owasp_security_scan",
            "n_plus_one_orm_audit",
            "secret_leak_protection",
        }


# ---------------------------------------------------------------------------
# 35. JevBackend fallback
# ---------------------------------------------------------------------------


class TestJevBackend:
    UNREACHABLE = "http://127.0.0.1:1"

    def test_bool_falls_back_to_mock_on_unreachable_endpoint(self):
        backend = JevBackend(api_key="test-key", base_url=self.UNREACHABLE, timeout=1.0)
        probability = backend.bool_check("Leak?", "context with secret")
        assert backend.used_fallback is True
        # Fallback is the deterministic mock.
        assert probability == pytest.approx(0.96)

    def test_score_falls_back_to_mock(self):
        backend = JevBackend(api_key="test-key", base_url=self.UNREACHABLE, timeout=1.0)
        rubric = _make_rubric(keyword_signals={"alpha": 4.0})
        assert backend.score_check("alpha", rubric) == pytest.approx(4.0)
        assert backend.used_fallback is True

    def test_choice_falls_back_to_mock(self):
        backend = JevBackend(api_key="test-key", base_url=self.UNREACHABLE, timeout=1.0)
        options = [{"name": "a", "description": "alpha"}, {"name": "b", "description": "beta"}]
        probabilities = backend.choice_check("alpha", options)
        assert backend.used_fallback is True
        assert sum(probabilities.values()) == pytest.approx(1.0)

    def test_api_key_defaults_to_env(self, monkeypatch):
        monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")
        backend = JevBackend(base_url=self.UNREACHABLE)
        assert backend.api_key == "env-key"

    def test_used_fallback_initially_false(self):
        backend = JevBackend(base_url=self.UNREACHABLE)
        assert backend.used_fallback is False


# ---------------------------------------------------------------------------
# 36+. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_context_bool_check(self, backend):
        assert backend.bool_check("q?", "") == pytest.approx(0.02)

    def test_empty_input_score_check(self, backend):
        assert backend.score_check("", _make_rubric()) == pytest.approx(0.0)

    def test_empty_input_choice_check(self, backend):
        options = [{"name": "a", "description": "alpha"}]
        probabilities = backend.choice_check("", options)
        assert probabilities == {"a": pytest.approx(1.0)}

    def test_unicode_inputs(self, backend, primitives):
        rubric = _make_rubric(keyword_signals={"密码": 5.0, "alpha": 3.0})
        assert backend.score_check("密码 detected", rubric) == pytest.approx(5.0)
        result = primitives.bool_gate("q?", "日本語のコンテキスト")
        assert 0.0 <= result.value <= 1.0
        options = [
            {"name": "emoji", "description": "emoji handling 絵文字"},
            {"name": "plain", "description": "plain text"},
        ]
        assert sum(backend.choice_check("絵文字 input", options).values()) == pytest.approx(1.0)

    def test_very_long_inputs(self, backend):
        long_text = "clean word " * 10000
        assert backend.score_check(long_text, _make_rubric()) == pytest.approx(0.0)
        assert backend.bool_check("q?", long_text + " secret") == pytest.approx(0.96)
        options = [{"name": "a", "description": "alpha"}, {"name": "b", "description": "beta"}]
        assert sum(backend.choice_check(long_text + " alpha", options).values()) == pytest.approx(1.0)

    def test_score_exactly_at_escalation_threshold_escalates(self, primitives):
        rubric = _make_rubric(
            keyword_signals={"alpha": 7.0}, escalation_threshold=7.0
        )
        result = primitives.score_gate("alpha", rubric)
        assert result.value == pytest.approx(7.0)
        assert result.escalate is True

    def test_score_exactly_at_pass_threshold_not_passing(self, primitives):
        rubric = _make_rubric(keyword_signals={"alpha": 2.0}, pass_threshold=2.0)
        result = primitives.score_gate("alpha", rubric)
        assert result.value == pytest.approx(2.0)
        assert result.detail["passes"] is False

    def test_verifier_boundary_score_two_fails(self, primitives):
        rubric = Rubric(
            rubric_id="boundary",
            name="boundary",
            keyword_signals={"alpha": 2.0},
            pass_threshold=2.0,
        )
        verifier = ReflexiveVerifier(primitives, rubric)
        result = verifier.verify(None, "alpha")
        assert result.verdict == Verdict.FAIL
        assert result.score == pytest.approx(0.2)

    def test_choice_tie_breaking_deterministic(self, backend, primitives):
        # Identical overlap scores: first registered option wins, every time.
        options = [
            {"name": "first", "description": "zzz"},
            {"name": "second", "description": "qqq"},
        ]
        winners = {
            primitives.route("no overlap here", options).value for _ in range(5)
        }
        assert winners == {"first"}
        assert backend.choice_check("x", options) == backend.choice_check("x", options)

    def test_latency_fields_on_all_primitives(self, primitives):
        rubric = _make_rubric()
        results = [
            primitives.bool_gate("q?", "ctx"),
            primitives.score_gate("alpha", rubric),
            primitives.route("alpha", [{"name": "a", "description": "alpha"}]),
        ]
        for result in results:
            assert result.latency_ms == pytest.approx(115.0)

    def test_route_with_empty_options_escalates(self, primitives):
        result = primitives.route("anything", [])
        assert result.value == "none"
        assert result.escalate is True
        assert result.confidence == 0.0

    def test_none_inputs_coerced(self, primitives):
        rubric = _make_rubric()
        # Empty strings are safe no-ops.
        assert primitives.score_gate("", rubric).value == pytest.approx(0.0)

    def test_reflex_result_post_init_normalises(self):
        result = ReflexResult(primitive="bool", value=0.5, confidence=1, latency_ms=10)
        assert isinstance(result.confidence, float)
        assert isinstance(result.latency_ms, float)
        assert result.detail == {}
        assert result.escalate is False

    def test_trace_store_recording_duck_typed(self, backend):
        class FakeStore:
            def __init__(self):
                self.records = []

            def record(self, trace):
                self.records.append(trace)

        store = FakeStore()
        primitives = ReflexPrimitives(backend, trace_store=store)
        primitives.bool_gate("q?", "ctx")
        primitives.score_gate("alpha", _make_rubric())
        assert len(store.records) == 2
        assert store.records[0].scenario_id == "reflex.bool"

    def test_trace_store_failure_never_raises(self, backend):
        class BrokenStore:
            def record(self, trace):
                raise RuntimeError("boom")

        primitives = ReflexPrimitives(backend, trace_store=BrokenStore())
        result = primitives.bool_gate("q?", "ctx")
        assert result.value == pytest.approx(0.02)

    def test_score_gate_detail_carries_rubric_lineage(self, primitives):
        registry = RubricRegistry()
        registry.register(_make_rubric(name="rx", version="1.0.0"))
        updated = registry.propose_update("rx", "iteration")
        result = primitives.score_gate("alpha", updated)
        assert result.detail["rubric_version"] == "1.0.1"
        assert updated.parent_version == "1.0.0"

    def test_gate_empty_check_list_passes(self):
        gate = ReflexGate([])
        report = gate.evaluate(None, None, {})
        assert report.result == GateResult.PASS
