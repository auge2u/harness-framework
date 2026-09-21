"""Tests for the harness.agent_backend module."""
from __future__ import annotations

import pytest

from harness.agent_backend import (
    AgentBackend,
    AnthropicBackend,
    BackendCapability,
    BackendConfig,
    BackendResponse,
    Message,
    MockBackend,
    OpenAIBackend,
)


# ---------------------------------------------------------------------------
# BackendCapability enum
# ---------------------------------------------------------------------------

def test_backend_capability_values():
    """BackendCapability enum has correct values."""
    assert BackendCapability.CHAT.value == "chat"
    assert BackendCapability.COMPLETION.value == "completion"
    assert BackendCapability.STREAMING.value == "streaming"
    assert BackendCapability.FUNCTION_CALLING.value == "function_calling"
    assert BackendCapability.VISION.value == "vision"
    assert BackendCapability.REASONING.value == "reasoning"


def test_backend_capability_members():
    """BackendCapability has exactly 6 members."""
    assert len(BackendCapability) == 6


# ---------------------------------------------------------------------------
# BackendConfig
# ---------------------------------------------------------------------------

def test_backend_config_defaults():
    """BackendConfig has correct defaults."""
    cfg = BackendConfig()
    assert cfg.provider == "mock"
    assert cfg.model == "default"
    assert cfg.max_tokens == 4096
    assert cfg.temperature == 0.7
    assert cfg.thinking is False
    assert cfg.beta_flags == []
    assert cfg.api_key is None
    assert cfg.base_url is None
    assert cfg.timeout == 60.0


def test_backend_config_custom():
    """BackendConfig can be fully customized."""
    cfg = BackendConfig(
        provider="openai",
        model="gpt-4",
        max_tokens=2048,
        temperature=0.5,
        thinking=True,
        api_key="sk-test",
        base_url="https://api.example.com",
        timeout=30.0,
    )
    assert cfg.provider == "openai"
    assert cfg.model == "gpt-4"
    assert cfg.max_tokens == 2048
    assert cfg.temperature == 0.5
    assert cfg.thinking is True
    assert cfg.api_key == "sk-test"
    assert cfg.base_url == "https://api.example.com"
    assert cfg.timeout == 30.0


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------

def test_message_creation():
    """Message requires role and content."""
    msg = Message(role="user", content="Hello")
    assert msg.role == "user"
    assert msg.content == "Hello"
    assert msg.metadata == {}


def test_message_custom_metadata():
    """Message can have metadata."""
    msg = Message(role="assistant", content="Hi", metadata={"tool_call": "tc1"})
    assert msg.metadata == {"tool_call": "tc1"}


# ---------------------------------------------------------------------------
# BackendResponse
# ---------------------------------------------------------------------------

def test_backend_response_defaults():
    """BackendResponse has correct defaults."""
    resp = BackendResponse(content="Hello")
    assert resp.content == "Hello"
    assert resp.usage == {}
    assert resp.cost_usd == 0.0
    assert resp.latency_ms == 0.0
    assert resp.model == ""
    assert resp.finish_reason == ""


def test_backend_response_custom():
    """BackendResponse can be customized."""
    resp = BackendResponse(
        content="Hello",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        cost_usd=0.001,
        latency_ms=100.0,
        model="gpt-4",
        finish_reason="stop",
    )
    assert resp.content == "Hello"
    assert resp.usage["prompt_tokens"] == 10
    assert resp.cost_usd == 0.001
    assert resp.latency_ms == 100.0
    assert resp.model == "gpt-4"
    assert resp.finish_reason == "stop"


# ---------------------------------------------------------------------------
# AgentBackend ABC
# ---------------------------------------------------------------------------

def test_agent_backend_cannot_instantiate():
    """AgentBackend ABC cannot be instantiated directly."""
    with pytest.raises(TypeError):
        AgentBackend()


# ---------------------------------------------------------------------------
# MockBackend.complete()
# ---------------------------------------------------------------------------

def test_mock_backend_complete_returns_response():
    """MockBackend.complete() returns a BackendResponse."""
    backend = MockBackend()
    msgs = [Message(role="user", content="Hello")]
    resp = backend.complete(msgs)
    assert isinstance(resp, BackendResponse)
    assert "Mock response" in resp.content
    assert resp.usage["prompt_tokens"] == 10
    assert resp.model == "mock"


