"""
Tools Registry - Action registration and execution for Heimdall.

Provides decorator-based action registration with automatic
Pydantic model generation for LLM tool calling.
"""

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_type_hints

from pydantic import BaseModel, Field, create_model

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.dom.service import SerializedDOM

logger = logging.getLogger(__name__)


class ActionResult(BaseModel):
    """Result of an action execution."""

    success: bool = True
    message: str = ""
    error: str | None = None
    data: dict = Field(default_factory=dict)

    @classmethod
    def ok(cls, message: str = "", **data) -> "ActionResult":
        return cls(success=True, message=message, data=data)

    @classmethod
    def fail(cls, error: str) -> "ActionResult":
        return cls(success=False, error=error)


class Action(BaseModel):
    """Registered action metadata."""

    name: str
    description: str
    func: Callable = Field(exclude=True)
    param_model: type[BaseModel] | None = None

    model_config = {"arbitrary_types_allowed": True}


class ToolRegistry:
    """
    Registry for browser actions.

    Actions are registered with @action decorator and can be
    executed by name with parameter validation.
    """

    def __init__(self):
        self._actions: dict[str, Action] = {}
        self._session: BrowserSession | None = None
        self._dom_state: SerializedDOM | None = None

    def set_context(
        self,
        session: "BrowserSession",
        dom_state: "SerializedDOM | None" = None,
    ) -> None:
        """Set execution context for actions."""
        self._session = session
        self._dom_state = dom_state

    def action(self, description: str) -> Callable:
        """
        Decorator to register an action.

        Usage:
            @registry.action("Click element by index")
            async def click(index: int) -> ActionResult:
                ...
        """

        def decorator(func: Callable) -> Callable:
            # Generate Pydantic model from function signature
            param_model = self._create_param_model(func)

            action = Action(
                name=func.__name__,
                description=description,
                func=func,
                param_model=param_model,
            )
            self._actions[func.__name__] = action

            logger.debug(f"Registered action: {func.__name__}")
            return func

        return decorator

    def _create_param_model(self, func: Callable) -> type[BaseModel]:
        """Create Pydantic model from function parameters."""
        sig = inspect.signature(func)

        # Try to get type hints, but handle forward refs that can't be resolved
        try:
            hints = get_type_hints(func, include_extras=False)
        except NameError:
            # Fall back to raw annotations if type hints can't be resolved
            hints = func.__annotations__ if hasattr(func, "__annotations__") else {}

        fields = {}
        for name, param in sig.parameters.items():
            if name in ("self", "session", "dom_state"):
                continue

            # Get annotation, defaulting to Any if not available or unresolvable
            annotation = hints.get(name)
            if annotation is None or isinstance(annotation, str):
                annotation = Any

            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[name] = (annotation, default)

        return create_model(f"{func.__name__}Params", **fields)

    async def execute(self, name: str, params: dict) -> ActionResult:
        """
        Execute action by name with parameters.

        Args:
            name: Action name
            params: Action parameters

        Returns:
            ActionResult from action execution
        """
        if name not in self._actions:
            return ActionResult.fail(f"Unknown action: {name}")

        action = self._actions[name]

        # Validate parameters
        try:
            if action.param_model:
                validated = action.param_model(**params)
                params = validated.model_dump()
        except Exception as e:
            return ActionResult.fail(f"Invalid parameters: {e}")

        # Inject context if needed
        sig = inspect.signature(action.func)
        kwargs = dict(params)

        if "session" in sig.parameters:
            kwargs["session"] = self._session
        if "dom_state" in sig.parameters:
            kwargs["dom_state"] = self._dom_state

        # Execute action
        try:
            if asyncio.iscoroutinefunction(action.func):
                result = await action.func(**kwargs)
            else:
                result = action.func(**kwargs)

            if not isinstance(result, ActionResult):
                result = ActionResult.ok(str(result) if result else "")

            return result

        except Exception as e:
            logger.error(f"Action {name} failed: {e}")
            return ActionResult.fail(str(e))

    def schema(self) -> list[dict]:
        """
        Generate LLM tool calling schema.

        Returns:
            List of tool definitions for LLM
        """
        tools = []

        for name, action in self._actions.items():
            tool = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": action.description,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            }

            if action.param_model:
                schema = action.param_model.model_json_schema()
                tool["function"]["parameters"]["properties"] = schema.get("properties", {})
                tool["function"]["parameters"]["required"] = schema.get("required", [])

            tools.append(tool)

        return tools

    @property
    def actions(self) -> dict[str, Action]:
        """Get all registered actions."""
        return dict(self._actions)


# Global registry instance
registry = ToolRegistry()


def action(description: str) -> Callable:
    """
    Decorator to register an action with the global registry.

    Usage:
        @action("Click element by index")
        async def click(index: int) -> ActionResult:
            ...
    """
    return registry.action(description)
