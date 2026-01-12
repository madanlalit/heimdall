"""
Google Gemini LLM Client - Google AI Gemini API integration for Heimdall.
"""

import logging
import os
from typing import Any

from heimdall.agent.llm.base import BaseLLM

logger = logging.getLogger(__name__)


class GoogleLLM(BaseLLM):
    """Google Gemini API client for chat completions with tool calling."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "gemini-2.0-flash",
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        """
        Initialize Google Gemini client.

        Args:
            api_key: Google AI API key (or set GOOGLE_API_KEY env var)
            model: Model identifier (e.g., 'gemini-2.0-flash', 'gemini-1.5-pro')
            temperature: Sampling temperature
            max_tokens: Max tokens to generate
        """
        from google import genai

        self._client = genai.Client(
            api_key=api_key or os.getenv("GOOGLE_API_KEY"),
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate chat completion with optional tool calling.

        Args:
            messages: Chat messages
            tools: Optional tool definitions
            tool_choice: Tool choice mode ('auto', 'required', 'none')
        """
        from google.genai import types

        # Convert messages to Gemini format
        system_instruction = None
        gemini_contents: list[types.Content] = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                system_instruction = content
            elif role == "user":
                gemini_contents.append(
                    types.Content(role="user", parts=[types.Part.from_text(text=content)])
                )
            elif role == "assistant":
                gemini_contents.append(
                    types.Content(role="model", parts=[types.Part.from_text(text=content)])
                )
            elif role == "tool":
                # Tool results need to be handled specially
                gemini_contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=msg.get("name", "tool"),
                                response={"result": content},
                            )
                        ],
                    )
                )

        # Convert tools to Gemini format
        gemini_tools = None
        if tools:
            function_declarations = []
            for tool in tools:
                func = tool["function"]
                function_declarations.append(
                    types.FunctionDeclaration(
                        name=func["name"],
                        description=func.get("description", ""),
                        parameters=func.get("parameters"),
                    )
                )
            gemini_tools = [types.Tool(function_declarations=function_declarations)]

        # Configure tool usage
        tool_config = None
        if tools:
            if tool_choice == "required":
                tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="ANY")
                )
            elif tool_choice == "none":
                tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="NONE")
                )
            else:  # auto
                tool_config = types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(mode="AUTO")
                )

        # Build generation config
        generation_config = types.GenerateContentConfig(
            temperature=self._temperature,
            max_output_tokens=self._max_tokens,
            system_instruction=system_instruction,
            tools=gemini_tools,
            tool_config=tool_config,
        )

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=gemini_contents,
            config=generation_config,
        )

        if response.usage_metadata:
            logger.info(
                f"Tokens: {response.usage_metadata.prompt_token_count} in, "
                f"{response.usage_metadata.candidates_token_count} out, "
                f"{response.usage_metadata.total_token_count} total"
            )

        # Parse response
        result: dict[str, Any] = {"content": ""}

        if response.candidates and response.candidates[0].content:
            candidate = response.candidates[0]
            for part in candidate.content.parts:
                if part.text:
                    result["content"] = part.text
                elif part.function_call:
                    if "tool_calls" not in result:
                        result["tool_calls"] = []
                    result["tool_calls"].append(
                        {
                            "id": f"call_{len(result['tool_calls'])}",
                            "type": "function",
                            "function": {
                                "name": part.function_call.name,
                                "arguments": part.function_call.args,
                            },
                        }
                    )

        return result

    async def close(self) -> None:
        """Close client (no-op for Gemini as it doesn't require explicit cleanup)."""
        pass
