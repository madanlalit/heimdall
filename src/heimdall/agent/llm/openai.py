"""
OpenAI LLM Client - OpenAI API integration for Heimdall.
"""

import logging
from typing import Any

from heimdall.agent.llm.base import BaseLLM

logger = logging.getLogger(__name__)


class OpenAILLM(BaseLLM):
    """OpenAI API client for chat completions with tool calling."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gpt-4",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        import os

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(
            api_key=api_key or os.getenv("OPENAI_API_KEY"),
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
        """Generate chat completion with optional tool calling."""
        params: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }

        if tools:
            params["tools"] = tools
            params["tool_choice"] = tool_choice

        params.update(kwargs)

        response = await self._client.chat.completions.create(**params)

        message = response.choices[0].message

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
