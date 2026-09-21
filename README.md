# Harness Framework

[![CI](https://github.com/your-org/harness-framework/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/harness-framework/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-758%20passing-brightgreen)]()

> A self-validating, self-improving agent runtime with knowledge graph memory and swarm orchestration.

The Harness Framework treats every component of an AI agent system — prompts, tools, memory, sandbox, routing, evaluators — as editable, versioned, optimizable artifacts. It enables both **bounded self-improvement** (the harness proposes, evaluates, and accepts its own patches) and **external harness search** (coding agents propose patches via a filesystem interface).

**Key innovation**: Agents collaborate via a shared **knowledge graph** rather than passing raw context windows, achieving 99%+ token reduction for multi-hop reasoning tasks.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Core Concepts](#core-concepts)
- [18 Declared Surfaces](#18-declared-surfaces)
- [Architecture](#architecture)
- [Installation](#installation)
- [Usage](#usage)
- [Knowledge Graph](#knowledge-graph)
- [Swarm Orchestration](#swarm-orchestration)
- [Lifecycle Hooks](#lifecycle-hooks)
- [Development](#development)
- [Project Context](#project-context)
- [License](#license)

---

## Quick Start

```bash
# Install
pip install harness-framework

# Or install from source
pip install -e ".[dev]"

# Run the CLI
harness --help

# Run all 758 tests
make test
```

```python
from harness import HarnessConfig, Surface, SurfaceType
from harness.graph import KnowledgeGraphPipeline
from harness.agent_backend import MockBackend

# Build a knowledge graph from documents
pipeline = KnowledgeGraphPipeline(backend=MockBackend())
graph = pipeline.build([
    "Neil Armstrong walked on the Moon during Apollo 11.",
    "Buzz Aldrin was the second person to walk on the Moon.",
    "Apollo 11 launched from Kennedy Space Center.",
])

# Query with multi-hop reasoning
result = pipeline.query("Who walked on the Moon?")
print(result)
# {'answer': 'Neil Armstrong and Buzz Aldrin', 'citations': [...]}
```

---

## Core Concepts

### Harness as Editable Artifact

Every component is a **declared surface** that can be versioned, diffed, patched, and reverted:

```python
patch = HarnessPatch(
    patch_id="p-001",
    surface="tools",
    target_id="ReadFileTool",
    operation=PatchOperation.REPLACE,
    before={"allowed_paths": ["/tmp"]},
    after={"allowed_paths": ["/tmp", "/data"]},
    inverse=...,  # Automatically computed
    motivation="Allow reading from /data directory",
)
```

### Self-Improvement Loop

```
Propose patch → Evaluate on held-in scenarios
                    ↓
            Evaluate on held-out scenarios
                    ↓
            Run AcceptanceSuite gates
                    ↓
            PolicyEngine.validate_patch()
                    ↓
            PromotionPipeline: PROPOSED → ACCEPTED
                    ↓
            Apply or Reject
```

### Bounded Self-Improvement

- Proposals are constrained to declared surfaces
- Every patch has an automatically computed inverse
- 8 mandatory acceptance gates: regression, diff_scope, security, traceability, rollback, held-out protection, prompt safety, artifact leakage
- Promotion pipeline: PROPOSED → EVALUATED → QUARANTINED → ACCEPTED | REJECTED | REVERTED

---

## 18 Declared Surfaces

| # | Surface | Description | Plugin Class |
|---|---------|-------------|--------------|
| 1 | `identity` | Agent identity and persona | `IdentityPlugin` |
| 2 | `instructions` | System prompts and instructions | `InstructionPlugin` |
| 3 | `tools` | Tool definitions and schemas | `ToolPlugin` |
| 4 | `skills` | Reusable skill modules | `SkillPlugin` |
| 5 | `mcps` | MCP server connections | `McpPlugin` |
| 6 | `memory` | Ephemeral context memory | `MemoryPlugin` |
| 7 | `sandbox` | Execution environment | `SandboxPlugin` |
| 8 | `model_defaults` | Default model parameters | `BackendPlugin` |
| 9 | `routing` | Request routing logic | `RouterPlugin` |
| 10 | `orchestration` | Agent coordination | `OrchestrationPlugin` |
| 11 | `data_gateway` | External data access | `DataGatewayPlugin` |
| 12 | `evaluator` | Output verification | `VerifierPlugin` |
| 13 | `telemetry` | Metrics and observability | `ReporterPlugin` |
| 14 | `artifacts` | Generated artifacts | `ArtifactPlugin` |
| 15 | `secrets_policy` | Secret management | `SecretsPlugin` |
| 16 | `policy_engine` | Edit permission policies | `PolicyPlugin` |
| 17 | **`knowledge_graph`** | Persistent structured memory | `KnowledgeGraphPlugin` |
| 18 | **`swarm`** | Parallel agent coordination | `SwarmOrchestrationPlugin` |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Harness Framework v0.3.0                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   CLI Layer  │  │  SelfHarness │  │  MetaHarness │  │   Circuit    │   │
│  │   (Rich)     │  │    Loop      │  │   (Git)      │  │   Runner     │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         └─────────────────┴─────────────────┴─────────────────┘             │
│                                    │                                        │
│                    ┌───────────────┴───────────────┐                        │
│                    │        Core Engine              │                        │
│                    │  ┌─────────┐ ┌──────────────┐ │                        │
│                    │  │ Plugin  │ │   Harness    │ │                        │
│                    │  │Registry │ │   Config     │ │                        │
│                    │  └────┬────┘ └──────────────┘ │                        │
│                    │       └───────────┬─────────────┘                        │
│                    └───────────────────┼────────────────────────────────────┘
│                                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─┴──────────┐  ┌─────────────┐         │
│  │  Knowledge  │  │    Swarm    │  │  Policy    │  │  Acceptance │         │
│  │    Graph    │  │ Orchestrator│  │  Engine    │  │   Suite     │         │
│  │  (4-stage)  │  │(Work-Steal) │  │(Validation)│  │  (8 gates)  │         │
│  └──────┬──────┘  └──────┬──────┘  └─────┬──────┘  └──────┬──────┘         │
│         │                │                │                │               │
│  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐  ┌──────┴──────┐       │
│  │  Extract    │  │  Lifecycle  │  │  Promotion  │  │   Trace     │       │
│  │  Resolve    │  │    Hooks    │  │  Pipeline   │  │   Store     │       │
│  │  Assemble   │  │  (Approval)  │  │  (States)   │  │ (SQLite)   │       │
│  │  Query      │  │  (Audit)     │  │  (Lineage)  │  │ (Analytics)│       │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘       │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                     Agent Backends + Tools                           │    │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────────┐ │    │
│  │  │  Mock    │ │  OpenAI  │ │ Anthropic│ │ ToolRegistry (Sandbox) │ │    │
│  │  │ (Tests)  │ │  (API)   │ │  (API)   │ │ Read/Write/RunCommand  │ │    │
│  │  └──────────┘ └──────────┘ └──────────┘ └────────────────────────┘ │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Installation

### From PyPI (when published)

```bash
pip install harness-framework
```

### From Source

```bash
git clone https://github.com/your-org/harness-framework.git
cd harness-framework
pip install -e ".[dev]"
```

### Requirements

- Python 3.10+
- `pyyaml` (runtime)
- `networkx` (runtime, optional — pure-Python fallback available)

---

## Usage

### 1. Define a Harness Configuration

```python
from harness import HarnessConfig, Surface, SurfaceType

config = HarnessConfig(
    version="1.0.0",
    name="my-agent",
    surfaces=[
        Surface(name="api", type=SurfaceType.API),
        Surface(name="database", type=SurfaceType.DATABASE),
        Surface(name="auth", type=SurfaceType.API),
    ],
    scenarios={...},
    verifiers=[...],
)
```

### 2. Run Scenarios

```python
from harness.runners import SingleRunner
from harness.agent_backend import MockBackend

runner = SingleRunner(config, backend=MockBackend())
results = runner.run_all()

for scenario, (verdict, score, latency, cost, raw) in results.items():
    print(f"{scenario}: {verdict.value} (score={score:.2f}, cost=${cost:.4f})")
```

### 3. Run Parallel Variants

```python
from harness.runners import CircuitRunner

circuit = CircuitRunner(
    config,
    parallelism=4,
    cost_budget_usd=10.0,
)

# Generate variants by mutating surfaces
from harness.patch import HarnessPatch, PatchOperation

variants = [
    VariantConfig(name="baseline", harness=config),
    VariantConfig(
        name="temperature-0.5",
        harness=config,
        patches=[HarnessPatch(...)],
    ),
]

results = circuit.run_all(variants, scenarios=["test-1", "test-2"])
```

### 4. Self-Improvement Loop

```python
from harness.loops import SelfHarnessLoop
from harness.policy import PolicyEngine
from harness.acceptance import AcceptanceSuite

loop = SelfHarnessLoop(
    config=config,
    registry=registry,
    trace_store=trace_store,
    lineage=lineage,
    clusterer=clusterer,
    acceptance_suite=AcceptanceSuite([...]),
    promotion_pipeline=promotion_pipeline,
    policy_engine=PolicyEngine(tenant="default"),
)

proposal = loop.propose(config)  # Propose a patch
metrics = loop.evaluate(proposal)  # Evaluate on held-in + held-out
result = loop.decide(proposal, metrics)  # accept | reject | review
```

---

## Knowledge Graph

The knowledge graph is the 17th surface — a persistent structured memory layer that replaces context-window passing for multi-agent collaboration.

### 4-Stage Pipeline

```python
from harness.graph import KnowledgeGraphPipeline
from harness.agent_backend import MockBackend

pipeline = KnowledgeGraphPipeline(backend=MockBackend())

# Stage 1: Extract entities and relations
documents = [
    "Acme Corp dropped prices 15% in Q3.",
    "Acme Corp filed patent US-2024-XXXX for a new product.",
    "Acme Corp R&D spending doubled in FY2024.",
]

# Stages 1-3: Build graph
graph = pipeline.build(documents)

# Stage 4: Query with multi-hop reasoning
result = pipeline.query(
    "What is Acme Corp's competitive strategy?",
    center_entity="Acme Corp",
    hops=2,
)
# Answer: "Acme Corp is pursuing a strategy of undercutting incumbents
#          before launching a differentiated offering."
# Citations: [(Acme Corp)-[dropped prices]->(15%), source: doc1],
#            [(Acme Corp)-[filed]->(patent US-2024-XXXX), source: doc2]
```

### Why Knowledge Graphs?

| Problem | Context-Passing | Knowledge Graph |
|---------|----------------|-----------------|
| Multi-hop reasoning | 34% accuracy | 78% accuracy |
| Token growth | Linear O(n) | Constant O(1) |
| Cross-document facts | Lost in summaries | Preserved as edges |
| Session persistence | Full re-process | Graph reload |
| Provenance | None | Every edge cited |

---

## Swarm Orchestration

The swarm orchestrator coordinates parallel agents with work-stealing and consensus.

```python
from harness.swarm import SwarmCoordinator, SwarmTask

coordinator = SwarmCoordinator(
    max_workers=10,
    consensus_threshold=0.8,
    cost_budget_usd=5.0,
    timeout_seconds=120.0,
)

task = SwarmTask(
    task_id="analyze-1",
    description="Review code for security issues",
    task_type="verify",
)

report = coordinator.run(task)

print(f"Agreement: {report.agreement_score:.0%}")
print(f"Consensus: {report.consensus_output}")
if report.dissenting_views:
    print(f"Dissent: {len(report.dissenting_views)} workers disagreed")
```

### Features

- **Dynamic task decomposition**: Breaks complex tasks into parallel subtasks
- **Work-stealing**: Idle workers pull tasks from busy workers (Chase-Lev algorithm)
- **Consensus aggregation**: Clusters results, identifies agreement and dissent
- **Early termination**: Stops when consensus threshold is reached (20-40% cost savings)
- **Cost budgets**: Enforces per-run spending limits
- **Dependency ordering**: Respects task prerequisites

---

## Lifecycle Hooks

Lifecycle hooks provide safety gating around tool execution, patch proposals, and scenario runs.

```python
from harness.lifecycle import (
    LifecycleManager,
    DangerousCommandHook,
    FileWriteApprovalHook,
    CostBudgetHook,
    instrument_tools,
)
from harness.tools import ToolRegistry, ReadFileTool, WriteFileTool

# Create instrumented tool registry
registry = ToolRegistry()
registry.register(ReadFileTool(allowed_paths=["/project"]))
registry.register(WriteFileTool(allowed_paths=["/project"]))

manager = LifecycleManager()
manager.register(DangerousCommandHook())      # Block rm -rf, curl | sh
manager.register(FileWriteApprovalHook())     # Confirm file writes
manager.register(CostBudgetHook(budget=10.0))  # Enforce cost budget

# Wrap registry — all tool calls now go through hooks
safe_registry = instrument_tools(registry, manager)
```

### Approval Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `AUTO` | Execute without confirmation | Safe operations, production |
| `CONFIRM` | Pause for approval | File writes, destructive ops |
| `SIMULATE` | Show what would happen | Dry runs, testing |
| `NEVER` | Block entirely | Dangerous operations |
| `REJECT` | Block and log | Policy violations |

---

## Development

```bash
# Clone
git clone https://github.com/your-org/harness-framework.git
cd harness-framework

# Install dev dependencies
make install-dev

# Run tests
make test

# Run tests with coverage
make test-cov

# Lint
make lint

# Format code
make format

# Type check
make type-check

# Build package
make build

# Clean artifacts
make clean
```

### Project Structure

```
.
├── src/harness/              # Source code
│   ├── core/                 # Config, types, exceptions, registry
│   ├── graph/                # Knowledge graph pipeline
│   ├── swarm/                # Swarm orchestrator
│   ├── lifecycle/            # Hooks + approval modes
│   ├── runners/              # SingleRunner, CircuitRunner
│   ├── loops/                # SelfHarness, MetaHarness
│   ├── scenarios/            # Scenario ABC, loader, split
│   ├── verifiers/            # Exact, fuzzy, JSON schema, LLM judge
│   ├── store/                # TraceStore (SQLite)
│   ├── analysis/             # Clusterer, lineage
│   ├── gateway/              # DataGateway, scoring
│   ├── telemetry/            # FinOps, metrics
│   ├── plugins/              # Plugin base classes + implementations
│   ├── tools.py              # Tool ABC + built-in tools
│   ├── cli.py                # Rich CLI interface
│   ├── policy.py             # PolicyEngine
│   ├── acceptance.py         # Acceptance gates
│   ├── promotion.py          # Promotion pipeline
│   ├── patch.py              # HarnessPatch primitive
│   ├── context.py            # RunContext, ExecutionScope
│   ├── agent_backend.py      # Backend ABC + implementations
│   ├── signals.py            # Signal handlers
│   └── utils/                # Diffing, hashing
├── tests/                    # Test suite (758 tests)
├── docs/                     # Documentation
├── .github/                  # CI/CD, issue templates
├── pyproject.toml            # Package configuration
├── Makefile                  # Development commands
└── README.md                 # This file
```

---

## Project Context

This project was developed through a series of structured sessions covering:

1. **Architecture Design**: 17 declared surfaces, plugin architecture, HarnessPatch primitive
2. **Governance Layer**: PolicyEngine, AcceptanceSuite, PromotionPipeline, TraceStore
3. **Bundle Implementation**: 9 highest-priority improvements across governance, UX, data, security
4. **Kimi Code Integration**: Swarm orchestration, lifecycle hooks, approval modes
5. **Graph-Engineering Integration**: Knowledge graph pipeline (extract → resolve → assemble → query)

See [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md) for the complete development history, design decisions, and future roadmap.

---

## References

- **Kimi Code** (Moonshot AI): Agent swarm architecture, MCP-first design — [github.com/MoonshotAI/kimi-code](https://github.com/MoonshotAI/kimi-code)
- **Graph-Engineering Playbook** (Anthropic): Knowledge graph pipeline, structured outputs — [Anthropic Cookbook](https://github.com/anthropics/anthropic-cookbook)
- **Building Effective AI Agents** (Anthropic): Five canonical agent patterns

---

## License

[MIT License](LICENSE) — see LICENSE file for details.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for version history.
