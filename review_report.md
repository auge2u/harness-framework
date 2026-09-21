# Harness Framework — Design & Maintainability Review

**Reviewer**: Senior Engineer (Claude-style review)
**Scope**: 10 files across CLI, plugins, context, promotion, tools, backend, telemetry, scenarios, verifiers, and utilities.
**Date**: 2025-01-16

---

## src/harness/cli.py

- **Concern**: ux
- **Observation**: `cmd_run` swallows scenario-loading exceptions as warnings and continues with an empty `scenarios` list. The user sees "Run complete." but nothing actually executed.
- **Impact**: Users may believe a run succeeded when it was a no-op. In CI this would silently pass.
- **Suggestion**: Return a non-zero exit code when no scenarios could be loaded, or at least emit an `error()` (not `warning()`). Example:
  ```python
  if not scenarios:
      console.error(f"No scenarios loaded — aborting.")
      return 1
  ```

- **Concern**: security
- **Observation**: Legacy flag handling unconditionally sets `args.count = 3` even if the user passed `--count 10` alongside `--harness-variants`.
- **Impact**: User intent is silently ignored. This is a command-line contract violation.
- **Suggestion**: Preserve existing values if present:
  ```python
  if not getattr(args, "count", None):
      args.count = 3
  ```

- **Concern**: maintainability
- **Observation**: Config-loading boilerplate (`if args.config and Path(args.config).exists(): ... else: HarnessConfig(...)`) is duplicated identically in all four command handlers.
- **Impact**: Any change to default config values or validation logic must be made in four places.
- **Suggestion**: Extract a helper:
  ```python
  def _load_config(path: Optional[str]) -> HarnessConfig:
      if path and Path(path).exists():
          return HarnessConfig.from_yaml(path)
      return HarnessConfig(version="0.2.0", name="cli_default", ...)
  ```

- **Concern**: ux
- **Observation**: `cmd_run` returns `0` even when scenarios fail. The verdict counts are displayed but the exit code is unchanged.
- **Impact**: CI pipelines cannot detect failure automatically.
- **Suggestion**: Return `1` when any `FAIL` verdicts exist:
  ```python
  return 0 if verdicts.get("FAIL", 0) == 0 else 1
  ```

- **Concern**: completeness
- **Observation**: `--max-cycles` and `--parallel` are defined twice (at top-level with `SUPPRESS` and in subparsers). `argparse` will raise a conflict error when both could match.
- **Impact**: The parser may crash or behave unpredictably depending on argparse version.
- **Suggestion**: Remove the top-level `--max-cycles`/`--parallel` definitions. Map legacy flags to subcommand-style args *before* parsing, or use a custom Action.

---

## src/harness/plugins.py

- **Concern**: maintainability
- **Observation**: Eighteen near-identical surface-specific plugin dataclasses (`IdentityPlugin`, `InstructionPlugin`, etc.) are hand-written with identical structure.
- **Impact**: Adding a new surface requires copy-pasting 6 lines. Easy to misspell `default_factory=lambda: ["surface_name"]`.
- **Suggestion**: Use a factory or class decorator:
  ```python
  def surface_plugin(surface: str):
      @dataclass
      class _Plugin(Plugin):
          name: str = surface
          surfaces: List[str] = field(default_factory=lambda s=surface: [s])
      _Plugin.__name__ = f"{surface.title().replace('_', '')}Plugin"
      return _Plugin
  ```

- **Concern**: maintainability
- **Observation**: `Plugin` is both a `@dataclass` and an `ABC`. The abstract methods `apply` and `validate` are on a dataclass. This is valid in Python 3.10+ but unconventional and may confuse linters or junior developers.
- **Impact**: Subclasses may inadvertently rely on dataclass-generated `__init__` overriding expected constructor signatures.
- **Suggestion**: Add a docstring note explaining this hybrid pattern, or separate into `PluginConfig` (dataclass) + `Plugin` (ABC with a config attribute).

- **Concern**: security
- **Observation**: `check_permission` for `operation="execute"` ignores the `surface` parameter entirely. It only checks `self.capabilities.can_execute`.
- **Impact**: A caller could check execute permission on any surface and always get the same answer, making surface-based execution gating meaningless.
- **Suggestion**: Either document that `surface` is ignored for execute operations, or add per-surface execution permissions (e.g., `can_execute_on: List[str]`).

