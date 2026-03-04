"""
Unit tests for BedrockLLM.

We mock boto3 so these tests run without real AWS credentials.
"""

import json
import sys
import types
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from heimdall.agent.llm.bedrock import BedrockLLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bedrock_response(text: str = "", tool_calls: list | None = None) -> dict:
    """Build a fake response dict as returned by boto3 bedrock-runtime converse."""
    content_blocks: list[dict] = []

    if text:
        content_blocks.append({"text": text})

    for tc in tool_calls or []:
        content_blocks.append(
            {
                "toolUse": {
                    "toolUseId": tc["id"],
                    "name": tc["name"],
                    "input": tc["input"],
                }
            }
        )

    return {
        "output": {"message": {"content": content_blocks}},
        "usage": {"inputTokens": 10, "outputTokens": 5, "totalTokens": 15},
        "stopReason": "end_turn",
    }


def _make_llm(model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0") -> "BedrockLLM":
    """Return a BedrockLLM instance with a mocked boto3 client."""
    mock_client = MagicMock()
    mock_session_cls = MagicMock()
    mock_session_cls.return_value.client.return_value = mock_client
    fake_boto3 = types.SimpleNamespace(Session=mock_session_cls)

    with patch.dict(sys.modules, {"boto3": fake_boto3}):
        from heimdall.agent.llm.bedrock import BedrockLLM

        llm = BedrockLLM(model=model)

    # Expose the mock client for assertions
    llm._client = mock_client  # type: ignore[attr-defined]
    return llm


# ---------------------------------------------------------------------------
# _convert_messages
# ---------------------------------------------------------------------------


class TestConvertMessages:
    def setup_method(self):
        self.llm = _make_llm()

    def test_system_message_becomes_system_block(self):
        system_blocks, bedrock_msgs = self.llm._convert_messages(
            [{"role": "system", "content": "You are helpful."}]
        )
        assert system_blocks == [{"text": "You are helpful."}]
        assert bedrock_msgs == []

    def test_user_message(self):
        _, msgs = self.llm._convert_messages([{"role": "user", "content": "Hello"}])
        assert msgs == [{"role": "user", "content": [{"text": "Hello"}]}]

    def test_assistant_message(self):
        _, msgs = self.llm._convert_messages([{"role": "assistant", "content": "I can help."}])
        assert msgs == [{"role": "assistant", "content": [{"text": "I can help."}]}]

    def test_consecutive_same_role_messages_are_merged(self):
        """Bedrock requires strictly alternating roles; consecutive same-role messages merge."""
        _, msgs = self.llm._convert_messages(
            [
                {"role": "user", "content": "First"},
                {"role": "user", "content": "Second"},
            ]
        )
        assert len(msgs) == 1
        assert len(msgs[0]["content"]) == 2

    def test_tool_result_appended_to_existing_user_turn(self):
        _, msgs = self.llm._convert_messages(
            [
                {"role": "user", "content": "Use a tool"},
                {
                    "role": "tool",
                    "tool_call_id": "call_abc",
                    "content": '{"result": 42}',
                },
            ]
        )
        # Should be one user turn with two content blocks
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert len(msgs[0]["content"]) == 2
        assert "toolResult" in msgs[0]["content"][1]

    def test_tool_result_starts_new_user_turn_if_no_existing(self):
        _, msgs = self.llm._convert_messages(
            [
                {
                    "role": "tool",
                    "tool_call_id": "call_xyz",
                    "content": "done",
                }
            ]
        )
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert "toolResult" in msgs[0]["content"][0]

    def test_image_url_data_part_converts_to_bedrock_image_block(self):
        _, msgs = self.llm._convert_messages(
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze screenshot"},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": (
                                    "data:image/png;base64,"
                                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO5YxY0AAAAASUVORK5CYII="
                                )
                            },
                        },
                    ],
                }
            ]
        )
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        image_block = msgs[0]["content"][1]
        assert image_block["image"]["format"] == "png"
        assert isinstance(image_block["image"]["source"]["bytes"], bytes)


# ---------------------------------------------------------------------------
# _build_params
# ---------------------------------------------------------------------------


class TestBuildParams:
    def setup_method(self):
        self.llm = _make_llm()

    def test_basic_params(self):
        params = self.llm._build_params(
            messages=[{"role": "user", "content": [{"text": "hi"}]}],
            system_blocks=[],
            tools=None,
            tool_choice="auto",
            extra_kwargs={},
        )
        assert params["modelId"] == self.llm._model
        assert "inferenceConfig" in params
        assert params["inferenceConfig"]["maxTokens"] == self.llm._max_tokens

    def test_system_block_included(self):
        params = self.llm._build_params(
            messages=[],
            system_blocks=[{"text": "Be concise"}],
            tools=None,
            tool_choice="auto",
            extra_kwargs={},
        )
        assert params["system"] == [{"text": "Be concise"}]

    def test_tools_included(self):
        tools = [
            {
                "function": {
                    "name": "click",
                    "description": "Click element",
                    "parameters": {"type": "object", "properties": {}},
                }
            }
        ]
        params = self.llm._build_params(
            messages=[],
            system_blocks=[],
            tools=tools,
            tool_choice="auto",
            extra_kwargs={},
        )
        assert "toolConfig" in params
        assert params["toolConfig"]["tools"][0]["toolSpec"]["name"] == "click"
        assert params["toolConfig"]["toolChoice"] == {"auto": {}}

    def test_tool_choice_required(self):
        tools = [
            {
                "function": {
                    "name": "click",
                    "description": "Click",
                    "parameters": {"type": "object", "properties": {}},
                }
            }
        ]
        params = self.llm._build_params(
            messages=[],
            system_blocks=[],
            tools=tools,
            tool_choice="required",
            extra_kwargs={},
        )
        assert params["toolConfig"]["toolChoice"] == {"any": {}}

    def test_no_system_key_when_empty(self):
        params = self.llm._build_params(
            messages=[],
            system_blocks=[],
            tools=None,
            tool_choice="auto",
            extra_kwargs={},
        )
        assert "system" not in params

    def test_unsupported_kwargs_are_filtered(self):
        params = self.llm._build_params(
            messages=[],
            system_blocks=[],
            tools=None,
            tool_choice="auto",
            extra_kwargs={
                "requestMetadata": {"source": "test"},
                "response_schema": {"type": "object"},
            },
        )
        assert params["requestMetadata"] == {"source": "test"}
        assert "response_schema" not in params


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------


