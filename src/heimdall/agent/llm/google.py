"""
Google Gemini LLM Client - Google AI Gemini API integration for Heimdall.
"""

import base64
import binascii
import importlib
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
        try:
            genai_module = importlib.import_module("google.genai")
            self._types = importlib.import_module("google.genai.types")
        except ImportError as err:
            raise ImportError(
                "google-genai is required for the Google provider. "
                'Install with: pip install "heimdall[google]"'
            ) from err

        self._client = genai_module.Client(
            api_key=api_key or os.getenv("GOOGLE_API_KEY"),
        )
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    @staticmethod
    def _data_url_to_part(data_url: str, types_module: Any) -> Any:
        """Convert data URL image to Gemini Part."""
        if not data_url.startswith("data:"):
            return types_module.Part.from_uri(file_uri=data_url)

        header, encoded = data_url.split(",", 1)
        if ";base64" not in header:
            raise ValueError("Only base64 data URLs are supported for image_url parts")

        mime_type = header[5:].replace(";base64", "")
        if not mime_type:
            raise ValueError("Missing MIME type in data URL")

        try:
            image_bytes = base64.b64decode(encoded)
        except binascii.Error as exc:
            raise ValueError("Invalid base64 image data in data URL") from exc

        return types_module.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    def _message_content_to_parts(self, content: Any, types_module: Any) -> list[Any]:
        """Convert OpenAI-style message content into Gemini parts."""
        if isinstance(content, str):
            return [types_module.Part.from_text(text=content)]

        if not isinstance(content, list):
            raise TypeError(f"Unsupported message content type: {type(content).__name__}")

        parts: list[Any] = []
        for item in content:
            if isinstance(item, str):
                parts.append(types_module.Part.from_text(text=item))
                continue

            if not isinstance(item, dict):
                raise TypeError(f"Unsupported message content item type: {type(item).__name__}")

            item_type = item.get("type")
            if item_type == "text":
                text = item.get("text", "")
                if not isinstance(text, str):
                    raise TypeError("Text content must be a string")
                parts.append(types_module.Part.from_text(text=text))
                continue

            if item_type == "image_url":
                image_url = item.get("image_url")
                if not isinstance(image_url, dict):
                    raise TypeError("image_url content must be an object")
                url = image_url.get("url")
                if not isinstance(url, str):
                    raise TypeError("image_url.url must be a string")
                parts.append(self._data_url_to_part(url, types_module))
                continue

            raise ValueError(f"Unsupported message content part type: {item_type}")

        return parts

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
        response_schema = kwargs.pop("response_schema", None)
        types_module = self._types

        # Convert messages to Gemini format
        system_instruction = None
        gemini_contents: list[Any] = []

        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "system":
                system_instruction = content
            elif role == "user":
                gemini_contents.append(
                    types_module.Content(
                        role="user",
                        parts=self._message_content_to_parts(content, types_module),
                    )
                )
            elif role == "assistant":
                gemini_contents.append(
                    types_module.Content(
                        role="model",
                        parts=self._message_content_to_parts(content, types_module),
                    )
                )
            elif role == "tool":
                # Tool results need to be handled specially
                gemini_contents.append(
                    types_module.Content(
                        role="user",
                        parts=[
                            types_module.Part.from_function_response(
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
                    types_module.FunctionDeclaration(
                        name=func["name"],
                        description=func.get("description", ""),
                        parameters=func.get("parameters"),
                    )
                )
            gemini_tools = [types_module.Tool(function_declarations=function_declarations)]

        # Configure tool usage
        tool_config = None
        if tools:
            if tool_choice == "required":
                tool_config = types_module.ToolConfig(
                    function_calling_config=types_module.FunctionCallingConfig(
                        mode=types_module.FunctionCallingConfigMode.ANY
                    )
                )
            elif tool_choice == "none":
                tool_config = types_module.ToolConfig(
                    function_calling_config=types_module.FunctionCallingConfig(
                        mode=types_module.FunctionCallingConfigMode.NONE
                    )
                )
            else:  # auto
                tool_config = types_module.ToolConfig(
                    function_calling_config=types_module.FunctionCallingConfig(
                        mode=types_module.FunctionCallingConfigMode.AUTO
                    )
                )

        # Build generation config
        generation_config = types_module.GenerateContentConfig(
            temperature=self._temperature,
            max_output_tokens=self._max_tokens,
            system_instruction=system_instruction,
            tools=gemini_tools,
            tool_config=tool_config,
            response_schema=response_schema,
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