def test_mock_backend_tracks_call_count():
    """MockBackend tracks the number of complete() calls."""
    backend = MockBackend()
    assert backend.call_count == 0
    backend.complete([Message(role="user", content="A")])
    assert backend.call_count == 1
    backend.complete([Message(role="user", content="B")])
    assert backend.call_count == 2


def test_mock_backend_custom_responses():
    """MockBackend uses custom responses dict."""
    backend = MockBackend(responses={"hello": "world"})
    resp = backend.complete([Message(role="user", content="hello")])
    assert resp.content == "world"


def test_mock_backend_default_response_when_no_match():
    """MockBackend returns generated response when message not in responses."""
    backend = MockBackend(responses={"hello": "world"})
    resp = backend.complete([Message(role="user", content="other")])
    assert "Mock response" in resp.content


# ---------------------------------------------------------------------------
# MockBackend.stream()
# ---------------------------------------------------------------------------

def test_mock_backend_stream_yields_tokens():
    """MockBackend.stream() yields string tokens."""
    backend = MockBackend()
    backend.responses = {"test": "hello world foo"}
    msgs = [Message(role="user", content="test")]
    tokens = list(backend.stream(msgs))
    assert len(tokens) > 0
    assert all(isinstance(t, str) for t in tokens)


def test_mock_backend_stream_content():
    """MockBackend.stream() yields expected content split into words."""
    backend = MockBackend()
    backend.responses = {"test": "hello world test"}
    tokens = list(backend.stream([Message(role="user", content="test")]))
    joined = "".join(tokens).strip()
    assert "hello" in joined
    assert "world" in joined


# ---------------------------------------------------------------------------
# MockBackend.estimate_cost()
# ---------------------------------------------------------------------------

def test_mock_backend_estimate_cost():
    """MockBackend.estimate_cost() returns 0.0."""
    backend = MockBackend()
    cost = backend.estimate_cost([Message(role="user", content="test")])
    assert cost == 0.0


# ---------------------------------------------------------------------------
# MockBackend.get_capabilities()
# ---------------------------------------------------------------------------

def test_mock_backend_get_capabilities():
    """MockBackend.get_capabilities() returns its declared capabilities."""
    backend = MockBackend()
    caps = backend.get_capabilities()
    assert BackendCapability.CHAT in caps
    assert BackendCapability.COMPLETION in caps


# ---------------------------------------------------------------------------
# OpenAIBackend stubs
# ---------------------------------------------------------------------------

def test_openai_backend_raises_on_complete():
    """OpenAIBackend.complete() raises NotImplementedError."""
    backend = OpenAIBackend()
    with pytest.raises(NotImplementedError):
        backend.complete([Message(role="user", content="test")])


def test_openai_backend_raises_on_stream():
    """OpenAIBackend.stream() raises NotImplementedError."""
    backend = OpenAIBackend()
    with pytest.raises(NotImplementedError):
        list(backend.stream([Message(role="user", content="test")]))


def test_openai_backend_name():
    """OpenAIBackend has correct name."""
    assert OpenAIBackend.name == "openai"


def test_openai_backend_get_capabilities():
    """OpenAIBackend.get_capabilities() returns capabilities."""
    backend = OpenAIBackend()
    caps = backend.get_capabilities()
    assert len(caps) > 0


# ---------------------------------------------------------------------------
# AnthropicBackend stubs
# ---------------------------------------------------------------------------

def test_anthropic_backend_raises_on_complete():
    """AnthropicBackend.complete() raises NotImplementedError."""
    backend = AnthropicBackend()
    with pytest.raises(NotImplementedError):
        backend.complete([Message(role="user", content="test")])


def test_anthropic_backend_raises_on_stream():
    """AnthropicBackend.stream() raises NotImplementedError."""
    backend = AnthropicBackend()
    with pytest.raises(NotImplementedError):
        list(backend.stream([Message(role="user", content="test")]))


def test_anthropic_backend_name():
    """AnthropicBackend has correct name."""
    assert AnthropicBackend.name == "anthropic"


def test_anthropic_backend_get_capabilities():
    """AnthropicBackend.get_capabilities() returns capabilities."""
    backend = AnthropicBackend()
    caps = backend.get_capabilities()
    assert len(caps) > 0