---

## src/harness/context.py

- **Concern**: maintainability
- **Observation**: `_DYNAMIC_SCOPING_RULES` is a list of untyped dictionaries. The keys `"surface_tags"`, `"required_evidence"`, etc. have no compile-time validation.
- **Impact**: A typo in the scoping table will only surface at runtime, and IDE autocomplete won't help.
- **Suggestion**: Define a dataclass:
  ```python
  @dataclass
  class ScopingRule:
      surface_tags: List[str]
      description: str
      required_evidence: List[str]
      gate_type: str
  ```

- **Concern**: maintainability
- **Observation**: `_resolve_gate_type` uses substring matching (`if "human-in-the-loop" in gate_type_str`), which is fragile. `"automated+human-in-the-loop+pareto"` would match the first substring and return `human_in_the_loop`, silently ignoring `pareto`.
- **Impact**: Composite gate types may resolve to an unexpectedly restrictive gate.
- **Suggestion**: Parse explicitly: `gates = set(gate_type_str.replace("-", "_").split("+"))`, then compute priority intersection.

- **Concern**: readability
- **Observation**: `gate_priority` list is hardcoded inside `get_scope_for_change`. This couples policy logic to the method.
- **Impact**: Changing gate priority requires editing the method body.
- **Suggestion**: Make it a module-level constant with a docstring explaining the rationale.

---

## src/harness/promotion.py

- **Concern**: consistency
- **Observation**: `evaluate` and `quarantine` guard against terminal-state transitions, but `accept` and `reject` do not.
- **Impact**: A record in `PROPOSED` state can be `accept()`ed without ever being evaluated. This may be intentional, but it's inconsistent with the documented state machine (`PROPOSED -> EVALUATED -> QUARANTINED -> ACCEPTED | REVERTED`).
- **Suggestion**: Add state validation to `accept` and `reject` if the intended flow requires prior evaluation. Otherwise, document the shortcut explicitly.

- **Concern**: maintainability
- **Observation**: `submit()` auto-generates `promotion_id` as `f"promo-{patch_id}"`. If the same patch is submitted twice, the second submission overwrites the first record silently.
- **Impact**: Lost promotion history for a patch.
- **Suggestion**: Append a suffix or UUID when a collision is detected, or raise an error.

- **Concern**: security
- **Observation**: `human_review` takes `decider: str` but only validates `decision`. The `decider` is stringified with `"unknown"` fallback, but there's no length limit or sanitization.
- **Impact**: Very long or malicious decider strings could cause issues in storage/serialization (though impact is low).
- **Suggestion**: Sanitize `decider` similarly to `decision` — strip, lowercase, and enforce a reasonable max length.

---

## src/harness/tools.py

- **Concern**: security
- **Observation**: `RunCommandTool._contains_blocked_pattern` uses substring matching. The pattern `"rm"` will block `"format"`, `"harmless"`, `"crm"`, etc. The pattern `">"` blocks any command with a redirect character.
- **Impact**: Over-blocking (false positives) frustrate users. More critically, `shell=True` with `subprocess.run(command, shell=True)` means an allowed base command like `ls` can be trivially chained: `ls; rm -rf /`.
- **Suggestion**: 1) Use token/word-boundary matching. 2) **Never use `shell=True` with user-provided input.** Use `shlex.split(command)` and pass as a list to `subprocess.run(..., shell=False)`.

- **Concern**: security
- **Observation**: The allowed-commands whitelist in `RunCommandTool.execute` only checks `command.strip().split()[0]` but then passes the full string to `shell=True`.
- **Impact**: Whitelist is trivially bypassed. `base_cmd = "ls"; full_command = "ls; cat /etc/passwd"` — the base_cmd passes the whitelist, but the shell executes the entire string.
- **Suggestion**: Parse with `shlex.split()`, verify the first token, and pass the tokenized list to `subprocess.run(cmd_list, shell=False)`.

- **Concern**: security
- **Observation**: `_resolve_and_validate` calls `os.path.realpath(abs_path)` on paths that may not exist. On some platforms, `realpath` of a non-existent path behaves differently (it may not resolve intermediate symlinks).
- **Impact**: A path like `/allowed/symlink_to_outside/file.txt` where `file.txt` doesn't exist may not have its symlink resolved, potentially escaping the sandbox.
- **Suggestion**: Verify each directory component exists and is within bounds *before* resolving the full path, or use `pathlib.Path.resolve(strict=False)` with explicit checks.

