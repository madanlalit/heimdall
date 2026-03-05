"""
Ollama LLM Client - Local Ollama integration for Heimdall.

Uses Ollama's OpenAI-compatible endpoint.
"""

import json
import logging
import os
from typing import Any

from heimdall.agent.llm.base import BaseLLM

logger = logging.getLogger(__name__)


def _normalize_ollama_base_url(base_url: str) -> str:
    """Normalize an Ollama host/base URL for OpenAI-compatible /v1 requests."""
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


class OllamaLLM(BaseLLM):
    """Ollama client for chat completions with tool calling."""

    OLLAMA_BASE_URL = "http://localhost:11434/v1"

    def __init__(
        self,
        model: str = "llama3.2",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        import importlib

        try:
            openai_module = importlib.import_module("openai")
        except ImportError as err:
            raise ImportError(
                "openai is required for the Ollama provider. "
                'Install with: pip install "heimdall[ollama]"'
            ) from err

        AsyncOpenAI = openai_module.AsyncOpenAI

        resolved_base_url = (
            base_url
            or os.getenv("OLLAMA_BASE_URL")
            or os.getenv("OLLAMA_HOST")
            or self.OLLAMA_BASE_URL
        )

        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OLLAMA_API_KEY") or "ollama",
            base_url=_normalize_ollama_base_url(resolved_base_url),
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        **kwargs,
    ) -> dict:
        """Generate chat completion with optional tool calling or response schema."""
        response_schema = kwargs.pop("response_schema", None)

        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }

        if response_schema:
            params["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "agent_output",
                    "schema": response_schema,
                },
            }
        elif tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        params.update(kwargs)

        response = await self._client.chat.completions.create(**params)
        message = response.choices[0].message

        result: dict[str, Any] = {
            "content": message.content or "",
        }

        if message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                arguments = tc.function.arguments
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments)

                tool_calls.append(
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": arguments,
                        },
                    }
                )
            result["tool_calls"] = tool_calls

        return result

    async def close(self) -> None:
        """Close client."""
        await self._client.close()


class OllamaClient(OllamaLLM):
    """Backward-compatible alias for OllamaLLM."""
