"""
AWS Bedrock LLM Client - Amazon Bedrock API integration for Heimdall.

Bedrock provides access to foundation models from Anthropic, AI21, Cohere,
Meta, Mistral, and Amazon through a unified AWS API.

Credentials are resolved via the standard AWS credential chain:
  - Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
  - ~/.aws/credentials / ~/.aws/config
  - IAM roles (e.g. EC2 instance profile, ECS task role, Lambda execution role)

Set AWS_DEFAULT_REGION (or pass region_name) to choose the Bedrock endpoint region.
"""

import base64
import binascii
import json
import logging
import os
from typing import Any

from heimdall.agent.llm.base import BaseLLM

logger = logging.getLogger(__name__)


class BedrockLLM(BaseLLM):
    """
    Amazon Bedrock API client for chat completions with tool calling.

    Uses the Bedrock ``converse`` API which provides a unified interface for
    text generation and tool use across all supported model families.

    Supported model IDs (examples):
      - anthropic.claude-3-5-sonnet-20241022-v2:0
      - anthropic.claude-3-haiku-20240307-v1:0
      - meta.llama3-70b-instruct-v1:0
      - amazon.nova-pro-v1:0
      - mistral.mistral-large-2402-v1:0
    """

    def __init__(
        self,
        model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ):
        """
        Initialise the Bedrock client.

        Args:
            model: Bedrock model ID.
            region_name: AWS region (falls back to AWS_DEFAULT_REGION / AWS_REGION env vars,
                then 'us-east-1').
            aws_access_key_id: AWS access key (falls back to AWS_ACCESS_KEY_ID env var).
            aws_secret_access_key: AWS secret key (falls back to AWS_SECRET_ACCESS_KEY env var).
            aws_session_token: AWS session token (falls back to AWS_SESSION_TOKEN env var).
            temperature: Sampling temperature (0.0 = deterministic).
            max_tokens: Maximum tokens to generate.
        """
        try:
            import boto3
        except ImportError as err:
            raise ImportError(
                "boto3 is required for the Bedrock provider. "
                'Install with: pip install "heimdall[bedrock]"'
            ) from err

        resolved_region = (
            region_name
            or os.getenv("AWS_DEFAULT_REGION")
            or os.getenv("AWS_REGION")
            or "us-east-1"
        )

        session_kwargs: dict[str, str] = {}
        if aws_access_key_id or os.getenv("AWS_ACCESS_KEY_ID"):
            session_kwargs["aws_access_key_id"] = aws_access_key_id or os.getenv(
                "AWS_ACCESS_KEY_ID", ""
            )
        if aws_secret_access_key or os.getenv("AWS_SECRET_ACCESS_KEY"):
            session_kwargs["aws_secret_access_key"] = aws_secret_access_key or os.getenv(
                "AWS_SECRET_ACCESS_KEY", ""
            )
        if aws_session_token or os.getenv("AWS_SESSION_TOKEN"):
            session_kwargs["aws_session_token"] = aws_session_token or os.getenv(
                "AWS_SESSION_TOKEN", ""
            )

        session = boto3.Session(**session_kwargs)
        self._client = session.client("bedrock-runtime", region_name=resolved_region)
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """
        Generate a chat completion using the Bedrock converse API.

        Args:
            messages: OpenAI-style message list (roles: system / user / assistant / tool).
            tools: OpenAI-style tool definitions.
            tool_choice: 'auto' or 'required'.

        Returns:
            Dict with 'content' (str) and optionally 'tool_calls' (list).
        """
        import asyncio

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._sync_chat_completion,
            messages,
            tools,
            tool_choice,
            kwargs,
        )

    async def close(self) -> None:
        """Close the boto3 client."""
        self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        tool_choice: str,
        extra_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Synchronous implementation called from a thread executor."""
        system_prompt, bedrock_messages = self._convert_messages(messages)
        params = self._build_params(
            bedrock_messages, system_prompt, tools, tool_choice, extra_kwargs
        )

        logger.debug(
            "Bedrock request – model=%s, messages=%d, tools=%d",
            self._model,
            len(bedrock_messages),
            len(tools) if tools else 0,
        )

        response = self._client.converse(**params)

        usage = response.get("usage", {})
        if usage:
            logger.info(
                "Tokens: %d in, %d out, %d total",
                usage.get("inputTokens", 0),
                usage.get("outputTokens", 0),
                usage.get("totalTokens", 0),
            )

        return self._parse_response(response)

    def _convert_messages(
        self, messages: list[dict]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Split system messages and convert the rest to Bedrock converse format.

        Returns:
            (system_blocks, bedrock_messages)
        """
        system_blocks: list[dict[str, Any]] = []
        bedrock_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_blocks.append({"text": content})
            elif role == "tool":
                # Tool result – attach to the previous assistant turn if possible,
                # otherwise start a new user turn.
                tool_result_block: dict[str, Any] = {
                    "toolResult": {
                        "toolUseId": msg.get("tool_call_id", ""),
                        "content": [{"text": str(content)}],
                    }
                }
                if bedrock_messages and bedrock_messages[-1]["role"] == "user":
                    bedrock_messages[-1]["content"].append(tool_result_block)
                else:
                    bedrock_messages.append({"role": "user", "content": [tool_result_block]})
            elif role in ("user", "assistant"):
                # Handle both plain-string and list content
                if isinstance(content, str):
                    bedrock_content: list[dict[str, Any]] = [{"text": content}]
                elif isinstance(content, list):
                    bedrock_content = []
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text":
                                bedrock_content.append({"text": part.get("text", "")})
                            elif part.get("type") == "image_url":
                                image_block = self._convert_image_part(part)
                                if image_block:
                                    bedrock_content.append(image_block)
                            elif part.get("type") == "tool_use":
                                bedrock_content.append(
                                    {
                                        "toolUse": {
                                            "toolUseId": part.get("id", ""),
                                            "name": part.get("name", ""),
                                            "input": part.get("input", {}),
                                        }
                                    }
                                )
                        else:
                            bedrock_content.append({"text": str(part)})
                else:
                    bedrock_content = [{"text": str(content)}]

                # Merge consecutive messages with the same role (Bedrock requires alternating)
                if bedrock_messages and bedrock_messages[-1]["role"] == role:
                    bedrock_messages[-1]["content"].extend(bedrock_content)
                else:
                    bedrock_messages.append({"role": role, "content": bedrock_content})

        return system_blocks, bedrock_messages

    def _build_params(
        self,
        messages: list[dict[str, Any]],
        system_blocks: list[dict[str, Any]],
        tools: list[dict] | None,
        tool_choice: str,
        extra_kwargs: dict[str, Any],
    ) -> dict[str, Any]:
        """Build the params dict for bedrock-runtime converse."""
        params: dict[str, Any] = {
            "modelId": self._model,
            "messages": messages,
            "inferenceConfig": {
                "maxTokens": self._max_tokens,
                "temperature": self._temperature,
            },
        }

        if system_blocks:
            params["system"] = system_blocks

        if tools:
            tool_specs = [
                {
                    "toolSpec": {
                        "name": t["function"]["name"],
                        "description": t["function"].get("description", ""),
                        "inputSchema": {"json": t["function"]["parameters"]},
                    }
                }
                for t in tools
            ]
            params["toolConfig"] = {"tools": tool_specs}

            if tool_choice == "required":
                params["toolConfig"]["toolChoice"] = {"any": {}}
            else:
                params["toolConfig"]["toolChoice"] = {"auto": {}}

        filtered_kwargs = {
            key: value
            for key, value in extra_kwargs.items()
            if key in self._SUPPORTED_CONVERSE_KWARGS
        }
        ignored_kwargs = sorted(set(extra_kwargs) - self._SUPPORTED_CONVERSE_KWARGS)
        if ignored_kwargs:
            logger.debug("Ignoring unsupported Bedrock kwargs: %s", ", ".join(ignored_kwargs))

        params.update(filtered_kwargs)
        return params

    def _convert_image_part(self, part: dict[str, Any]) -> dict[str, Any] | None:
        """Convert OpenAI-style `image_url` part to Bedrock image block."""
        image_url_data = part.get("image_url", {})
        if isinstance(image_url_data, dict):
            url = image_url_data.get("url", "")
        else:
            url = str(image_url_data)

        if not isinstance(url, str) or not url:
            return None

        if not url.startswith("data:image/") or ";base64," not in url:
            # Bedrock image blocks don't accept remote URLs directly; keep context as text.
            return {"text": f"Image URL: {url}"}

        header, encoded = url.split(",", 1)
        mime_type = header.split(";")[0].removeprefix("data:")
        image_format = mime_type.split("/")[-1].lower()
        if image_format == "jpg":
            image_format = "jpeg"

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            logger.warning("Skipping malformed data URL image in Bedrock message conversion")
            return None

        return {
            "image": {
                "format": image_format,
                "source": {"bytes": image_bytes},
            }
        }

    def _parse_response(self, response: dict[str, Any]) -> dict[str, Any]:
        """Parse a Bedrock converse response into the standard Heimdall dict."""
        output = response.get("output", {})
        message = output.get("message", {})
        content_blocks: list[dict[str, Any]] = message.get("content", [])

        result: dict[str, Any] = {"content": ""}
        tool_calls: list[dict[str, Any]] = []

        for block in content_blocks:
            if "text" in block:
                result["content"] += block["text"]
            elif "toolUse" in block:
                tool_use = block["toolUse"]
                # Bedrock returns input as a dict; serialise to JSON string for consistency
                # with the OpenAI-style interface expected by Heimdall's agent.
                arguments = tool_use.get("input", {})
                tool_calls.append(
                    {
                        "id": tool_use.get("toolUseId", ""),
                        "type": "function",
                        "function": {
                            "name": tool_use.get("name", ""),
                            "arguments": json.dumps(arguments),
                        },
                    }
                )

        if tool_calls:
            result["tool_calls"] = tool_calls

        logger.debug(
            "Bedrock response – content=%r, tool_calls=%d",
            result["content"][:200],
            len(tool_calls),
        )
        return result
    _SUPPORTED_CONVERSE_KWARGS = {
        "additionalModelRequestFields",
        "additionalModelResponseFieldPaths",
        "guardrailConfig",
        "performanceConfig",
        "requestMetadata",
    }
