# Architecture Guide

## Core Concepts

### Harness as Editable Artifact

Every component of an AI agent system is a **declared surface** that can be versioned, diffed, patched, and reverted.

### Self-Improvement Loop

```
Propose patch → Evaluate on scenarios → Run acceptance gates → Decide accept/reject
```

### Bounded Change

- Proposals constrained to declared surfaces
- Every patch has an automatically computed inverse
- 8 mandatory acceptance gates

## System Diagram

See README.md for the full architecture diagram.

## Components

### Core Engine
- **PluginRegistry**: Discovers and manages plugins for all surfaces
- **HarnessConfig**: Central configuration with validation
- **HarnessPatch**: Atomic, invertible edit primitive

### Governance
- **PolicyEngine**: Validates patches before evaluation
- **AcceptanceSuite**: 8 gates for patch quality
- **PromotionPipeline**: State machine for patch lifecycle

### Execution
- **SingleRunner**: Sequential scenario execution
- **CircuitRunner**: Parallel variant evaluation
- **SwarmCoordinator**: Dynamic agent swarm orchestration

### Memory
- **TraceStore**: SQLite-backed execution history with analytics
- **HarnessLineage**: Versioned configuration history
- **KnowledgeGraphPipeline**: Persistent structured memory

### Safety
- **LifecycleManager**: Chain-of-responsibility hooks for tool execution
- **ApprovalManager**: Per-tool/per-surface approval modes
- **Sandbox**: Path-traversal-protected file operations
