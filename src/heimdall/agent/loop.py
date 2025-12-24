"""
Agent Loop - Main orchestration loop for Heimdall agent.

Manages the think → act → observe cycle for browser automation.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.dom.service import DomService
    from heimdall.events.bus import EventBus
    from heimdall.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgentConfig(BaseModel):
    """Agent configuration."""

    max_steps: int = 50
    max_retries: int = 3
    max_consecutive_failures: int = 5
    step_timeout: float = 60.0


class AgentState(BaseModel):
    """Agent execution state."""

    step_count: int = 0
    done: bool = False
    success: bool = False
    error: str | None = None

    # History
    actions_taken: list[dict] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)

    # Failure tracking
    consecutive_failures: int = 0
    total_failures: int = 0


class Agent:
    """
    LLM-driven browser automation agent.

    Orchestrates the loop:
    1. Get DOM state
    2. Build LLM prompt with context
    3. Get LLM response with tool calls
    4. Execute tools
    5. Repeat until done or max steps
    """

    def __init__(
        self,
        session: "BrowserSession",
        dom_service: "DomService",
        registry: "ToolRegistry",
        llm_client: Any,
        event_bus: "EventBus | None" = None,
        config: AgentConfig | None = None,
    ):
        self._session = session
        self._dom_service = dom_service
        self._registry = registry
        self._llm = llm_client
        self._bus = event_bus
        self._config = config or AgentConfig()
        self._state = AgentState()
        self._message_builder = MessageBuilder()

    async def run(self, task: str) -> AgentState:
        """
        Run agent to complete a task.

        Args:
            task: Natural language task description

        Returns:
            Final agent state
        """
        logger.info(f"Starting task: {task[:80]}")
        self._state = AgentState()

        try:
            while not self._should_stop():
                await self._execute_step(task)

            self._state.success = self._state.done and not self._state.error

        except Exception as e:
            logger.error(f"Agent error: {e}")
            self._state.error = str(e)
            self._state.success = False

        logger.info(f"Task complete: success={self._state.success}")
        return self._state

    async def _execute_step(self, task: str) -> None:
        """Execute one agent step."""
        self._state.step_count += 1

        logger.debug(f"Step {self._state.step_count}")

        # 1. Get DOM state
        dom_state = await self._dom_service.get_state()
        self._registry.set_context(self._session, dom_state)

        # 2. Build messages
        messages = self._message_builder.build(
            task=task,
            dom_state=dom_state,
            history=self._state.actions_taken[-5:],  # Last 5 actions
        )

        # 3. Get LLM response
        try:
            response = await self._call_llm(messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            self._state.consecutive_failures += 1
            return

        # 4. Process tool calls
        tool_calls = response.get("tool_calls", [])

        if not tool_calls:
            # LLM gave text response without action
            content = response.get("content", "")
            logger.info(f"LLM response (no tool call): {content[:200]}")

            # Track consecutive no-action responses to prevent infinite loop
            self._state.consecutive_failures += 1

            # If LLM says task is done in its response, mark as done
            if any(
                word in content.lower() for word in ["complete", "done", "finished", "successful"]
            ):
                logger.info("Task appears complete from LLM response")
                self._state.done = True
            return

        # 5. Execute tools
        for tool_call in tool_calls:
            name = tool_call.get("function", {}).get("name", "")
            args = tool_call.get("function", {}).get("arguments", {})

            if isinstance(args, str):
                import json

                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            result = await self._registry.execute(name, args)

            # Record action
            self._state.actions_taken.append(
                {
                    "step": self._state.step_count,
                    "action": name,
                    "params": args,
                    "success": result.success,
                    "message": result.message,
                    "error": result.error,
                }
            )

            if result.success:
                self._state.consecutive_failures = 0

                # Check for done action
                if name == "done" or result.data.get("done"):
                    self._state.done = True
                    return
            else:
                self._state.consecutive_failures += 1
                self._state.total_failures += 1
                logger.warning(f"Action failed: {result.error}")

        # Small delay between steps
        await asyncio.sleep(0.2)

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Call LLM with messages and tools."""
        tools = self._registry.schema()
        logger.debug(
            f"Registry has {len(self._registry.actions)} actions, schema has {len(tools)} tools"
        )

        # This is a placeholder - actual implementation depends on LLM client
        response = await self._llm.chat_completion(
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )

        return response

    def _should_stop(self) -> bool:
        """Check if agent should stop."""
        if self._state.done:
            return True

        if self._state.step_count >= self._config.max_steps:
            logger.warning(f"Max steps reached: {self._config.max_steps}")
            return True

        if self._state.consecutive_failures >= self._config.max_consecutive_failures:
            logger.error("Too many consecutive failures")
            self._state.error = "Too many consecutive failures"
            return True

        return False


class MessageBuilder:
    """Builds LLM messages with context."""

    def build(
        self,
        task: str,
        dom_state: Any,
        history: list[dict] | None = None,
    ) -> list[dict]:
        """Build messages for LLM."""
        messages = []

        # System message
        messages.append(
            {
                "role": "system",
                "content": self._system_prompt(),
            }
        )

        # User message with task and DOM
        user_content = f"""Task: {task}

Current page elements:
{dom_state.text if hasattr(dom_state, "text") else str(dom_state)}

Available elements: {dom_state.element_count if hasattr(dom_state, "element_count") else "unknown"}
"""

        # Add history
        if history:
            user_content += "\n\nRecent actions:\n"
            for action in history:
                status = "✓" if action.get("success") else "✗"
                user_content += f"- {status} {action.get('action')}({action.get('params', {})})\n"

        messages.append(
            {
                "role": "user",
                "content": user_content,
            }
        )

        return messages

    def _system_prompt(self) -> str:
        return """You are a browser automation agent. \
Your task is to interact with web pages to accomplish user goals.

Guidelines:
1. Analyze the page elements shown with [index] numbers
2. Use the available tools to interact with elements
3. Click buttons, fill forms, navigate as needed
4. Call 'done' when the task is complete
5. Be efficient - use the minimum actions needed

Always respond with tool calls to take actions. Do not just describe what to do."""
