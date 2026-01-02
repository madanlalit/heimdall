"""
Agent Loop - Main orchestration loop for Heimdall agent.

Manages the think → act → observe cycle for browser automation.
Uses structured output for stateful reasoning across steps.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from heimdall.agent.views import (
    ActionResult,
    AgentHistory,
    AgentHistoryList,
    AgentOutput,
    BrowserStateSnapshot,
)
from heimdall.events.bus import EventBus
from heimdall.watchdogs import (
    DOMWatchdog,
    ErrorWatchdog,
    NavigationWatchdog,
    NetworkWatchdog,
)

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

    # Vision: send screenshots to LLM (user-toggleable)
    use_vision: bool = False

    # Stateful reasoning options
    use_thinking: bool = True  # Enable thinking field in output
    flash_mode: bool = False  # Disable evaluation/next_goal for speed
    max_actions_per_step: int = 3  # Max actions per LLM response

    # Wait for page stability after actions
    wait_for_stability: bool = True
    stability_timeout: float = 5.0

    # Domain restriction: only allow navigation to these domains
    # If empty, all domains are allowed
    # Examples: ["chatgpt.com", "*.openai.com", "google.com"]
    allowed_domains: list[str] = Field(default_factory=list)

    # Custom instructions to extend the system prompt
    # This content is appended to the base system prompt
    extend_system_prompt: str | None = None


class AgentState(BaseModel):
    """Agent execution state."""

    step_count: int = 0
    done: bool = False
    success: bool = False
    error: str | None = None

    # Failure tracking
    consecutive_failures: int = 0
    total_failures: int = 0


class Agent:
    """
    LLM-driven browser automation agent.

    Orchestrates the loop:
    1. Get DOM state
    2. Build LLM prompt with structured history
    3. Get LLM response as structured JSON (thinking, memory, actions)
    4. Execute actions
    5. Record results in history
    6. Repeat until done or max steps
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
        self._bus = event_bus or EventBus()
        self._config = config or AgentConfig()
        self._state = AgentState()
        self._history = AgentHistoryList()
        self._message_builder = MessageBuilder(
            extend_system_prompt=self._config.extend_system_prompt
        )

        # Initialize watchdogs
        self._watchdogs = {
            "navigation": NavigationWatchdog(self._session, self._bus),
            "network": NetworkWatchdog(self._session, self._bus),
            "dom": DOMWatchdog(self._session, self._bus),
            "error": ErrorWatchdog(self._session, self._bus),
        }

        # File system for persisting agent data (todo.md, etc.)
        from heimdall.agent.filesystem import FileSystem

        self._filesystem = FileSystem()

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
        self._history = AgentHistoryList()

        # Initialize todo with task
        self._filesystem.update_todo([f"Complete: {task[:100]}"])

        # Start watchdogs
        for w in self._watchdogs.values():
            await w.start()

        try:
            while not self._should_stop():
                await self._execute_step(task)

            self._state.success = self._state.done and not self._state.error

        except Exception as e:
            logger.error(f"Agent error: {e}")
            self._state.error = str(e)
            self._state.success = False

        finally:
            # Stop watchdogs
            for w in self._watchdogs.values():
                await w.stop()

        logger.info(f"Task complete: success={self._state.success}")
        return self._state

    async def _execute_step(self, task: str) -> None:
        """Execute one agent step with structured output."""
        self._state.step_count += 1
        step_number = self._state.step_count

        logger.debug(f"Step {step_number}")

        # 1. Get DOM state
        dom_state = await self._dom_service.get_state()
        self._registry.set_context(
            self._session,
            dom_state,
            allowed_domains=self._config.allowed_domains,
        )

        # 2. Optional: capture screenshot for vision
        screenshot_b64 = None
        if self._config.use_vision:
            try:
                import base64

                screenshot_data = await self._session.screenshot()
                screenshot_b64 = base64.b64encode(screenshot_data).decode()
            except Exception as e:
                logger.debug(f"Screenshot for vision failed: {e}")

        # 3. Build messages with structured history
        messages = self._message_builder.build(
            task=task,
            dom_state=dom_state,
            history=self._history,
            step_info=(step_number, self._config.max_steps),
            screenshot_b64=screenshot_b64,
        )

        # 4. Get LLM response
        try:
            response = await self._call_llm(messages)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            self._state.consecutive_failures += 1
            return

        # 5. Parse structured output
        agent_output = self._parse_agent_output(response)

        if not agent_output or not agent_output.action:
            logger.warning("No valid actions in response")
            self._state.consecutive_failures += 1
            return

        # Log the agent's reasoning
        if agent_output.evaluation_previous_goal:
            logger.info(f"Evaluation: {agent_output.evaluation_previous_goal}")
        if agent_output.next_goal:
            logger.info(f"Next Goal: {agent_output.next_goal}")

        # 6. Execute actions and collect results
        results: list[ActionResult] = []
        page_changed = False

        for action_dict in agent_output.action[: self._config.max_actions_per_step]:
            if page_changed:
                logger.debug("Page changed, skipping remaining actions")
                break

            # Extract action name and args
            if not action_dict:
                continue

            action_name = list(action_dict.keys())[0]
            action_args = action_dict[action_name] or {}

            logger.info(f"Tool call: {action_name}({json.dumps(action_args)[:50]}...)")

            # Execute action
            exec_result = await self._registry.execute(action_name, action_args)

            # Convert to ActionResult
            result = ActionResult(
                is_done=action_name == "done",
                success=exec_result.success,
                error=exec_result.error,
                extracted_content=exec_result.message,
            )
            results.append(result)

            if exec_result.success:
                self._state.consecutive_failures = 0

                # Check for done action
                if action_name == "done":
                    self._state.done = True
                    self._state.success = action_args.get("success", True)
                    break

                # Wait for page stability after state-changing actions
                if self._config.wait_for_stability and action_name in (
                    "click",
                    "navigate",
                    "type_text",
                    "press_key",
                ):
                    try:
                        # Use watchdog for smarter waiting (wait for load + stability)
                        nav_watchdog: NavigationWatchdog = self._watchdogs["navigation"]  # type: ignore
                        await nav_watchdog.wait_for_load(timeout=self._config.stability_timeout)

                        # Check if URL changed (indicates page navigation)
                        new_dom = await self._dom_service.get_state()
                        if (
                            hasattr(new_dom, "url")
                            and hasattr(dom_state, "url")
                            and new_dom.url != dom_state.url
                        ):
                            page_changed = True
                    except Exception as e:
                        logger.debug(f"Smart wait failed: {e}")
            else:
                self._state.consecutive_failures += 1
                self._state.total_failures += 1
                logger.warning(f"Action failed: {exec_result.error}")

        # 7. Record step in history
        history_item = AgentHistory(
            step_number=step_number,
            model_output=agent_output,
            results=results,
            state=BrowserStateSnapshot(
                url=getattr(dom_state, "url", None),
                title=getattr(dom_state, "title", None),
                element_count=getattr(dom_state, "element_count", 0),
            ),
        )
        self._history.add(history_item)

        # 8. Update todo.md if agent provided a todo list
        if agent_output.todo:
            self._filesystem.update_todo(agent_output.todo)

        # Small delay between steps
        await asyncio.sleep(0.2)

    def _parse_agent_output(self, response: dict) -> AgentOutput | None:
        """Parse LLM response into structured AgentOutput."""
        content = response.get("content", "")

        if not content:
            # Try to extract from tool calls (fallback for tool-calling models)
            tool_calls = response.get("tool_calls", [])
            if tool_calls:
                actions = []
                for tc in tool_calls:
                    name = tc.get("function", {}).get("name", "")
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    if name:
                        actions.append({name: args})

                return AgentOutput(action=actions) if actions else None
            return None

        # Try to parse JSON from content
        try:
            # Handle markdown code blocks
            if "```json" in content:
                start = content.find("```json") + 7
                end = content.find("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.find("```") + 3
                end = content.find("```", start)
                content = content[start:end].strip()

            # Handle case where LLM outputs "json" or "json\n{" at the start
            content = content.strip()
            if content.startswith("json"):
                content = content[4:].strip()

            data = json.loads(content)

            # Parse actions
            actions = data.get("action", [])
            if isinstance(actions, dict):
                actions = [actions]

            # Normalize actions to ensure correct format
            normalized_actions = self._normalize_actions(actions)

            return AgentOutput(
                thinking=data.get("thinking"),
                evaluation_previous_goal=data.get("evaluation_previous_goal"),
                memory=data.get("memory"),
                todo=data.get("todo"),
                next_goal=data.get("next_goal"),
                action=normalized_actions,
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to parse agent output: {e}")
            logger.debug(f"Raw content: {content[:500]}")
            return None

    def _normalize_actions(self, actions: list) -> list[dict]:
        """Normalize action format to ensure correct structure."""
        normalized = []
        for action in actions:
            if not action:
                continue

            # Already correct format: {"action_name": {"param": "value"}}
            if isinstance(action, dict):
                # Check if it's a nested format like {"action_name": {"action_name": {...}}}
                keys = list(action.keys())
                if len(keys) == 1:
                    action_name = keys[0]
                    action_params = action[action_name]

                    # If params is None, use empty dict
                    if action_params is None:
                        action_params = {}

                    # If params is a string (malformed), try to parse it
                    if isinstance(action_params, str):
                        try:
                            action_params = json.loads(action_params)
                        except json.JSONDecodeError:
                            # Treat the string as the first positional arg
                            action_params = {"value": action_params}
                    elif not isinstance(action_params, dict):
                        # Handle primitives (int, bool, etc.) by wrapping them
                        action_params = {"value": action_params}

                    normalized.append({action_name: action_params})
                else:
                    # Multiple keys - treat as a single action with those params
                    normalized.append(action)

        return normalized

    async def _call_llm(self, messages: list[dict]) -> dict:
        """Call LLM with messages. Uses JSON Schema mode for structured output."""
        tools = self._registry.schema()
        logger.debug(
            f"Registry has {len(self._registry.actions)} actions, schema has {len(tools)} tools"
        )

        # Generate JSON schema for structured output
        from heimdall.agent.schema import create_agent_output_schema

        response_schema = create_agent_output_schema(tools)

        # Request structured output using JSON Schema mode
        # This enforces the format at API level - much more reliable
        response = await self._llm.chat_completion(
            messages=messages,
            tools=tools,
            tool_choice="auto",
            response_schema=response_schema,
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
    """Builds LLM messages with structured history context."""

    def __init__(self, extend_system_prompt: str | None = None):
        self._system_prompt_cache: str | None = None
        self._extend_system_prompt = extend_system_prompt

    def build(
        self,
        task: str,
        dom_state: Any,
        history: AgentHistoryList | None = None,
        step_info: tuple[int, int] | None = None,
        screenshot_b64: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build messages for LLM with structured history."""
        messages: list[dict[str, Any]] = []

        # System message
        messages.append(
            {
                "role": "system",
                "content": self._get_system_prompt(),
            }
        )

        # Build user content with structured sections
        user_content = ""

        # Agent history (structured format from previous steps)
        if history and len(history) > 0:
            history_text = history.format_for_prompt(max_items=10)
            if history_text:
                user_content += f"<agent_history>\n{history_text}\n</agent_history>\n\n"

        # User request
        user_content += f"<user_request>\n{task}\n</user_request>\n\n"

        # Step info
        if step_info:
            current_step, max_steps = step_info
            date_str = datetime.now().strftime("%Y-%m-%d")
            user_content += (
                f"<step_info>Step {current_step}/{max_steps} | Date: {date_str}</step_info>\n\n"
            )

        # Browser state
        dom_text = dom_state.text if hasattr(dom_state, "text") else str(dom_state)
        element_count = (
            dom_state.element_count if hasattr(dom_state, "element_count") else "unknown"
        )
        url = dom_state.url if hasattr(dom_state, "url") else "unknown"

        user_content += f"""<browser_state>
URL: {url}
Elements: {element_count}

Interactive elements:
{dom_text}
</browser_state>
"""

        # Build user message content (with optional vision)
        if screenshot_b64:
            # Vision-enabled message
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_content},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{screenshot_b64}",
                            },
                        },
                    ],
                }
            )
        else:
            messages.append(
                {
                    "role": "user",
                    "content": user_content,
                }
            )

        return messages

    def _get_system_prompt(self) -> str:
        """Load system prompt from template file."""
        if self._system_prompt_cache:
            return self._system_prompt_cache

        # Try to load from file
        base_prompt = None
        try:
            from pathlib import Path

            prompt_file = Path(__file__).parent / "prompts" / "system_prompt.md"
            if prompt_file.exists():
                base_prompt = prompt_file.read_text()
        except Exception:
            pass

        # Fallback to inline prompt
        if not base_prompt:
            base_prompt = """You are a browser automation agent.

Respond with JSON containing: thinking, evaluation_previous_goal, memory, next_goal, action.

Always respond with valid JSON, not plain text."""

        # Append custom instructions if provided
        if self._extend_system_prompt:
            base_prompt += (
                f"\n\n<custom_instructions>\n{self._extend_system_prompt}\n</custom_instructions>"
            )

        self._system_prompt_cache = base_prompt
        return self._system_prompt_cache
