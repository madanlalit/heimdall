"""Unit tests for create_agent_output_schema in agent/schema.py."""

from heimdall.agent.schema import create_agent_output_schema


def _tool(name: str, properties: dict, required: list[str] | None = None) -> dict:
    """Build a minimal tool definition matching the registry schema output."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Description for {name}",
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


class TestCreateAgentOutputSchema:
    def test_returns_object_type(self):
        schema = create_agent_output_schema([])
        assert schema["type"] == "object"

    def test_has_all_expected_top_level_fields(self):
        schema = create_agent_output_schema([])
        props = schema["properties"]
        for field in (
            "thinking",
            "evaluation_previous_goal",
            "memory",
            "todo",
            "next_goal",
            "action",
        ):
            assert field in props, f"Missing field: {field}"

    def test_required_excludes_optional_fields(self):
        """'todo' is optional in the schema; all other main fields are required."""
        schema = create_agent_output_schema([])
        required = schema["required"]
        # todo is NOT in the required list (it's not included in the source schema)
        assert "todo" not in required
        for field in ("thinking", "evaluation_previous_goal", "memory", "next_goal", "action"):
            assert field in required

    def test_additional_properties_false(self):
        schema = create_agent_output_schema([])
        assert schema.get("additionalProperties") is False

    def test_action_field_is_array(self):
        schema = create_agent_output_schema([])
        action_schema = schema["properties"]["action"]
        assert action_schema["type"] == "array"

    def test_action_array_has_min_max_items(self):
        schema = create_agent_output_schema([])
        action_schema = schema["properties"]["action"]
        assert action_schema.get("minItems") == 1
        assert action_schema.get("maxItems") == 3

    def test_no_tools_produces_fallback_object_items(self):
        schema = create_agent_output_schema([])
        items = schema["properties"]["action"]["items"]
        assert items == {"anyOf": [{"type": "object"}]}

    def test_single_tool_appears_in_anyof(self):
        tools = [_tool("click", {"index": {"type": "integer"}}, required=["index"])]
        schema = create_agent_output_schema(tools)
        any_of = schema["properties"]["action"]["items"]["anyOf"]
        assert len(any_of) == 1
        # The entry should require the action name as a key
        assert "click" in any_of[0]["properties"]
        assert any_of[0]["required"] == ["click"]

    def test_multiple_tools_each_appear_in_anyof(self):
        tools = [
            _tool("click", {"index": {"type": "integer"}}, required=["index"]),
            _tool("navigate", {"url": {"type": "string"}}, required=["url"]),
            _tool("type_text", {"text": {"type": "string"}}, required=["text"]),
        ]
        schema = create_agent_output_schema(tools)
        any_of = schema["properties"]["action"]["items"]["anyOf"]
        assert len(any_of) == 3
        names = {list(entry["properties"].keys())[0] for entry in any_of}
        assert names == {"click", "navigate", "type_text"}

    def test_each_tool_entry_has_additional_properties_false(self):
        tools = [_tool("scroll", {"direction": {"type": "string"}})]
        schema = create_agent_output_schema(tools)
        any_of = schema["properties"]["action"]["items"]["anyOf"]
        for entry in any_of:
            assert entry.get("additionalProperties") is False
            # The nested action object also forbids extra props
            action_obj = list(entry["properties"].values())[0]
            assert action_obj.get("additionalProperties") is False

    def test_tool_parameters_preserved(self):
        props = {"index": {"type": "integer"}, "force": {"type": "boolean"}}
        tools = [_tool("click", props, required=["index"])]
        schema = create_agent_output_schema(tools)
        any_of = schema["properties"]["action"]["items"]["anyOf"]
        click_obj = any_of[0]["properties"]["click"]
        assert click_obj["properties"]["index"] == {"type": "integer"}
        assert click_obj["properties"]["force"] == {"type": "boolean"}
        assert "index" in click_obj["required"]
