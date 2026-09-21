"""Plugin base class and surface-specific plugin ABCs for the Harness framework."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class TrustLevel(Enum):
    """Trust levels for plugins, controlling sandbox permissions."""

    BUILTIN = "builtin"       # Core framework plugins, fully trusted
    VENDORED = "vendored"     # Third-party but reviewed/approved
    COMMUNITY = "community"   # Community submitted, sandboxed
    UNTRUSTED = "untrusted"   # Experimental, heavily restricted


@dataclass
class PluginCapabilities:
    """Capabilities advertised by a plugin.

    Attributes:
        can_read: Surface names this plugin can read from.
        can_write: Surface names this plugin can modify.
        can_execute: Whether the plugin can run commands or external tools.
        can_access_network: Whether the plugin can make network calls.
        can_access_filesystem: Whether the plugin can read/write files.
        can_access_secrets: Whether the plugin can access secret stores.
        requires_human_approval: Surface names requiring human approval before
            the plugin can operate on them.
    """

    can_read: List[str] = field(default_factory=list)
    can_write: List[str] = field(default_factory=list)
    can_execute: bool = False
    can_access_network: bool = False
    can_access_filesystem: bool = False
    can_access_secrets: bool = False
    requires_human_approval: List[str] = field(default_factory=list)


@dataclass
class PluginContext:
    """Context passed to plugins during execution.

    Attributes:
        tenant: Tenant identifier for multi-tenant deployments.
        run_id: Unique identifier for the current harness run.
        lineage_version: Optional lineage version string for tracking.
        policy_constraints: Dict of policy constraints to enforce.
        metadata: Arbitrary metadata for the plugin execution.
    """

    tenant: str = "default"
    run_id: str = ""
    lineage_version: Optional[str] = None
    policy_constraints: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Plugin(ABC):
    """Base class for all Harness plugins.

    Every plugin declares a *name*, *version*, target *surface(s)*,
    *trust level*, *capabilities*, and implements :meth:`apply` and
    :meth:`validate`.

    Attributes:
        name: Human-readable plugin identifier.
        version: Semantic version string.
        surfaces: Which harness surfaces this plugin targets.
        trust_level: Trust level controlling sandbox permissions.
        capabilities: Fine-grained capability advertisement.
        dependencies: Names of other plugins this plugin depends on.
        enabled: Whether the plugin is active.
    """

    name: str = ""
    version: str = "0.1.0"
    surfaces: List[str] = field(default_factory=list)
    trust_level: TrustLevel = field(default=TrustLevel.COMMUNITY)
    capabilities: PluginCapabilities = field(default_factory=PluginCapabilities)
    dependencies: List[str] = field(default_factory=list)
    enabled: bool = True

    @abstractmethod
    def apply(self, harness_config: Any, context: PluginContext) -> Dict[str, Any]:
        """Apply this plugin's transformation to the harness config.

        Args:
            harness_config: The current harness configuration object.
            context: Execution context for this plugin run.

        Returns:
            A dict with at minimum ``status`` (``"success"`` or ``"failure"``)
            and optionally:

            - ``patches``: list of :class:`~harness.patch.HarnessPatch` objects
            - ``metrics``: dict of arbitrary metric key/value pairs
            - ``artifacts``: dict of generated artifacts
        """
        ...

    @abstractmethod
    def validate(self, harness_config: Any) -> List[str]:
        """Validate that the plugin can operate on the given config.

        Args:
            harness_config: The current harness configuration object.

        Returns:
            List of validation error messages.  An empty list means the
            plugin is valid for the given configuration.
        """
        ...

    def get_capabilities(self) -> PluginCapabilities:
        """Return this plugin's capabilities."""
        return self.capabilities

    def check_permission(self, surface: str, operation: str = "read") -> bool:
        """Check if this plugin has permission for an operation on a surface.

        Args:
            surface: Name of the harness surface to check.
            operation: One of ``"read"``, ``"write"``, or ``"execute"``.

        Returns:
            ``True`` if the plugin has the requested permission.
        """
        if operation == "read":
            return (
                surface in self.capabilities.can_read
                or surface in self.capabilities.can_write
            )
        elif operation == "write":
            return surface in self.capabilities.can_write
        elif operation == "execute":
            return self.capabilities.can_execute
        return False


# ---------------------------------------------------------------------------
# Surface-specific plugin ABCs
# ---------------------------------------------------------------------------


@dataclass
class IdentityPlugin(Plugin):
    """Plugin targeting the ``identity`` surface.

    Manages agent identity — name, persona, behavioural constraints,
    and self-description.
    """

    name: str = "identity"
    surfaces: List[str] = field(default_factory=lambda: ["identity"])