class TestParseResponse:
    def setup_method(self):
        self.llm = _make_llm()

    def test_text_response(self):
        raw = _make_bedrock_response(text="Sure, I can help!")
        result = self.llm._parse_response(raw)
        assert result["content"] == "Sure, I can help!"
        assert "tool_calls" not in result

    def test_tool_call_response(self):
        raw = _make_bedrock_response(
            tool_calls=[{"id": "tc1", "name": "click", "input": {"selector": "#btn"}}]
        )
        result = self.llm._parse_response(raw)
        assert len(result["tool_calls"]) == 1
        tc = result["tool_calls"][0]
        assert tc["id"] == "tc1"
        assert tc["function"]["name"] == "click"
        # Arguments should be JSON-serialized
        args = json.loads(tc["function"]["arguments"])
        assert args == {"selector": "#btn"}

    def test_mixed_text_and_tool_call(self):
        raw = _make_bedrock_response(
            text="Clicking now.",
            tool_calls=[{"id": "tc2", "name": "navigate", "input": {"url": "/home"}}],
        )
        result = self.llm._parse_response(raw)
        assert result["content"] == "Clicking now."
        assert len(result["tool_calls"]) == 1


# ---------------------------------------------------------------------------
# chat_completion integration (sync path)
# ---------------------------------------------------------------------------


class TestChatCompletion:
    def setup_method(self):
        self.llm = _make_llm()

    @pytest.mark.asyncio
    async def test_chat_completion_calls_converse(self):
        self.llm._client.converse.return_value = _make_bedrock_response(text="Done")
        result = await self.llm.chat_completion(
            messages=[{"role": "user", "content": "Do something"}]
        )
        assert result["content"] == "Done"
        self.llm._client.converse.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_completion_with_tools(self):
        raw = _make_bedrock_response(
            tool_calls=[{"id": "tc3", "name": "scroll", "input": {"direction": "down"}}]
        )
        self.llm._client.converse.return_value = raw

        tools = [
            {
                "function": {
                    "name": "scroll",
                    "description": "Scroll the page",
                    "parameters": {"type": "object", "properties": {}},
                }
            }
        ]

        result = await self.llm.chat_completion(
            messages=[{"role": "user", "content": "Scroll down"}],
            tools=tools,
        )
        assert result["tool_calls"][0]["function"]["name"] == "scroll"

    @pytest.mark.asyncio
    async def test_close(self):
        await self.llm.close()
        self.llm._client.close.assert_called_once()


# ---------------------------------------------------------------------------
# Factory integration
# ---------------------------------------------------------------------------


class TestFactory:
    def test_create_llm_client_returns_bedrock_instance(self):
        mock_session_cls = MagicMock()
        mock_session_cls.return_value.client.return_value = MagicMock()
        fake_boto3 = types.SimpleNamespace(Session=mock_session_cls)

        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            from heimdall.agent.factory import create_llm_client
            from heimdall.agent.llm.bedrock import BedrockLLM

            llm = create_llm_client(provider="bedrock")  # type: ignore[arg-type]
            assert isinstance(llm, BedrockLLM)

    def test_create_llm_client_uses_custom_model(self):
        custom_model = "meta.llama3-70b-instruct-v1:0"
        mock_session_cls = MagicMock()
        mock_session_cls.return_value.client.return_value = MagicMock()
        fake_boto3 = types.SimpleNamespace(Session=mock_session_cls)

        with patch.dict(sys.modules, {"boto3": fake_boto3}):
            from heimdall.agent.factory import create_llm_client

            llm = create_llm_client(provider="bedrock", model=custom_model)  # type: ignore[arg-type]
            assert llm._model == custom_model

    def test_auto_provider_resolves_bedrock_with_aws_env(self, monkeypatch):
        from heimdall.agent import factory

        monkeypatch.setattr(factory, "_module_available", lambda module: module == "boto3")
        monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)

        assert factory._resolve_auto_provider() == "bedrock"

    def test_auto_provider_raises_when_no_provider_sdk_installed(self, monkeypatch):
        from heimdall.agent import factory

        monkeypatch.setattr(factory, "_module_available", lambda module: False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_PROFILE", raising=False)
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

        with pytest.raises(ImportError, match="No LLM provider SDK is installed"):
            factory._resolve_auto_provider()