- **Concern**: security
- **Observation**: `ReadFileTool` opens files with `encoding="utf-8"` but does not enforce a size limit. A malicious agent could request a multi-GB log file, causing memory exhaustion.
- **Impact**: Denial of service via resource exhaustion.
- **Suggestion**: Add a `max_size_bytes` parameter and read in chunks, or use `os.path.getsize()` to reject oversized files.

- **Concern**: completeness
- **Observation**: `ToolRegistry.dispatch` calls `tool.execute(params, context)` but does not validate `params` against the tool's schema before execution.
- **Impact**: Tools receive malformed input and must handle validation themselves, leading to inconsistent error handling.
- **Suggestion**: Add a `_validate_params(tool.schema, params)` helper in `ToolRegistry.dispatch` that checks required fields and basic types before calling `execute`.

---

## src/harness/agent_backend.py

- **Concern**: completeness
- **Observation**: `OpenAIBackend` and `AnthropicBackend` are pure stubs — every method raises `NotImplementedError`.
- **Impact**: The framework cannot run against real APIs out of the box. Users must implement backends themselves.
- **Suggestion**: Either implement minimal working backends (they're ~20 lines each with `requests` or `httpx`), or remove them and document that users should install an optional extra package (e.g., `harness[openai]`).

- **Concern**: security
- **Observation**: `BackendConfig` stores `api_key` as a plain string dataclass field. If the config is ever logged, serialized, or printed, the key is exposed.
- **Impact**: API keys may leak into logs or trace stores.
- **Suggestion**: Mark the field with `repr=False` and add a `__repr__` override, or use a thin wrapper class that redacts on repr:
  ```python
  @dataclass
  class BackendConfig:
      api_key: Optional[str] = field(default=None, repr=False)
  ```

- **Concern**: maintainability
- **Observation**: `MockBackend.complete` uses `messages[-1].content` as a lookup key. If `messages` is empty, this will raise `IndexError`.
- **Impact**: Tests with empty message lists crash instead of getting the default mock response.
- **Suggestion**: Guard the access:
  ```python
  key = messages[-1].content if messages else "default"
  ```

---

## src/harness/telemetry/finops.py

- **Concern**: maintainability
- **Observation**: `get_daily_attribution` divides `trace.cost_usd` by `len(surfaces)` without checking for zero. The `if not surfaces:` fallback handles the empty-list case, but if `surfaces` is an empty list (not just falsy), `len(surfaces) == 0` and a `ZeroDivisionError` would occur.
- **Impact**: Runtime crash on traces with `surfaces=[]`.
- **Suggestion**: Use `max(len(surfaces), 1)` or explicitly guard:
  ```python
  cost_per_surface = trace.cost_usd / max(len(surfaces), 1)
  ```

- **Concern**: maintainability
- **Observation**: `get_budget_forecast` uses naive linear extrapolation from a 7-day average with no trend weighting, seasonality, or confidence interval.
- **Impact**: Forecasts will be misleading for workloads with weekly patterns or growth trends. The `alert_if_over_budget` may fire false positives on weekends or false negatives during growth.
- **Suggestion**: Document the limitation explicitly ("naive linear extrapolation — not suitable for production budgeting"), or implement a simple weighted moving average.

- **Concern**: maintainability
- **Observation**: `datetime.now()` is used without timezone awareness. Mixed with UTC timestamps from traces, this could produce inconsistent day boundaries.
- **Impact**: Daily attribution may shift by a day depending on server timezone.
- **Suggestion**: Use `datetime.now(timezone.utc)` consistently.

---

## src/harness/scenarios/base.py

- **Concern**: security
- **Observation**: Class attributes `tags: List[str] = []` and `surfaces: List[str] = []` are mutable list objects shared across all instances and subclasses.
- **Impact**: Subclass `MyScenario.tags.append("foo")` mutates the base class attribute, affecting every other scenario. This is the classic mutable-default-argument bug at the class level.
- **Suggestion**: Use `tuple` or `None` as defaults, or initialize in `__init__`:
  ```python
  tags: List[str] = field(default_factory=list)  # if using dataclass
  # OR for plain class:
  def __init__(self):
      self.tags = []
  ```

- **Concern**: maintainability
- **Observation**: `Scenario` is not a dataclass, so subclasses must remember to call `super().__init__()` if one is added later.
- **Impact**: Subclasses that define `__init__` without `super()` will silently skip any future base-class initialization.
- **Suggestion**: Add an explicit `__init__` that initializes the mutable fields, even if it's a no-op now:
  ```python
  def __init__(self):
      self.tags = list(self.tags)
      self.surfaces = list(self.surfaces)
  ```

---

## src/harness/verifiers/builtin.py

- **Concern**: maintainability
- **Observation**: `JsonSchemaVerifier.verify` uses a broad `except Exception` to catch jsonschema errors, then checks `type(exc).__module__ == "jsonschema.exceptions"` to distinguish validation errors from import errors.
- **Impact**: If `jsonschema` raises a `SchemaError` (module `jsonschema.exceptions`) vs an `ImportError` (module `builtins`), the check works. But if `jsonschema` is installed and raises `jsonschema.exceptions.ValidationError`, the fallback branch is unreachable. More importantly, a `RecursionError` or `MemoryError` in jsonschema would be silently swallowed.
- **Suggestion**: Use specific exception imports:
  ```python
  try:
      import jsonschema
      from jsonschema.exceptions import ValidationError
      HAS_JSONSCHEMA = True
  except ImportError:
      HAS_JSONSCHEMA = False
  ```
  Then in `verify`:
  ```python
  if HAS_JSONSCHEMA:
      try:
          jsonschema.validate(instance=actual, schema=self.schema)
          return VerificationResult(...PASS...)
      except ValidationError as exc:
          return VerificationResult(...FAIL with exc...)
  # fallback below
  ```

- **Concern**: security
- **Observation**: `LLMJudgeVerifier` stores an arbitrary callable (`judge_fn`) and invokes it without any sandboxing or timeout.
- **Impact**: If `judge_fn` comes from untrusted config (e.g., deserialized YAML), arbitrary code execution is possible.
- **Suggestion**: Document that `judge_fn` must be a trusted callable. Optionally wrap the call in a try/except with a timeout using `signal` or `concurrent.futures`.

- **Concern**: readability
- **Observation**: `FuzzyVerifier._compute_score` catches `Exception` when importing `rapidfuzz` and again for `difflib`. This silently hides import errors and even legitimate crashes.
- **Impact**: If `difflib` raises an unexpected error (e.g., memory issue), the verifier returns `0.0` instead of surfacing the problem.
- **Suggestion**: Catch `ImportError` specifically for imports, and catch `Exception` only around `SequenceMatcher.ratio()` with an explicit log:
  ```python
  except Exception as exc:
      logger.warning("difflib failed: %s", exc)
      return 0.0
  ```

---

## src/harness/utils/diffing.py

- **Concern**: security
- **Observation**: `_freeze_dict` and `_freeze_list` are mutually recursive with no cycle detection. A dictionary that contains itself (via circular reference) will cause infinite recursion and a `RecursionError`.
- **Impact**: Malicious or malformed input can crash the process.
- **Suggestion**: Add a `seen: set = None` parameter or use `id()` tracking:
  ```python
  def _freeze_dict(d: dict, _seen: Optional[Set[int]] = None) -> Any:
      _seen = _seen or set()
      if id(d) in _seen:
          return ("<circular>",)
      _seen.add(id(d))
      ...
  ```

- **Concern**: maintainability
- **Observation**: `_freeze_dict` sorts keys using `_sort_key` which returns `(type(val).__name__, val)`. If a dictionary has keys of incomparable types (e.g., `int` and `str`), the sort will raise `TypeError` in Python 3.
- **Impact**: Crash on dicts with mixed-type keys.
- **Suggestion**: Ensure `_sort_key` returns comparable values. The current implementation may still fail because `"int" < "str"` is fine but `1 < "a"` is never reached since it's wrapped in a tuple. Actually, `(type_name, val)` tuples are compared element-wise, and since the first element is always a string, it should be fine. But if two keys have the same type and incomparable values (e.g., custom objects without `__lt__`), it fails. Consider:
  ```python
  def _sort_key(val: Any) -> tuple:
      return (type(val).__name__, str(val))
  ```

- **Concern**: readability
- **Observation**: The docstring says "For list values the comparison is performed as a set-difference (order is ignored)." This is misleading — it treats lists as *sets*, so `[1, 1, 2]` and `[1, 2]` would compare as equal.
- **Impact**: Users may be surprised that duplicate elements are deduplicated.
- **Suggestion**: Update the docstring to explicitly mention that duplicates are ignored.

---

# Top 5 User-Facing Concerns

| # | Concern | File | Impact |
|---|---------|------|--------|
| 1 | **CLI silently succeeds on failure** | `cli.py` | `cmd_run` returns exit code `0` even when scenarios fail or can't load. CI pipelines will pass on broken builds. |
| 2 | **Command sandbox is trivially bypassed** | `tools.py` | `RunCommandTool` uses `shell=True` with substring-based blocklists. A command like `ls; rm -rf /` passes the whitelist because `ls` is the first token, but the shell executes the entire string. |
| 3 | **Legacy flag handling ignores user arguments** | `cli.py` | `--harness-variants` hardcodes `args.count = 3`, ignoring any `--count` the user provided alongside it. |
| 4 | **File read has no size limits** | `tools.py` | `ReadFileTool` can read arbitrarily large files, enabling DoS via memory exhaustion. |
| 5 | **Real backends are non-functional stubs** | `agent_backend.py` | `OpenAIBackend` and `AnthropicBackend` raise `NotImplementedError`. Users cannot run against real LLM APIs without writing their own backend. |

# Top 5 Maintainability Concerns

| # | Concern | File | Impact |
|---|---------|------|--------|
| 1 | **Massive duplication in CLI commands** | `cli.py` | Config loading, registry setup, and verifier registration are copy-pasted across all four handlers. |
| 2 | **Mutable class-level defaults in Scenario ABC** | `scenarios/base.py` | `tags = []` and `surfaces = []` are shared mutable objects across all subclasses — classic Python footgun. |
| 3 | **Untyped global scoping rules table** | `context.py` | `_DYNAMIC_SCOPING_RULES` is a list of plain dicts with no type safety or IDE support. |
| 4 | **18 near-identical plugin boilerplate classes** | `plugins.py` | Adding a new surface requires copy-pasting. A factory or registry would eliminate this. |
| 5 | **Broad exception swallowing in verifiers** | `verifiers/builtin.py` | `FuzzyVerifier` and `JsonSchemaVerifier` catch `Exception` broadly, hiding real bugs and import issues. |

---

# Overall Verdict: `needs_work`

## Rationale

The codebase shows **strong architectural vision** — clean separation of concerns, thoughtful abstractions (plugin surfaces, promotion state machine, execution scoping), and good documentation habits. The promotion pipeline and plugin base classes are particularly well-crafted.

However, **there are critical safety and UX issues that must be addressed before shipping**:

1. **Security**: The `RunCommandTool` is dangerously unsafe. `shell=True` + substring blocklists + a whitelist that only checks the first token is a recipe for sandbox escape. This needs immediate hardening.
2. **CLI reliability**: Returning exit code `0` on failure and swallowing exceptions as warnings makes the tool unsuitable for CI/automation.
3. **Completeness**: The real LLM backends are pure stubs. A framework advertised as "Reflective Agent Runtime" cannot actually run against real agents out of the box.
4. **Maintainability**: The mutable class-level defaults in `Scenario` and the CLI duplication are real footguns that will slow future development.

## Recommended Priorities

| Priority | Action |
|----------|--------|
| **P0 (Blocker)** | Fix `RunCommandTool` — remove `shell=True`, use `shlex.split()`, and fix blocklist matching. |
| **P0 (Blocker)** | Fix CLI exit codes — return non-zero when scenarios fail or can't load. |
| **P1** | Implement minimal working `OpenAIBackend` and `AnthropicBackend` (or remove stubs and document extras). |
| **P1** | Add `max_size_bytes` to `ReadFileTool` and size validation. |
| **P1** | Fix mutable class-level defaults in `scenarios/base.py`. |
| **P2** | Extract CLI config-loading helper to remove duplication. |
| **P2** | Replace untyped `_DYNAMIC_SCOPING_RULES` with dataclasses. |
| **P2** | Use specific exception types in verifiers instead of broad `except Exception`. |