@dataclass
class InstructionPlugin(Plugin):
    """Plugin targeting the ``instructions`` surface.

    Manages system instructions, prompt fragments, and directive
    hierarchies that guide agent behaviour.
    """

    name: str = "instructions"
    surfaces: List[str] = field(default_factory=lambda: ["instructions"])


@dataclass
class ToolPlugin(Plugin):
    """Plugin targeting the ``tools`` surface.

    Manages tool descriptors, tool availability, and tool-schemas
    exposed to the agent.
    """

    name: str = "tools"
    surfaces: List[str] = field(default_factory=lambda: ["tools"])


@dataclass
class SkillPlugin(Plugin):
    """Plugin targeting the ``skills`` surface.

    Manages skill definitions, skill composition, and skill
    activation rules.
    """

    name: str = "skills"
    surfaces: List[str] = field(default_factory=lambda: ["skills"])


@dataclass
class McpPlugin(Plugin):
    """Plugin targeting the ``mcps`` surface.

    Manages Model Context Protocol (MCP) server configurations,
    connections, and available MCP resources.
    """

    name: str = "mcps"
    surfaces: List[str] = field(default_factory=lambda: ["mcps"])


@dataclass
class MemoryPlugin(Plugin):
    """Plugin targeting the ``memory`` surface.

    Manages memory schemas, retrieval strategies, persistence
    layers, and episodic/semantic memory configuration.
    """

    name: str = "memory"
    surfaces: List[str] = field(default_factory=lambda: ["memory"])


@dataclass
class SandboxPlugin(Plugin):
    """Plugin targeting the ``sandbox`` surface.

    Manages sandbox policies — allowed paths, blocked commands,
    resource limits, and execution constraints.
    """

    name: str = "sandbox"
    surfaces: List[str] = field(default_factory=lambda: ["sandbox"])


@dataclass
class BackendPlugin(Plugin):
    """Plugin targeting the ``model_defaults`` surface.

    Manages default backend configuration — provider, model,
    temperature, max-tokens, and provider-specific flags.
    """

    name: str = "model_defaults"
    surfaces: List[str] = field(default_factory=lambda: ["model_defaults"])


@dataclass
class RouterPlugin(Plugin):
    """Plugin targeting the ``routing`` surface.

    Manages request routing rules — model selection, fallback
    chains, load-balancing, and latency-based routing.
    """

    name: str = "routing"
    surfaces: List[str] = field(default_factory=lambda: ["routing"])


@dataclass
class OrchestrationPlugin(Plugin):
    """Plugin targeting the ``orchestration`` surface.

    Manages orchestration policies — parallelisation, sequencing,
    dependency graphs, and workflow patterns.
    """

    name: str = "orchestration"
    surfaces: List[str] = field(default_factory=lambda: ["orchestration"])


@dataclass
class DataGatewayPlugin(Plugin):
    """Plugin targeting the ``data_gateway`` surface.

    Manages data gateway configuration — sources, connectors,
    caching policies, and data-flow rules.
    """

    name: str = "data_gateway"
    surfaces: List[str] = field(default_factory=lambda: ["data_gateway"])


@dataclass
class VerifierPlugin(Plugin):
    """Plugin targeting the ``evaluator`` surface.

    Manages verifier configuration — check types, thresholds,
    composite rules, and evaluation policies.
    """

    name: str = "evaluator"
    surfaces: List[str] = field(default_factory=lambda: ["evaluator"])


@dataclass
class ReporterPlugin(Plugin):
    """Plugin targeting the ``telemetry`` surface.

    Manages telemetry and reporting configuration — metrics
    collection, sinks, dashboards, and alerting rules.
    """

    name: str = "telemetry"
    surfaces: List[str] = field(default_factory=lambda: ["telemetry"])


@dataclass
class ArtifactPlugin(Plugin):
    """Plugin targeting the ``artifacts`` surface.

    Manages artifact storage configuration — retention policies,
    storage backends, and artifact type registrations.
    """

    name: str = "artifacts"
    surfaces: List[str] = field(default_factory=lambda: ["artifacts"])


@dataclass
class SecretsPlugin(Plugin):
    """Plugin targeting the ``secrets_policy`` surface.

    Manages secrets access policies — which secrets are available,
    rotation rules, and secret provider configuration.
    """

    name: str = "secrets_policy"
    surfaces: List[str] = field(default_factory=lambda: ["secrets_policy"])


@dataclass
class PolicyPlugin(Plugin):
    """Plugin targeting the ``policy_engine`` surface.

    Manages the policy engine itself — rule sets, guardrails,
    enforcement modes, and policy composition.
    """

    name: str = "policy_engine"
    surfaces: List[str] = field(default_factory=lambda: ["policy_engine"])
