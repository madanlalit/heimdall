"""Unit tests for ToolRegistry: action decorator, execute(), schema(), and ActionResult."""

import pytest

from heimdall.exceptions import ActionError
from heimdall.tools.registry import ActionResult, ToolRegistry


# ── ActionResult ──────────────────────────────────────────────────────────────


class TestActionResult:
    def test_ok_creates_success(self):
        r = ActionResult.ok("all good")
        assert r.success is True
        assert r.message == "all good"
        assert r.error is None

    def test_ok_carries_extra_data(self):
        r = ActionResult.ok("done", url="https://example.com", status=200)
        assert r.data["url"] == "https://example.com"
        assert r.data["status"] == 200

    def test_fail_creates_failure(self):
        r = ActionResult.fail("something went wrong")
        assert r.success is False
        assert r.error == "something went wrong"

    def test_success_field_defaults_true(self):
        r = ActionResult()
        assert r.success is True


# ── ToolRegistry.action decorator ─────────────────────────────────────────────


class TestActionDecorator:
    def setup_method(self):
        self.reg = ToolRegistry()

    def test_action_registered_by_name(self):
        @self.reg.action("Do something")
        def do_something(x: int) -> ActionResult:
            return ActionResult.ok()

        assert "do_something" in self.reg.actions

    def test_action_description_stored(self):
        @self.reg.action("My description")
        def my_action(x: int) -> ActionResult:
            return ActionResult.ok()

        assert self.reg.actions["my_action"].description == "My description"

    def test_param_model_created_with_correct_fields(self):
        @self.reg.action("Click element")
        def click(index: int, force: bool = False) -> ActionResult:
            return ActionResult.ok()

        model = self.reg.actions["click"].param_model
        assert model is not None
        schema = model.model_json_schema()
        assert "index" in schema["properties"]
        assert "force" in schema["properties"]

    def test_decorator_returns_original_function(self):
        def my_func(x: int) -> ActionResult:
            return ActionResult.ok()

        result = self.reg.action("desc")(my_func)
        assert result is my_func

    def test_multiple_actions_registered_independently(self):
        @self.reg.action("First")
        def first_action() -> ActionResult:  # type: ignore[return]
            pass

        @self.reg.action("Second")
        def second_action() -> ActionResult:  # type: ignore[return]
            pass

        assert "first_action" in self.reg.actions
        assert "second_action" in self.reg.actions


# ── ToolRegistry.execute ──────────────────────────────────────────────────────


class TestExecute:
    def setup_method(self):
        self.reg = ToolRegistry()

    @pytest.mark.asyncio
    async def test_unknown_action_returns_failure(self):
        result = await self.reg.execute("nonexistent", {})
        assert result.success is False
        assert "Unknown action" in result.error

    @pytest.mark.asyncio
    async def test_sync_action_executes_and_returns_result(self):
        @self.reg.action("Add numbers")
        def add(a: int, b: int) -> ActionResult:
            return ActionResult.ok(f"{a + b}")

        result = await self.reg.execute("add", {"a": 3, "b": 4})
        assert result.success is True
        assert result.message == "7"

    @pytest.mark.asyncio
    async def test_async_action_executes_and_returns_result(self):
        @self.reg.action("Async greet")
        async def greet(name: str) -> ActionResult:
            return ActionResult.ok(f"Hello, {name}")

        result = await self.reg.execute("greet", {"name": "World"})
        assert result.success is True
        assert result.message == "Hello, World"

    @pytest.mark.asyncio
    async def test_invalid_params_returns_failure(self):
        @self.reg.action("Requires int")
        def requires_int(x: int) -> ActionResult:
            return ActionResult.ok()

        result = await self.reg.execute("requires_int", {"x": "not-an-int"})
        # Pydantic coerces strings to int when possible; test with truly invalid type
        # Use a dict as the value to ensure a validation error
        result2 = await self.reg.execute("requires_int", {"x": {"nested": True}})
        assert result2.success is False
        assert "Invalid parameters" in result2.error

    @pytest.mark.asyncio
    async def test_action_error_returns_failure(self):
        @self.reg.action("Raises action error")
        def raises_action_error() -> ActionResult:
            raise ActionError("intentional action failure")

        result = await self.reg.execute("raises_action_error", {})
        assert result.success is False
        assert "intentional action failure" in result.error

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_system_error(self):
        @self.reg.action("Crashes")
        def crashes() -> ActionResult:
            raise RuntimeError("boom")

        result = await self.reg.execute("crashes", {})
        assert result.success is False
        assert "System error" in result.error

    @pytest.mark.asyncio
    async def test_non_actionresult_return_wrapped_as_ok(self):
        @self.reg.action("Returns string")
        def returns_string() -> str:  # type: ignore[return]
            return "raw string"

        result = await self.reg.execute("returns_string", {})
        assert result.success is True
        assert result.message == "raw string"

    @pytest.mark.asyncio
    async def test_none_return_wrapped_as_ok_empty_message(self):
        @self.reg.action("Returns None")
        def returns_none():  # type: ignore[return]
            return None

        result = await self.reg.execute("returns_none", {})
        assert result.success is True
        assert result.message == ""

    @pytest.mark.asyncio
    async def test_session_injected_when_requested(self):
        """Actions that declare 'session' param should receive it from context."""
        captured = {}

        @self.reg.action("Needs session")
        def needs_session(session) -> ActionResult:
            captured["session"] = session
            return ActionResult.ok()

        mock_session = object()
        self.reg.set_context(session=mock_session)  # type: ignore[arg-type]
        await self.reg.execute("needs_session", {})
        assert captured["session"] is mock_session

    @pytest.mark.asyncio
    async def test_session_missing_returns_failure(self):
        """If a session is required but not set, execution should fail gracefully."""

        @self.reg.action("Requires session")
        def act_with_session(session) -> ActionResult:
            return ActionResult.ok()

        # Don't call set_context — session remains None
        result = await self.reg.execute("act_with_session", {})
        assert result.success is False
        assert "session" in result.error.lower()


# ── ToolRegistry.schema ───────────────────────────────────────────────────────


class TestSchema:
    def setup_method(self):
        self.reg = ToolRegistry()

    def test_empty_registry_returns_empty_list(self):
        assert self.reg.schema() == []

    def test_schema_has_one_entry_per_action(self):
        @self.reg.action("First")
        def act_a() -> ActionResult:  # type: ignore[return]
            pass

        @self.reg.action("Second")
        def act_b() -> ActionResult:  # type: ignore[return]
            pass

        assert len(self.reg.schema()) == 2

    def test_schema_entry_structure(self):
        @self.reg.action("Say hello")
        def say_hello(name: str) -> ActionResult:
            return ActionResult.ok()

        schema = self.reg.schema()
        entry = schema[0]
        assert entry["type"] == "function"
        assert entry["function"]["name"] == "say_hello"
        assert entry["function"]["description"] == "Say hello"
        assert "parameters" in entry["function"]

    def test_schema_required_fields(self):
        @self.reg.action("Move")
        def move(x: int, y: int, speed: float = 1.0) -> ActionResult:
            return ActionResult.ok()

        schema = self.reg.schema()
        params = schema[0]["function"]["parameters"]
        assert "x" in params["properties"]
        assert "y" in params["properties"]
        assert "speed" in params["properties"]
        # x and y are required (no default), speed is optional
        assert "x" in params["required"]
        assert "y" in params["required"]
        assert "speed" not in params["required"]
