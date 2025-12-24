"""
Anthropic LLM Client - Claude API integration for Heimdall.
"""

import logging
from typing import Any

from heimdall.agent.llm.base import BaseLLM

logger = logging.getLogger(__name__)


class AnthropicLLM(BaseLLM):
    """Anthropic Claude API client for chat completions with tool calling."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        import os

        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY"),
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
        # Convert messages format for Anthropic
        system_msg = ""
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_msg = msg["content"]
            else:
                anthropic_messages.append(msg)

        # Convert tools to Anthropic format
        anthropic_tools = None
        if tools:
            anthropic_tools = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        params: dict[str, Any] = {
            "model": self._model,
            "messages": anthropic_messages,
            "max_tokens": self._max_tokens,
            "temperature": self._temperature,
        }

        if system_msg:
            params["system"] = system_msg

        if anthropic_tools:
            params["tools"] = anthropic_tools
            if tool_choice == "auto":
                params["tool_choice"] = {"type": "auto"}
            elif tool_choice == "required":
                params["tool_choice"] = {"type": "any"}

        params.update(kwargs)

        response = await self._client.messages.create(**params)

        result: dict[str, Any] = {"content": "", "tool_calls": []}

        for block in response.content:
            if block.type == "text":
                result["content"] = block.text
            elif block.type == "tool_use":
                result["tool_calls"].append(
                    {
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": block.input,
                        },
                    }
                )

        if not result["tool_calls"]:
            del result["tool_calls"]

        return result

    async def close(self) -> None:
        """Close client."""
        await self._client.close()
