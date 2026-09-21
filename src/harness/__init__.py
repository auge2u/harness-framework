"""Harness - A self-validating AI harness framework."""

__version__ = "0.4.0"

# These imports are safe because they do not trigger circular dependencies:
# - exceptions: no internal harness imports
# - types: no internal harness imports
# - scenarios.base: only depends on typing stdlib
# - scenarios.split: only depends on scenarios.base
# - scenarios.loader: only depends on scenarios.base
# - verifiers.base: only depends on core.types
# - verifiers.builtin: only depends on core.types + verifiers.base
# - verifiers.composite: only depends on core.types + verifiers.base

from harness.core.exceptions import (
    BudgetExceededError,
    ConfigValidationError,
    HarnessError,
    LineageError,
    PluginAlreadyRegisteredError,
    PluginNotFoundError,
    RateLimitExceededError,
    ScenarioError,
    SourceBlockedError,
    SourceNotFoundError,
    VerifierError,
)
from harness.core.types import (
    ChangeScope,
    FailureSignature,
    HarnessProposal,
    Surface,
    SurfaceType,
    TraceRecord,
    Verdict,
    VerificationResult,
)
from harness.scenarios.base import Scenario
from harness.scenarios.loader import ScenarioLoader
from harness.scenarios.split import ScenarioSplit
from harness.verifiers.base import Verifier
from harness.verifiers.builtin import (
    ExactVerifier,
    FuzzyVerifier,
    JsonSchemaVerifier,
    LLMJudgeVerifier,
)
from harness.verifiers.composite import CompositeVerifier

# v2 exports
from harness.plugins import (
    Plugin,
    PluginContext,
    PluginCapabilities,
    TrustLevel,
    IdentityPlugin,
    InstructionPlugin,
    ToolPlugin,
    SkillPlugin,
    McpPlugin,
    MemoryPlugin,
    SandboxPlugin,
    BackendPlugin,
    RouterPlugin,
    OrchestrationPlugin,
    DataGatewayPlugin,
    VerifierPlugin,
    ReporterPlugin,
    ArtifactPlugin,
    SecretsPlugin,
    PolicyPlugin,
)
from harness.plugins.kg_plugin import KnowledgeGraphPlugin
from harness.plugins.swarm import SwarmOrchestrationPlugin
from harness.patch import HarnessPatch, PatchOperation
from harness.lifecycle import (
    ApprovalMode,
    HookPoint,
    ToolCallContext,
    HookResult,
    LifecycleHook,
    LifecycleManager,
    ApprovalManager,
    instrument_tools,
    DangerousCommandHook,
    FileWriteApprovalHook,
    AuditLogHook,
    CostBudgetHook,
    SemanticHookBase,
    SemanticDangerousCommandHook,
    SemanticSecretLeakHook,
    SemanticPatchRiskHook,
    register_semantic_gates,
)
from harness.store.snapshots import (
    ContentAddressedStore,
    SnapshotRef,
    SnapshotNotFoundError,
)
from harness.audit import (
    GENESIS_HASH,
    AuditEntry,
    AuditLog,
    AuditRecorder,
    ChainVerification,
)
from harness.swarm import (
    SwarmTask,
    SwarmWorker,
    SwarmResult,
    ConsensusReport,
    SwarmCoordinator,
    WorkStealingQueue,
)
from harness.graph import (
    Entity,
    Relation,
    ExtractedGraph,
    ResolutionCluster,
    ProvenanceRecord,
    GraphExtractor,
    EntityResolver,
    GraphAssembler,
    GraphQuerier,
    ProvenanceTracker,
    KnowledgeGraphPipeline,
)
from harness.reflex import (
    ReflexBackend,
    MockReflexBackend,
    JevBackend,
    Rubric,
    RubricRegistry,
    ReflexResult,
    ReflexPrimitives,
    EscalationEvent,
    EscalationPolicy,
    ReflexiveVerifier,
    QualitativeLinter,
    RoutableSkill,
    RoutingDecision,
    ReflexRouter,
    ReflexGate,
    AuditFinding,
    ReflexCIRunner,
    DecisionRecord,
    RubricMetrics,
    MetaRefinementAnalyzer,
)
from harness.context import RunContext, ExecutionScope
from harness.agent_backend import (
    AgentBackend,
    MockBackend,
    OpenAIBackend,
    AnthropicBackend,
    BackendConfig,
    Message,
    BackendResponse,
    BackendCapability,
)
from harness.tools import (
    Tool,
    ToolSchema,
    ToolRegistry,
    ReadFileTool,
    WriteFileTool,
    RunCommandTool,
)

