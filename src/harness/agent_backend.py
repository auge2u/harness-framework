"""Agent backend abstraction for framework-agnostic model interaction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional


class BackendCapability(Enum):
    """Capabilities that an agent backend may support."""

    CHAT = "chat"
    COMPLETION = "completion"
    STREAMING = "streaming"
    FUNCTION_CALLING = "function_calling"
    VISION = "vision"
    REASONING = "reasoning"


@dataclass
class BackendConfig:
    """Configuration for an agent backend.

    Attributes:
        provider: Backend provider identifier -- ``"mock"``, ``"openai"``,
            ``"anthropic"``, etc.
        model: Model name or identifier.
        max_tokens: Maximum tokens to generate.
        temperature: Sampling temperature in ``[0, 2]``.
        thinking: Whether to enable extended thinking/reasoning mode.
        beta_flags: List of provider-specific beta feature flags.
        api_key: Optional API key (falls back to env vars if omitted).
        base_url: Optional custom base URL for the API endpoint.
        timeout: Request timeout in seconds.
    """

    provider: str = "mock"
    model: str = "default"
    max_tokens: int = 4096
    temperature: float = 0.7
    thinking: bool = False
    beta_flags: List[str] = field(default_factory=list)
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout: float = 60.0

    def __post_init__(self):
        if self.beta_flags is None:
            self.beta_flags = []


@dataclass
class Message:
    """A single message in a conversation.

    Attributes:
        role: Message role -- ``"system"``, ``"user"``, ``"assistant"``,
            or ``"tool"``.
        content: Text content of the message.
        metadata: Arbitrary per-message metadata (e.g., tool call IDs,
            citations, attachments).
    """

    role: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class BackendResponse:
    """Response from a backend.

    Attributes:
        content: Generated text content.
        usage: Token usage statistics with keys ``prompt_tokens``,
            ``completion_tokens``, and ``total_tokens``.
        cost_usd: Estimated cost in US dollars.
        latency_ms: Request latency in milliseconds.
        model: Model identifier that produced the response.
        finish_reason: Reason the generation stopped -- ``"stop"``,
            ``"length"``, ``"content_filter"``, etc.
        metadata: Arbitrary per-response metadata.
    """

    content: str
    usage: Dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    model: str = ""
    finish_reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.usage is None:
            self.usage = {}
        if self.metadata is None:
            self.metadata = {}
        self.cost_usd = float(self.cost_usd)
        self.latency_ms = float(self.latency_ms)


class AgentBackend(ABC):
    """Abstract base for all agent backends.

    Concrete subclasses must implement :meth:`complete` and :meth:`stream`
    and declare their supported :class:`BackendCapability` values.
    """

    name: str = ""
    capabilities: List[BackendCapability] = []

    @abstractmethod
    def complete(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> BackendResponse:
        """Send messages and get a complete response.

        Args:
            messages: Ordered list of conversation messages.
            config: Optional backend-specific configuration override.

        Returns:
            A :class:`BackendResponse` containing the generated content
            and metadata.
        """
        ...

    @abstractmethod
    def stream(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> Iterator[str]:
        """Stream response tokens.

        Args:
            messages: Ordered list of conversation messages.
            config: Optional backend-specific configuration override.

        Yields:
            Individual token strings as they are generated.
        """
        ...

    @abstractmethod
    def get_capabilities(self) -> List[BackendCapability]:
        """Return the capabilities supported by this backend.

        Returns:
            List of :class:`BackendCapability` values.
        """
        ...

    def estimate_cost(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> float:
        """Estimate cost in USD for a request.

        Base implementation returns ``0.0``.  Subclasses that have
        access to a pricing model should override this method.

        Args:
            messages: Messages that will be sent.
            config: Optional backend configuration.

        Returns:
            Estimated cost in US dollars.
        """
        return 0.0


# ---------------------------------------------------------------------------
# Mock backend (for testing)
# ---------------------------------------------------------------------------


class MockBackend(AgentBackend):
    """Deterministic mock backend for testing.

    Returns canned responses based on message content lookup.
    Tracks call counts for test assertions.
    """

    name = "mock"
    capabilities = [BackendCapability.CHAT, BackendCapability.COMPLETION]

    def __init__(self, responses: Optional[Dict[str, str]] = None):
        self.responses = responses or {}
        self.call_count = 0

    def complete(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> BackendResponse:
        self.call_count += 1
        key = messages[-1].content if messages else "default"
        content = self.responses.get(
            key, f"Mock response #{self.call_count} for: {key[:50]}"
        )
        return BackendResponse(
            content=content,
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            },
            cost_usd=0.001,
            model="mock",
        )

    def stream(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> Iterator[str]:
        content = self.complete(messages, config).content
        for word in content.split():
            yield word + " "

    def get_capabilities(self) -> List[BackendCapability]:
        return self.capabilities


# ---------------------------------------------------------------------------
# OpenAI backend (stub -- interface only)
# ---------------------------------------------------------------------------


class OpenAIBackend(AgentBackend):
    """OpenAI API backend.

    **Interface only** -- requires the ``openai`` package to be installed.
    Implement :meth:`complete` and :meth:`stream` after installing
    ``openai`` and setting ``OPENAI_API_KEY``.
    """

    name = "openai"
    capabilities = [
        BackendCapability.CHAT,
        BackendCapability.STREAMING,
        BackendCapability.FUNCTION_CALLING,
    ]

    def __init__(self, config: Optional[BackendConfig] = None):
        self.config = config or BackendConfig(provider="openai")

    def complete(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> BackendResponse:
        raise NotImplementedError(
            "OpenAI backend requires the 'openai' package. "
            "Install it with: pip install openai"
        )

    def stream(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> Iterator[str]:
        raise NotImplementedError(
            "OpenAI backend requires the 'openai' package. "
            "Install it with: pip install openai"
        )

    def get_capabilities(self) -> List[BackendCapability]:
        return self.capabilities


# ---------------------------------------------------------------------------
# Anthropic backend (stub -- interface only)
# ---------------------------------------------------------------------------


class AnthropicBackend(AgentBackend):
    """Anthropic API backend.

    **Interface only** -- requires the ``anthropic`` package to be installed.
    Implement :meth:`complete` and :meth:`stream` after installing
    ``anthropic`` and setting ``ANTHROPIC_API_KEY``.
    """

    name = "anthropic"
    capabilities = [
        BackendCapability.CHAT,
        BackendCapability.STREAMING,
        BackendCapability.REASONING,
    ]

    def __init__(self, config: Optional[BackendConfig] = None):
        self.config = config or BackendConfig(provider="anthropic")

    def complete(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> BackendResponse:
        raise NotImplementedError(
            "Anthropic backend requires the 'anthropic' package. "
            "Install it with: pip install anthropic"
        )

    def stream(
        self,
        messages: List[Message],
        config: Optional[BackendConfig] = None,
    ) -> Iterator[str]:
        raise NotImplementedError(
            "Anthropic backend requires the 'anthropic' package. "
            "Install it with: pip install anthropic"
        )

    def get_capabilities(self) -> List[BackendCapability]:
        return self.capabilities
