"""Qualitative AI linter built on reflex primitives.

Six natural-language quality rules, each mapped onto a Jev primitive
(bool / score / choice) instead of rigid inline regex logic:

1. Log data leakage & secret protection (bool).
2. Function naming & side-effect alignment (score).
3. Comment quality & garbage audit (choice, 4 categories).
4. PR diff security pre-screening (score; 10x System 2 context reduction).
5. N+1 ORM query smell audit (score).
6. OWASP security vulnerability scan (choice + severity score).

The rubrics used by the score-based rules are module-level
:class:`~harness.reflex.rubrics.Rubric` instances — editable harness
artifacts that can be versioned through a
:class:`~harness.reflex.rubrics.RubricRegistry`.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from harness.reflex.primitives import ReflexPrimitives, ReflexResult
from harness.reflex.rubrics import Rubric, RubricRegistry

__all__ = [
    "SIDE_EFFECT_RUBRIC",
    "DIFF_RISK_RUBRIC",
    "N_PLUS_ONE_RUBRIC",
    "OWASP_SEVERITY_RUBRIC",
    "COMMENT_QUALITY_OPTIONS",
    "OWASP_CHOICE_OPTIONS",
    "QualitativeLinter",
]


# ---------------------------------------------------------------------------
# Module-level rubrics (editable harness artifacts)
# ---------------------------------------------------------------------------

SIDE_EFFECT_RUBRIC = Rubric(
    rubric_id="side-effect-alignment-v1",
    name="side_effect_alignment",
    version="1.0.0",
    criteria=[
        "0-2: function name accurately describes all behaviour",
        "3-5: name omits minor side effects (caching, metrics)",
        "6-8: name hides meaningful mutations or I/O",
        "9-10: innocent name (get_/calculate_) hides severe side effects "
        "(delete, drop, send_email, write)",
    ],
    keyword_signals={
        "delete": 8.2,
        "drop": 8.2,
        "send_email": 8.2,
        "write": 7.5,
    },
    escalation_threshold=7.0,
    pass_threshold=2.0,
    motivation="Reject getter/calculator functions that hide severe side effects.",
)

DIFF_RISK_RUBRIC = Rubric(
    rubric_id="diff-security-prescreen-v1",
    name="diff_security_prescreen",
    version="1.0.0",
    criteria=[
        "0-2: benign diff, no risky primitives",
        "3-5: diff touches sensitive surfaces without obvious risk",
        "6-8: diff contains potentially dangerous calls",
        "9-10: diff contains eval/exec/DROP TABLE/chmod 777 on untrusted data",
    ],
    keyword_signals={
        "eval(": 8.6,
        "exec(": 8.6,
        "drop table": 8.6,
        "chmod 777": 8.6,
    },
    escalation_threshold=5.0,
    pass_threshold=2.0,
    motivation="Pre-screen diffs so only risky ones consume System 2 context.",
)

N_PLUS_ONE_RUBRIC = Rubric(
    rubric_id="n-plus-one-orm-v2",
    name="n_plus_one_orm",
    version="2.0.0",
    criteria=[
        "0-2: eager loaded (select_related / prefetch_related / joins / include:)",
        "3-5: loop present but no per-iteration ORM queries",
        "6-8: loop with attribute traversal, lazy-loading suspected",
        "9-10: explicit ORM queries (.get/.filter/query) inside loop bodies",
    ],
    keyword_signals={
        "for ": 3.0,
        "while ": 3.0,
        ".get(": 4.0,
        ".filter(": 3.0,
        "query(": 3.0,
        "select_related": -10.0,
        "prefetch_related": -10.0,
        "joins": -5.0,
        "include:": -5.0,
    },
    # Rubric schema v2 (R0 baseline refinement): scoring only applies when
    # an ORM manager signal is present, so ``dict.get()`` / ``cache.get()``
    # in plain loops no longer score.  True positives such as
    # ``User.objects.get(...)`` inside a loop are unaffected.
    required_signals=["objects"],
    escalation_threshold=6.0,
    pass_threshold=2.0,
    parent_version="1.0.0",
    motivation=(
        "Flag N+1 ORM query smells before they hit production databases. "
        "v2: require the Django-style ORM manager signal 'objects' before "
        "loop+query scoring applies (R0 baseline: 16/25 findings were "
        "false positives on non-ORM dict.get() loops)."
    ),
)

OWASP_SEVERITY_RUBRIC = Rubric(
    rubric_id="owasp-severity-v1",
    name="owasp_severity",
    version="1.0.0",
    criteria=[
        "0-2: no critical vulnerability detected",
        "3-5: risky pattern present but mitigated (sanitised, parameterized)",
        "6-8: high-severity exposure (XSS, unsafe output rendering)",
        "9-10: critical injection (SQLi, RCE, unsafe deserialization, "
        "unauthenticated admin endpoints)",
    ],
    keyword_signals={
        'f"select': 9.8,
        "f'select": 9.8,
        " % ": 9.8,
        " + req.": 9.8,
        "eval(": 9.9,
        "exec(": 9.9,
        "pickle.loads(": 9.9,
        "subprocess.call(": 9.9,
        "<script>": 8.5,
        "dangerouslysetinnerhtml": 8.5,
        "response.write(req.": 8.5,
        "@app.route": 4.5,
        "admin_": 4.6,
    },
    escalation_threshold=7.0,
    pass_threshold=2.0,
    motivation="Block PRs that introduce OWASP Top 10 critical vulnerabilities.",
)

#: Choice options for rule 3 (comment quality).
COMMENT_QUALITY_OPTIONS: List[Dict[str, str]] = [
    {
        "name": "helpful_explanation",
        "description": "helpful explanation why context reasoning intent rationale",
    },
    {
        "name": "essential_arch_note",
        "description": "essential architecture note design decision tradeoff constraint invariant",
    },
    {
        "name": "obvious_redundant_noise",
        "description": "obvious redundant noise restates code multiply value increment loop variable",
    },
    {
        "name": "misleading_outdated",
        "description": "misleading outdated stale todo hack fixme incorrect wrong",
    },
]

#: Choice options for rule 6 (OWASP vulnerability classification).
OWASP_CHOICE_OPTIONS: List[Dict[str, str]] = [
    {
        "name": "owasp_a03_sql_injection",
        "description": (
            "raw sql query string concatenation select from where users "
            "email injection database"
        ),
    },
    {
        "name": "owasp_a03_remote_code_execution",
        "description": (
            "eval exec pickle subprocess remote code execution unsafe "
            "deserialization untrusted input"
        ),
    },
    {
        "name": "owasp_a03_cross_site_scripting",
        "description": (
            "script cross site scripting html unescaped output render "
            "dangerously inner markup"
        ),
    },
    {
        "name": "owasp_a01_broken_access_control",
        "description": (
            "broken access control admin route missing authorization "
            "decorator endpoint privilege"
        ),
    },
    {
        "name": "clean",
        "description": "clean safe sanitize escape input code no vulnerability",
    },
]

#: Logging-context markers for rule 1 (secret leak protection).  The rule
#: only invokes the backend when one of these is present in the input.
LOG_CONTEXT_MARKERS = ("logger.", "logging.", "print(", "console.log", "sys.stdout")

#: Rule identifiers used in audit result dictionaries and CI findings.
RULE_LOG_DATA_LEAKAGE = "log_data_leakage"
RULE_FUNCTION_INTENT = "function_intent"
RULE_COMMENT_QUALITY = "comment_quality"
RULE_DIFF_SECURITY = "diff_security_pre_screen"
RULE_N_PLUS_ONE_ORM = "n_plus_one_orm"
RULE_SECURITY_VULNERABILITIES = "security_vulnerabilities"


class QualitativeLinter:
    """6-rule qualitative code audit built on reflex primitives.

    Args:
        primitives: The :class:`~harness.reflex.primitives.ReflexPrimitives`
            wrapper used to evaluate every rule.
        registry: Optional :class:`~harness.reflex.rubrics.RubricRegistry`;
            when provided, the four module-level score rubrics are
            registered into it for versioning.
    """

    def __init__(
        self,
        primitives: ReflexPrimitives,
        registry: Optional[RubricRegistry] = None,
    ) -> None:
        self.primitives = primitives
        self.registry = registry
        if self.registry is not None:
            for rubric in (
                SIDE_EFFECT_RUBRIC,
                DIFF_RISK_RUBRIC,
                N_PLUS_ONE_RUBRIC,
                OWASP_SEVERITY_RUBRIC,
            ):
                self.registry.register(rubric)

    # -- Rule 1 ---------------------------------------------------------------

    def evaluate_log_data_leakage(self, log_statement: str) -> ReflexResult:
        """Rule 1: detect PII, secrets, or financial data in log statements.

        Primitive: bool — "is a secret or PII exposed?"

        R0 baseline refinement: the check only runs when a logging context
        is present (``logger.``, ``logging.``, ``print(``, ``console.log``,
        ``sys.stdout``).  Non-logging content — e.g. docstrings or
        token-bucket code that merely mentions "token" — returns PASS
        without invoking the backend, eliminating those false positives.
        """
        text_lower = (log_statement or "").lower()
        if not any(marker in text_lower for marker in LOG_CONTEXT_MARKERS):
            return ReflexResult(
                primitive="bool",
                value=0.0,
                confidence=1.0,
                latency_ms=0.0,
                escalate=False,
                detail={
                    "rule": RULE_LOG_DATA_LEAKAGE,
                    "rule_name": "Data Leakage & Secret Protection",
                    "input": log_statement.strip(),
                    "leak_probability": 0.0,
                    "action": "PASS",
                    "skipped": "no_logging_context",
                    "recommendation": "Redact secrets/tokens from log statements.",
                },
            )
        result = self.primitives.bool_gate(
            question=(
                "Does this log statement expose secrets, credentials, PII, "
                "or financial data?"
            ),
            context=log_statement,
            block_threshold=0.85,
        )
        probability = float(result.value)
        if probability > 0.85:
            action = "BLOCK_PR_CRITICAL_LEAK"
        elif probability > 0.50:
            action = "WARN_AUDIT"
        else:
            action = "PASS"
        result.detail.update(
            {
                "rule": RULE_LOG_DATA_LEAKAGE,
                "rule_name": "Data Leakage & Secret Protection",
                "input": log_statement.strip(),
                "leak_probability": probability,
                "action": action,
                "recommendation": "Redact secrets/tokens from log statements.",
            }
        )
        return result

    # -- Rule 2 ---------------------------------------------------------------

    def evaluate_function_intent(self, func_code: str) -> ReflexResult:
        """Rule 2: does the function name describe all of its side effects?

        Primitive: score (0-10 spectrum rubric).
        """
        result = self.primitives.score_gate(func_code, SIDE_EFFECT_RUBRIC)
        status = "REJECT_UNNAMED_SIDE_EFFECTS" if result.escalate else "PASS"
        result.detail.update(
            {
                "rule": RULE_FUNCTION_INTENT,
                "rule_name": "Function Naming & Side Effect Alignment",
                "input_snippet": func_code.strip().split("\n")[0],
                "status": status,
                "recommendation": (
                    "Rename the function to disclose its side effects or "
                    "remove the hidden mutation."
                ),
            }
        )
        return result

    # -- Rule 3 ---------------------------------------------------------------

    def evaluate_comment_quality(
        self, comment: str, code_context: str
    ) -> ReflexResult:
        """Rule 3: classify garbage comments vs essential documentation.

        Primitive: choice over four categories
        (``helpful_explanation``, ``essential_arch_note``,
        ``obvious_redundant_noise``, ``misleading_outdated``).
        """
        routing_input = f"{comment} {code_context}".strip()
        result = self.primitives.route(
            routing_input, COMMENT_QUALITY_OPTIONS, confidence_floor=0.5
        )
        category = str(result.value)
        action = (
            "AUTO_STRIP"
            if category in ("obvious_redundant_noise", "misleading_outdated")
            else "KEEP"
        )
        result.detail.update(
            {
                "rule": RULE_COMMENT_QUALITY,
                "rule_name": "Comment Quality & Garbage Audit",
                "comment": comment,
                "code": code_context,
                "classified_category": category,
                "action": action,
            }
        )
        return result

    # -- Rule 4 ---------------------------------------------------------------

    def pre_screen_diff_security(self, diff_text: str) -> ReflexResult:
        """Rule 4: diff pre-screening against security risk questions.

        Primitive: score.  Risky diffs escalate to System 2, giving a 10x
        context reduction for the expensive reasoner.
        """
        result = self.primitives.score_gate(diff_text, DIFF_RISK_RUBRIC)
        result.detail.update(
            {
                "rule": RULE_DIFF_SECURITY,
                "rule_name": "Diff Security Pre-Screening",
                "diff_snippet": diff_text.strip().split("\n")[0],
                "forward_to_system2": result.escalate,
                "context_reduction_factor": "10x",
            }
        )
        return result

    # -- Rule 5 ---------------------------------------------------------------

    def evaluate_n_plus_one_orm(self, code_snippet: str) -> ReflexResult:
        """Rule 5: detect N+1 ORM query smells.

        Primitive: score (0-10 spectrum rubric).
        """
        result = self.primitives.score_gate(code_snippet, N_PLUS_ONE_RUBRIC)
        flagged = result.escalate
        result.detail.update(
            {
                "rule": RULE_N_PLUS_ONE_ORM,
                "rule_name": "N+1 ORM Query Smell Audit",
                "code_snippet": code_snippet.strip().split("\n")[0],
                "status": "FLAG_N_PLUS_ONE_SMELL" if flagged else "PASS",
                "recommendation": (
                    "Use eager loading (select_related / prefetch_related / "
                    "includes) or bulk fetch outside the loop."
                    if flagged
                    else "Clean ORM usage."
                ),
            }
        )
        return result

    # -- Rule 6 ---------------------------------------------------------------

    def evaluate_security_vulnerabilities(self, code_snippet: str) -> ReflexResult:
        """Rule 6: OWASP Top 10 security vulnerability scan.

        Primitive: choice (OWASP risk vector classification) combined with
        a severity score (0-10 spectrum rubric).  The returned
        :class:`ReflexResult` carries the vulnerability type as its value
        and the severity score plus recommendation in its detail.
        """
        choice = self.primitives.route(
            code_snippet, OWASP_CHOICE_OPTIONS, confidence_floor=0.5
        )
        severity = self.primitives.score_gate(code_snippet, OWASP_SEVERITY_RUBRIC)

        vulnerability_type = str(choice.value)
        severity_score = float(severity.value)
        blocked = severity.escalate
        recommendations = {
            "owasp_a03_sql_injection": (
                "CRITICAL: Replace raw string concatenation with "
                "parameterized ORM/SQL queries."
            ),
            "owasp_a03_remote_code_execution": (
                "CRITICAL: Remove eval/exec or unsafe deserialization; "
                "sanitize untrusted input."
            ),
            "owasp_a03_cross_site_scripting": (
                "HIGH: Escape dynamic output or use DOMPurify/HTML "
                "sanitization."
            ),
            "owasp_a01_broken_access_control": (
                "CRITICAL: Add explicit authorization decorator "
                "(@require_auth) before exposing admin endpoints."
            ),
            "clean": "No critical vulnerability detected.",
        }
        recommendation = recommendations.get(
            vulnerability_type, "No critical vulnerability detected."
        )

        choice.escalate = choice.escalate or blocked
        choice.detail.update(
            {
                "rule": RULE_SECURITY_VULNERABILITIES,
                "rule_name": "OWASP Security Vulnerability Scan",
                "vulnerability_type": vulnerability_type,
                "severity_score": severity_score,
                "status": "BLOCK_PR_SECURITY_VULNERABILITY" if blocked else "PASS",
                "recommendation": recommendation,
            }
        )
        return choice

    # -- Full audit -------------------------------------------------------------

    def run_full_audit(self, snippet: str) -> Dict[str, ReflexResult]:
        """Run all 6 rules against *snippet* and return the results by rule id."""
        return {
            RULE_LOG_DATA_LEAKAGE: self.evaluate_log_data_leakage(snippet),
            RULE_FUNCTION_INTENT: self.evaluate_function_intent(snippet),
            RULE_COMMENT_QUALITY: self.evaluate_comment_quality(snippet, snippet),
            RULE_DIFF_SECURITY: self.pre_screen_diff_security(snippet),
            RULE_N_PLUS_ONE_ORM: self.evaluate_n_plus_one_orm(snippet),
            RULE_SECURITY_VULNERABILITIES: self.evaluate_security_vulnerabilities(
                snippet
            ),
        }