__all__ = [
    "__version__",
    # v1
    "BudgetExceededError",
    "ChangeScope",
    "CompositeVerifier",
    "ConfigValidationError",
    "ExactVerifier",
    "FailureSignature",
    "FuzzyVerifier",
    "HarnessError",
    "HarnessProposal",
    "JsonSchemaVerifier",
    "LineageError",
    "LLMJudgeVerifier",
    "PluginAlreadyRegisteredError",
    "PluginNotFoundError",
    "RateLimitExceededError",
    "Scenario",
    "ScenarioError",
    "ScenarioLoader",
    "ScenarioSplit",
    "SourceBlockedError",
    "SourceNotFoundError",
    "Surface",
    "SurfaceType",
    "TraceRecord",
    "Verifier",
    "VerifierError",
    "Verdict",
    "VerificationResult",
    # v2: plugins
    "Plugin",
    "PluginContext",
    "PluginCapabilities",
    "TrustLevel",
    "IdentityPlugin",
    "InstructionPlugin",
    "ToolPlugin",
    "SkillPlugin",
    "McpPlugin",
    "MemoryPlugin",
    "SandboxPlugin",
    "BackendPlugin",
    "RouterPlugin",
    "OrchestrationPlugin",
    "DataGatewayPlugin",
    "VerifierPlugin",
    "ReporterPlugin",
    "ArtifactPlugin",
    "SecretsPlugin",
    "PolicyPlugin",
    "KnowledgeGraphPlugin",
    "SwarmOrchestrationPlugin",
    # v2: lifecycle
    "ApprovalMode",
    "HookPoint",
    "ToolCallContext",
    "HookResult",
    "LifecycleHook",
    "LifecycleManager",
    "ApprovalManager",
    "instrument_tools",
    "DangerousCommandHook",
    "FileWriteApprovalHook",
    "AuditLogHook",
    "CostBudgetHook",
    "SemanticHookBase",
    "SemanticDangerousCommandHook",
    "SemanticSecretLeakHook",
    "SemanticPatchRiskHook",
    "register_semantic_gates",
    # v0.4.1: snapshots + audit (Wave C)
    "ContentAddressedStore",
    "SnapshotRef",
    "SnapshotNotFoundError",
    "GENESIS_HASH",
    "AuditEntry",
    "AuditLog",
    "AuditRecorder",
    "ChainVerification",
    # v2: swarm
    "SwarmTask",
    "SwarmWorker",
    "SwarmResult",
    "ConsensusReport",
    "SwarmCoordinator",
    "WorkStealingQueue",
    # v2: graph
    "Entity",
    "Relation",
    "ExtractedGraph",
    "ResolutionCluster",
    "ProvenanceRecord",
    "GraphExtractor",
    "EntityResolver",
    "GraphAssembler",
    "GraphQuerier",
    "ProvenanceTracker",
    "KnowledgeGraphPipeline",
    # v0.4: reflex (System 1 tier)
    "ReflexBackend",
    "MockReflexBackend",
    "JevBackend",
    "Rubric",
    "RubricRegistry",
    "ReflexResult",
    "ReflexPrimitives",
    "EscalationEvent",
    "EscalationPolicy",
    "ReflexiveVerifier",
    "QualitativeLinter",
    "RoutableSkill",
    "RoutingDecision",
    "ReflexRouter",
    "ReflexGate",
    "AuditFinding",
    "ReflexCIRunner",
    "DecisionRecord",
    "RubricMetrics",
    "MetaRefinementAnalyzer",
    # v2: patch
    "HarnessPatch",
    "PatchOperation",
    # v2: context
    "RunContext",
    "ExecutionScope",
    # v2: agent backend
    "AgentBackend",
    "MockBackend",
    "OpenAIBackend",
    "AnthropicBackend",
    "BackendConfig",
    "Message",
    "BackendResponse",
    "BackendCapability",
    # v2: tools
    "Tool",
    "ToolSchema",
    "ToolRegistry",
    "ReadFileTool",
    "WriteFileTool",
    "RunCommandTool",
]
