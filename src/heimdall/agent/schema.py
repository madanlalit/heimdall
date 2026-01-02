"""
Schema utilities for generating optimized JSON schemas for LLM structured output.
"""

from typing import Any


def create_agent_output_schema(tool_definitions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Create JSON schema for AgentOutput that includes action definitions.

    This generates a schema that enforces:
    - thinking, evaluation_previous_goal, memory, todo, next_goal fields
    - action array with valid actions from tool definitions
    """
    # Build action schemas from tool definitions
    action_schemas = []
    for tool in tool_definitions:
        func = tool.get("function", {})
        name = func.get("name", "")
        params = func.get("parameters", {})

        # Create schema for this action: {"action_name": {params}}
        action_schemas.append(
            {
                "type": "object",
                "properties": {
                    name: {
                        "type": "object",
                        "properties": params.get("properties", {}),
                        "required": params.get("required", []),
                        "additionalProperties": False,
                    }
                },
                "required": [name],
                "additionalProperties": False,
            }
        )

    return {
        "type": "object",
        "properties": {
            "thinking": {
                "type": "string",
                "description": "Extended reasoning about current state",
            },
            "evaluation_previous_goal": {
                "type": "string",
                "description": "Did the last action succeed, fail, or uncertain? Include verdict.",
            },
            "memory": {
                "type": "string",
                "description": "Working memory to track progress",
            },
            "todo": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of remaining tasks",
            },
            "next_goal": {
                "type": "string",
                "description": "Clear statement of next objective",
            },
            "action": {
                "type": "array",
                "items": {"anyOf": action_schemas if action_schemas else [{"type": "object"}]},
                "description": "Actions to execute",
                "minItems": 1,
                "maxItems": 3,
            },
        },
        "required": ["thinking", "evaluation_previous_goal", "memory", "next_goal", "action"],
        "additionalProperties": False,
    }
