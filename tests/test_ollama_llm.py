"""Unit tests for OllamaLLM and factory wiring."""

import json
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_response(content: str = "", tool_calls: list | None = None):
    message = types.SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _make_tool_call(name: str, arguments):
    return types.SimpleNamespace(
        id="call_1",
        type="function",
        function=types.SimpleNamespace(name=name, arguments=arguments),
    )


def _make_llm(**kwargs):
    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = AsyncMock(return_value=_make_response("ok"))
    mock_client.close = AsyncMock()

    mock_async_openai = MagicMock(return_value=mock_client)
    fake_openai = types.SimpleNamespace(AsyncOpenAI=mock_async_openai)

    with patch.dict(sys.modules, {"openai": fake_openai}):
        from heimdall.agent.llm.ollama import OllamaLLM

        llm = OllamaLLM(**kwargs)

    return llm, mock_client, mock_async_openai


class TestOllamaLLM:
    def test_init_uses_default_base_url_and_api_key(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

        _, _, mock_async_openai = _make_llm()

        mock_async_openai.assert_called_once_with(
            api_key="ollama",
            base_url="http://localhost:11434/v1",
        )

    def test_init_normalizes_ollama_host_env(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
        monkeypatch.delenv("OLLAMA_API_KEY", raising=False)

        _, _, mock_async_openai = _make_llm()

        assert mock_async_openai.call_args.kwargs["base_url"] == "http://127.0.0.1:11434/v1"

    @pytest.mark.asyncio
    async def test_chat_completion_maps_response_schema(self):
        llm, mock_client, _ = _make_llm(model="qwen2.5:7b")
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

        result = await llm.chat_completion(
            messages=[{"role": "user", "content": "hello"}],
            tools=[{"function": {"name": "click", "parameters": {"type": "object"}}}],
            response_schema=schema,
        )

        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "qwen2.5:7b"
        assert call_kwargs["response_format"]["json_schema"]["schema"] == schema
        assert "tools" not in call_kwargs
        assert result["content"] == "ok"

    @pytest.mark.asyncio
    async def test_chat_completion_parses_tool_calls(self):
        llm, mock_client, _ = _make_llm()
        mock_client.chat.completions.create = AsyncMock(
            return_value=_make_response(
                tool_calls=[_make_tool_call("click", {"index": 3})],
            )
        )

        result = await llm.chat_completion(messages=[{"role": "user", "content": "click item"}])

        assert len(result["tool_calls"]) == 1
        tool_call = result["tool_calls"][0]
        assert tool_call["function"]["name"] == "click"
        assert json.loads(tool_call["function"]["arguments"]) == {"index": 3}

    @pytest.mark.asyncio
    async def test_close_calls_client_close(self):
        llm, mock_client, _ = _make_llm()
        await llm.close()
        mock_client.close.assert_awaited_once()


class TestFactory:
    def test_create_llm_client_returns_ollama_instance(self):
        mock_client = MagicMock()
        mock_async_openai = MagicMock(return_value=mock_client)
        fake_openai = types.SimpleNamespace(AsyncOpenAI=mock_async_openai)

        with patch.dict(sys.modules, {"openai": fake_openai}):
            from heimdall.agent.factory import create_llm_client
            from heimdall.agent.llm.ollama import OllamaLLM

            llm = create_llm_client(provider="ollama")  # type: ignore[arg-type]

            assert isinstance(llm, OllamaLLM)

    def test_auto_provider_resolves_ollama_when_host_is_configured(self, monkeypatch):
        from heimdall.agent import factory

        monkeypatch.setattr(factory, "_module_available", lambda module: module == "openai")
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        assert factory._resolve_auto_provider() == "ollama"

    def test_auto_provider_prefers_openai_key_over_ollama_host(self, monkeypatch):
        from heimdall.agent import factory

        monkeypatch.setattr(factory, "_module_available", lambda module: module == "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.setenv("OLLAMA_HOST", "http://localhost:11434")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        assert factory._resolve_auto_provider() == "openai"

