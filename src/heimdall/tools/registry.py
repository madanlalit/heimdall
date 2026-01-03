"""
Tools Registry - Action registration and execution for Heimdall.

Provides decorator-based action registration with automatic
Pydantic model generation for LLM tool calling.
"""

import asyncio
import inspect
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeAlias, cast, get_type_hints

from pydantic import BaseModel, Field, create_model

from heimdall.exceptions import ActionError

# Type alias for functions (callables with __name__)
ActionFunc: TypeAlias = Callable[..., Any]

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
        self._session: "BrowserSession | None" = None
        self._dom_state: "SerializedDOM | None" = None
        self._allowed_domains: list[str] = []

    def set_context(
        self,
        session: "BrowserSession",
        dom_state: "SerializedDOM | None" = None,
        allowed_domains: list[str] | None = None,
    ) -> None:
        """Set execution context for actions."""
        self._session = session
        self._dom_state = dom_state
        if allowed_domains is not None:
            self._allowed_domains = allowed_domains

    def action(self, description: str) -> Callable[[ActionFunc], ActionFunc]:
        """
        Decorator to register an action.

        Usage:
            @registry.action("Click element by index")
            async def click(index: int) -> ActionResult:
                ...
        """

        def decorator(func: ActionFunc) -> ActionFunc:
            # Generate Pydantic model from function signature
            param_model = self._create_param_model(func)

            # Get function name (functions always have __name__)
            func_name = cast(str, getattr(func, "__name__", "unknown"))

            action_obj = Action(
                name=func_name,
                description=description,
                func=func,
                param_model=param_model,
            )
            self._actions[func_name] = action_obj

            logger.debug(f"Registered action: {func_name}")
            return func

        return decorator

    def _create_param_model(self, func: ActionFunc) -> type[BaseModel]:
        """Create Pydantic model from function parameters."""
        sig = inspect.signature(func)

        # Try to get type hints, but handle forward refs that can't be resolved
        try:
            hints = get_type_hints(func, include_extras=False)
        except NameError:
            # Fall back to raw annotations if type hints can't be resolved
            hints = getattr(func, "__annotations__", {})

        fields: dict[str, Any] = {}
        for name, param in sig.parameters.items():
            if name in ("self", "session", "dom_state"):
                continue

            # Get annotation, defaulting to Any if not available or unresolvable
            annotation = hints.get(name)
            if annotation is None or isinstance(annotation, str):
                annotation = Any

            default = ... if param.default is inspect.Parameter.empty else param.default
            fields[name] = (annotation, default)

        func_name = cast(str, getattr(func, "__name__", "unknown"))
        # create_model has complex overloads, dynamic kwargs typing
        return create_model(f"{func_name}Params", **fields)

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
            if not self._session:
                return ActionResult.fail("Browser session not initialized in context")
            kwargs["session"] = self._session
        if "dom_state" in sig.parameters:
            kwargs["dom_state"] = self._dom_state
        if "allowed_domains" in sig.parameters:
            kwargs["allowed_domains"] = self._allowed_domains

        # Execute action
        try:
            if asyncio.iscoroutinefunction(action.func):
                result = await action.func(**kwargs)
            else:
                result = action.func(**kwargs)

            if not isinstance(result, ActionResult):
                result = ActionResult.ok(str(result) if result else "")

            return result

        except ActionError as e:
            # Expected action failure
            return ActionResult.fail(str(e))
        except Exception as e:
            # Unexpected system error during action
            logger.error(f"Action {name} failed: {e}", exc_info=True)
            return ActionResult.fail(f"System error: {e}")

    def schema(self) -> list[dict[str, Any]]:
        """
        Generate LLM tool calling schema.

        Returns:
            List of tool definitions for LLM
        """
        tools: list[dict[str, Any]] = []

        for name, action in self._actions.items():
            parameters: dict[str, Any] = {
                "type": "object",
                "properties": {},
                "required": [],
            }

            if action.param_model:
                model_schema = action.param_model.model_json_schema()
                parameters["properties"] = model_schema.get("properties", {})
                parameters["required"] = model_schema.get("required", [])

            tool: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": action.description,
                    "parameters": parameters,
                },
            }

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
