"""
OpenRouter LLM Client - OpenRouter API integration for Heimdall.

OpenRouter provides access to multiple models (Claude, GPT-4, Llama, etc.)
through an OpenAI-compatible API.
"""

import logging
import os
from typing import Any

from heimdall.agent.llm.base import BaseLLM

logger = logging.getLogger(__name__)


class OpenRouterLLM(BaseLLM):
    """
    OpenRouter API client for chat completions with tool calling.

    OpenRouter provides a unified API for multiple model providers.
    Uses OpenAI-compatible API with a different base URL.

    Models available include:
    - anthropic/claude-3.5-sonnet
    - openai/gpt-4-turbo
    - meta-llama/llama-3.1-70b-instruct
    - google/gemini-pro-1.5
    - and many more at https://openrouter.ai/models
    """

    OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "google/gemini-3-flash-preview",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        site_url: str | None = None,
        site_name: str = "Heimdall",
    ):
        """
        Initialize OpenRouter client.

        Args:
            api_key: OpenRouter API key (or set OPENROUTER_API_KEY env var)
            model: Model identifier (e.g., 'anthropic/claude-3.5-sonnet')
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
            site_url: Optional URL for rankings on openrouter.ai
            site_name: App name shown in OpenRouter dashboard
        """
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENROUTER_API_KEY"),
            base_url=self.OPENROUTER_BASE_URL,
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._site_url = site_url
        self._site_name = site_name

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate chat completion with optional tool calling."""
        # Build extra headers for OpenRouter
        extra_headers = {
            "X-Title": self._site_name,
        }

        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "extra_headers": extra_headers,
        }

        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        params.update(kwargs)

        # Log request
        logger.debug(f"LLM Request - {len(messages)} messages, {len(tools) if tools else 0} tools")
        for msg in messages:
            role = msg.get("role", "?")
            content = msg.get("content", "")[:500]
            logger.debug(f"  [{role}]: {content}...")

        response = await self._client.chat.completions.create(**params)

        # Log token usage
        if response.usage:
            logger.info(
                f"Tokens: {response.usage.prompt_tokens} in, "
                f"{response.usage.completion_tokens} out, "
                f"{response.usage.total_tokens} total"
            )

        message = response.choices[0].message

        # Log response content
        logger.debug(
            f"LLM Response: {message.content[:500] if message.content else '(no content)'}"
        )
        if message.tool_calls:
            for tc in message.tool_calls:
                logger.info(f"Tool call: {tc.function.name}({tc.function.arguments[:100]}...)")

        result: dict[str, Any] = {
            "content": message.content or "",
        }

        if message.tool_calls:
            result["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return result

    async def close(self) -> None:
        """Close client."""
        await self._client.close()
