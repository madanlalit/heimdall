"""
Agent Loop - Main orchestration loop for Heimdall agent.

Manages the think → act → observe cycle for browser automation.
Uses structured output for stateful reasoning across steps.
"""

import asyncio
import base64
import json
import logging
import signal
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from heimdall.agent.views import (
    ActionResult,
    AgentHistory,
    AgentHistoryList,
    AgentOutput,
    BrowserStateSnapshot,
    StepMetadata,
)
from heimdall.events.bus import EventBus
from heimdall.utils.media import save_screenshot_async
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

    max_steps: int = 100
    max_retries: int = 3
    max_consecutive_failures: int = 5
    step_timeout: float = 60.0

    # Vision: send screenshots to LLM
    use_vision: bool = False

    # Demo mode: visual feedback during execution
    demo_mode: bool = False

    # Stateful reasoning options
    use_thinking: bool = True
    flash_mode: bool = False
    max_actions_per_step: int = 3

    wait_for_stability: bool = True
    stability_timeout: float = 5.0
    network_idle_timeout: float = 2.0

    # Domain restriction
    # Examples: ["chatgpt.com", "*.openai.com", "google.com"]
    allowed_domains: list[str] = Field(default_factory=list)

    # Custom instructions to extend the system prompt
    extend_system_prompt: str | None = None

    # Tracing options
    save_trace_path: str | Path | None = None
    capture_screenshots: bool = False
    use_collector: bool = False

    # State persistence for pause/resume
    workspace_path: str | Path | None = None
    enable_persistence: bool = True
    run_id: str | None = None  # Specific run ID to resume (if provided)


class AgentState(BaseModel):
    """Agent execution state."""

    step_count: int = 0
    done: bool = False
    success: bool = False
    error: str | None = None

    consecutive_failures: int = 0
    total_failures: int = 0
    previous_url: str | None = None


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

        # Initialize demo mode if enabled
        self._demo_mode = None
        if self._config.demo_mode:
            from heimdall.browser.demo import DemoMode

            self._demo_mode = DemoMode(self._session)
            logger.info("Demo mode enabled - visual feedback active")

        # Initialize collector for detailed step capture
        self._collector = None
        if self._config.use_collector and self._config.save_trace_path:
            from heimdall.collector import Collector

            output_dir = Path(self._config.save_trace_path).parent
            self._collector = Collector(
                session,
                output_dir,
                capture_screenshots=self._config.capture_screenshots or self._config.use_collector,
                capture_network=True,
            )
            logger.info("Collector enabled - detailed step capture active")

        # Initialize watchdogs
        self._watchdogs = {
            "navigation": NavigationWatchdog(self._session, self._bus),
            "network": NetworkWatchdog(self._session, self._bus),
            "dom": DOMWatchdog(self._session, self._bus),
            "error": ErrorWatchdog(self._session, self._bus),
        }

        from heimdall.agent.filesystem import FileSystem

        self._filesystem = FileSystem()

        # Pause/resume state
        self._paused = False
        self._pause_requested = False
        self._session_id = str(uuid.uuid4())[:8]
        self._task: str = ""  # Store task for state persistence
        self._resume_event = asyncio.Event()

        # Initialize state manager for persistence
        self._state_manager = None
        self._run_id: str | None = None
        if self._config.enable_persistence and self._config.workspace_path:
            from heimdall.persistence import StateManager

            # Use provided run_id or generate new one
            self._run_id = self._config.run_id or str(uuid.uuid4())[:8]
            self._state_manager = StateManager(
                Path(self._config.workspace_path), run_id=self._run_id
            )
            logger.info(f"Run ID: {self._run_id}")
            logger.info(f"State persistence enabled - workspace: {self._config.workspace_path}")

        self._subscribe_to_events()

    async def run(self, task: str) -> AgentHistoryList:
        """
        Run agent to complete a task.

        Supports interactive pause/resume:
        - Ctrl+C: Pause execution and save state
        - Enter: Resume execution
        - Ctrl+C again (while paused): Exit completely

        Args:
            task: Natural language task description

        Returns:
            Final agent state
        """
        logger.info(f"Starting task: {task[:80]}")
        self._task = task

        # Try to resume from state if run_id was provided
        restored = False
        if (
            self._config.enable_persistence
            and self._config.run_id  # Only resume if specific run_id provided
            and self._state_manager
            and self._state_manager.has_saved_state
        ):
            try:
                persisted = await self._state_manager.load_state()

                # Check various conditions and provide specific error messages
                if persisted and persisted.done:
                    logger.warning(
                        f"Run {self._run_id} is already completed. "
                        f"Please start a new run without --run-id flag."
                    )
                elif persisted and persisted.task != task:
                    logger.warning(
                        f"Run {self._run_id} has a different task. "
                        f"The saved task does not match the current task."
                    )
                elif persisted and not persisted.paused:
                    logger.warning(
                        f"Run {self._run_id} was not paused (possibly crashed). "
                        f"Cannot resume non-paused sessions."
                    )
                elif (
                    persisted and persisted.task == task and not persisted.done and persisted.paused
                ):
                    # Valid resume - restore state
                    self._session_id = persisted.session_id
                    self._state = AgentState(
                        step_count=persisted.step_count,
                        done=persisted.done,
                        success=persisted.success,
                        error=persisted.error,
                        consecutive_failures=persisted.consecutive_failures,
                        total_failures=persisted.total_failures,
                        previous_url=persisted.last_url,
                    )
                    self._history = AgentHistoryList()
                    for h in persisted.history:
                        self._history.add(AgentHistory.model_validate(h))

                    restored = True
                    logger.info(
                        f"Resumed run {self._run_id} (session {self._session_id}) "
                        f"at step {self._state.step_count}"
                    )
            except Exception as e:
                logger.warning(f"Failed to resume run {self._run_id}: {e}")

        if not restored:
            self._state = AgentState()
            self._history = AgentHistoryList()
            if self._run_id:
                logger.info(f"Started new run: {self._run_id}")

        self._paused = False
        self._pause_requested = False
        self._exit_requested = False

        self._filesystem.update_todo([f"Complete: {task[:100]}"])

        for w in self._watchdogs.values():
            await w.start()

        # Set up SIGINT handler for pause/resume using asyncio (safer for async code)
        loop = asyncio.get_running_loop()

        def sigint_handler():
            if self._paused:
                # Already paused - request exit
                logger.info("Second Ctrl+C received - will exit...")
                self._exit_requested = True
                self._resume_event.set()
            else:
                # First Ctrl+C - request pause
                self._pause_requested = True
                print()  # New line after ^C
                logger.info("Pause requested... will pause after current step")

        # Add signal handler (asyncio-compatible, won't interrupt async operations)
        try:
            loop.add_signal_handler(signal.SIGINT, sigint_handler)
            signal_handler_installed = True
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            signal_handler_installed = False
            signal.signal(signal.SIGINT, lambda s, f: sigint_handler())

        try:
            while not self._should_stop():
                # Check for pause request
                if self._pause_requested and not self._paused:
                    await self._handle_pause()
                    self._pause_requested = False

                    # Check if exit was requested during pause
                    if self._exit_requested:
                        logger.info("Exiting...")
                        break

                await self._execute_step(task)

            self._state.success = self._state.done and not self._state.error

        except Exception as e:
            logger.error(f"Agent error: {e}")
            self._state.error = str(e)
            self._state.success = False

        finally:
            # Remove signal handler
            if signal_handler_installed:
                import contextlib

                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(signal.SIGINT)

            for w in self._watchdogs.values():
                await w.stop()

            self._unsubscribe_from_events()

            # Save trace (success or failure/interrupt)
            if self._config.save_trace_path and len(self._history) > 0:
                try:
                    self._history.save_to_file(self._config.save_trace_path)
                    logger.info(f"Trace saved to: {self._config.save_trace_path}")
                except Exception as e:
                    logger.error(f"Failed to save trace: {e}")

            # Export collector data (success or failure/interrupt)
            if self._collector and self._config.save_trace_path:
                try:
                    from heimdall.collector import Exporter

                    exporter = Exporter(Path(self._config.save_trace_path).parent)
                    await asyncio.to_thread(
                        exporter.export_steps,
                        self._collector.export()["steps"],
                        "collector_steps.json",
                    )
                    logger.info("Collector steps exported")
                except Exception as e:
                    logger.error(f"Failed to export collector data: {e}")

        logger.info(f"Task complete: success={self._state.success}")

        return self._history

    async def _handle_pause(self) -> None:
        """Handle pause request - save state and wait for resume."""
        from rich.console import Console

        console = Console()

        self._paused = True
        await self._save_state(paused=True)

        console.print("\n[bold yellow]⏸️  Execution paused[/bold yellow]")
        console.print(f"   Step: {self._state.step_count}/{self._config.max_steps}")
        console.print(f"   Session: {self._session_id}")

        if self._state_manager:
            console.print(
                f"   State saved to: {self._state_manager.workspace}/.heimdall_state.json"
            )

        console.print(
            "\n   Press [bold green]Enter[/bold green] to resume, "
            "or [bold red]Ctrl+C[/bold red] to exit...\n"
        )

        # Clear event from previous runs
        self._resume_event.clear()

        # Define input reader that sets the event
        loop = asyncio.get_running_loop()

        def read_input():
            try:
                input()
            except EOFError:
                pass
            finally:
                loop.call_soon_threadsafe(self._resume_event.set)

        # Start input thread
        loop.run_in_executor(None, read_input)

        # Wait for either Input (Enter) or Signal (Ctrl+C setting the event)
        await self._resume_event.wait()

        if self._exit_requested:
            # Loop in run() will handle the break
            pass
        else:
            console.print("[bold green]▶️  Resuming execution...[/bold green]\n")
            self._paused = False

    async def _save_state(self, paused: bool = False) -> None:
        """Save current agent state for persistence."""
        if not self._state_manager:
            return

        try:
            from heimdall.persistence import PersistedState, TaskProgress

            # Serialize history
            history_data = [h.to_dict() for h in self._history.history]

            # Build progress from agent output
            last_output = self._history.last_output()
            progress = TaskProgress(
                completed=[],
                pending=last_output.todo if last_output and last_output.todo else [],
                current=last_output.next_goal or "" if last_output else "",
            )

            state = PersistedState(
                session_id=self._session_id,
                task=self._task,
                step_count=self._state.step_count,
                done=self._state.done,
                success=self._state.success,
                error=self._state.error,
                consecutive_failures=self._state.consecutive_failures,
                total_failures=self._state.total_failures,
                last_url=self._state.previous_url or "",
                history=history_data,
                progress=progress,
                paused=paused,
                paused_at=datetime.now().isoformat() if paused else None,
            )

            await self._state_manager.save_state(state)
            logger.debug(f"State saved: step={state.step_count}, paused={paused}")

        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

    async def _execute_step(self, task: str) -> None:
        """Execute one agent step with structured output."""
        self._state.step_count += 1
        step_number = self._state.step_count
        step_start_time = time.time()

        logger.debug(f"Step {step_number}")

        # 1. Get DOM state
        dom_state = await self._dom_service.get_state()
        self._registry.set_context(
            self._session,
            dom_state,
            allowed_domains=self._config.allowed_domains,
        )

        # Start collector step capture
        if self._collector:
            await self._collector.start_step(step_number, instruction=task, dom_state=dom_state)

        # 2. Capture screenshot
        screenshot_b64 = None
        screenshot_path = None

        if self._config.use_vision or self._config.capture_screenshots:
            try:
                screenshot_data = await self._session.screenshot()
                screenshot_b64 = base64.b64encode(screenshot_data).decode()

                if self._config.save_trace_path:
                    save_dir = Path(self._config.save_trace_path).parent / "screenshots"
                    screenshot_path = str(save_dir / f"step_{step_number}.png")

                    # Non-blocking save
                    asyncio.create_task(save_screenshot_async(screenshot_data, screenshot_path))

            except Exception as e:
                logger.debug(f"Screenshot capture failed: {e}")

        current_url = getattr(dom_state, "url", "unknown")

        # 3. Build messages
        js_errors = self._watchdogs["error"].js_errors  # type: ignore
        failed_requests = self._watchdogs["network"].failed_requests  # type: ignore

        messages = self._message_builder.build(
            task=task,
            dom_state=dom_state,
            history=self._history,
            step_info=(step_number, self._config.max_steps),
            screenshot_b64=screenshot_b64,
            errors=js_errors,
            network_failures=failed_requests,
            previous_url=self._state.previous_url,
        )

        # 4. Get LLM response
        try:
            response = await self._call_llm(messages)
        except Exception as e:
            import traceback

            logger.error(f"LLM call failed: {e}\n{traceback.format_exc()}")
            self._state.consecutive_failures += 1
            return

        self._state.previous_url = current_url

        self._watchdogs["error"].clear_errors()  # type: ignore
        self._watchdogs["network"].clear_failed_requests()  # type: ignore

        # 5. Parse output
        agent_output = self._parse_agent_output(response)

        if not agent_output or not agent_output.action:
            logger.warning("No valid actions in response")
            self._state.consecutive_failures += 1
            return

        if agent_output.evaluation_previous_goal:
            logger.info(f"Evaluation: {agent_output.evaluation_previous_goal}")
        if agent_output.next_goal:
            logger.info(f"Next Goal: {agent_output.next_goal}")

        # 6. Execute actions
        results: list[ActionResult] = []
        page_changed = False

        for action_dict in agent_output.action[: self._config.max_actions_per_step]:
            if page_changed:
                logger.debug("Page changed, skipping remaining actions")
                break

            if not action_dict:
                continue

            action_name = list(action_dict.keys())[0]
            action_args = action_dict[action_name] or {}

            logger.info(f"Tool call: {action_name}({json.dumps(action_args)[:50]}...)")

            # Demo mode: show visual feedback before action
            if self._demo_mode:
                await self._show_demo_feedback(action_name, action_args, dom_state)

            exec_result = await self._registry.execute(action_name, action_args)

            # Record action in collector
            if self._collector:
                await self._collector.record_action(
                    action=action_name,
                    params=action_args,
                    success=exec_result.success,
                    message=exec_result.message or "",
                    error=exec_result.error,
                    element_info=exec_result.data.get("element"),
                )

            result = ActionResult(
                is_done=action_name == "done",
                success=exec_result.success,
                error=exec_result.error,
                extracted_content=exec_result.message,
            )
            results.append(result)

            if exec_result.success:
                self._state.consecutive_failures = 0

                if action_name == "done":
                    self._state.done = True
                    self._state.success = action_args.get("success", True)
                    break

                # Wait for page stability
                if self._config.wait_for_stability and action_name in (
                    "click",
                    "navigate",
                    "type_text",
                    "press_key",
                ):
                    try:
                        logger.debug("Waiting for page stability...")

                        nav_watchdog: NavigationWatchdog = self._watchdogs["navigation"]  # type: ignore
                        nav_complete = await nav_watchdog.wait_for_load(
                            timeout=self._config.stability_timeout
                        )

                        if nav_complete:
                            logger.debug("Navigation load complete")

                        net_watchdog: NetworkWatchdog = self._watchdogs["network"]  # type: ignore
                        net_idle = await net_watchdog.wait_for_idle(
                            timeout=self._config.network_idle_timeout
                        )

                        if net_idle:
                            logger.debug("Network idle detected")
                        else:
                            logger.debug(
                                f"Network idle timeout ({net_watchdog.pending_count} pending)"
                            )

                        new_dom = await self._dom_service.get_state()
                        if (
                            hasattr(new_dom, "url")
                            and hasattr(dom_state, "url")
                            and new_dom.url != dom_state.url
                        ):
                            page_changed = True
                            logger.debug(f"Page changed: {dom_state.url} → {new_dom.url}")
                    except Exception as e:
                        logger.debug(f"Smart wait failed: {e}")
            else:
                self._state.consecutive_failures += 1
                self._state.total_failures += 1
                logger.warning(f"Action failed: {exec_result.error}")

        if self._collector and self._config.save_trace_path:
            await self._collector.end_step()
            try:
                from heimdall.collector import Exporter

                exporter = Exporter(Path(self._config.save_trace_path).parent)
                await asyncio.to_thread(
                    exporter.export_steps,
                    self._collector.export()["steps"],
                    "collector_steps.json",
                )
                logger.debug("Collector steps exported (incremental)")
            except Exception as e:
                logger.warning(f"Failed to export collector data: {e}")

        # 7. Record history
        step_end_time = time.time()
        history_item = AgentHistory(
            step_number=step_number,
            model_input=messages,
            model_output=agent_output,
            results=results,
            state=BrowserStateSnapshot(
                url=getattr(dom_state, "url", None),
                title=getattr(dom_state, "title", None),
                element_count=getattr(dom_state, "element_count", 0),
                screenshot_path=screenshot_path,
                screenshot_b64=screenshot_b64 if self._config.capture_screenshots else None,
            ),
            metadata=StepMetadata(
                step_start_time=step_start_time,
                step_end_time=step_end_time,
                step_number=step_number,
            ),
        )
        self._history.add(history_item)

        # Save trace
        if self._config.save_trace_path:
            try:
                self._history.save_to_file(self._config.save_trace_path)
                logger.debug(f"Trace updated: {self._config.save_trace_path}")
            except Exception as e:
                logger.warning(f"Failed to update trace: {e}")

        # 8. Update todo
        if agent_output.todo:
            self._filesystem.update_todo(agent_output.todo)

        await asyncio.sleep(0.2)

    def _parse_agent_output(self, response: dict) -> AgentOutput | None:
        """Parse LLM response into structured AgentOutput."""
        content = response.get("content", "")

        if not content:
            # Fallback: tool calls
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

        # Parse JSON
        try:
            from heimdall.utils.text import extract_json_from_markdown

            content = extract_json_from_markdown(content)

            data = json.loads(content)

            actions = data.get("action", [])
            if isinstance(actions, dict):
                actions = [actions]

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

        from heimdall.agent.schema import create_agent_output_schema

        response_schema = create_agent_output_schema(tools)

        # Request structured output using JSON Schema mode
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

    def _subscribe_to_events(self) -> None:
        """Subscribe to watchdog events."""
        from heimdall.events.types import (
            DOMChangedEvent,
            ErrorEvent,
            NavigationCompletedEvent,
            NetworkIdleEvent,
            NetworkRequestCompletedEvent,
        )

        self._bus.on(NavigationCompletedEvent, self._on_navigation_completed)
        self._bus.on(NetworkIdleEvent, self._on_network_idle)
        self._bus.on(DOMChangedEvent, self._on_dom_changed)
        self._bus.on(ErrorEvent, self._on_error)
        self._bus.on(NetworkRequestCompletedEvent, self._on_network_request_completed)

        logger.debug("Subscribed to watchdog events")

    def _unsubscribe_from_events(self) -> None:
        """Unsubscribe from watchdog events."""
        from heimdall.events.types import (
            DOMChangedEvent,
            ErrorEvent,
            NavigationCompletedEvent,
            NetworkIdleEvent,
            NetworkRequestCompletedEvent,
        )

        self._bus.off(NetworkRequestCompletedEvent, self._on_network_request_completed)
        self._bus.off(NavigationCompletedEvent, self._on_navigation_completed)
        self._bus.off(NetworkIdleEvent, self._on_network_idle)
        self._bus.off(DOMChangedEvent, self._on_dom_changed)
        self._bus.off(ErrorEvent, self._on_error)

        logger.debug("Unsubscribed from watchdog events")

    async def _on_navigation_completed(self, event: Any) -> None:
        """Handle navigation completed event."""
        logger.info(f"Navigation completed: {event.url}")

    async def _on_network_idle(self, event: Any) -> None:
        """Handle network idle event."""
        logger.debug("Network idle detected")

    async def _on_dom_changed(self, event: Any) -> None:
        """Handle DOM changed event."""
        logger.debug(f"DOM changed: +{event.added_nodes} -{event.removed_nodes} nodes")

    async def _on_error(self, event: Any) -> None:
        """Handle error event."""
        logger.warning(f"Browser error: {event.error_type} - {event.message}")

    async def _on_network_request_completed(self, event: Any) -> None:
        """Handle network request completed event."""
        if self._collector:
            await self._collector.record_network_request(
                url=event.url,
                method=event.method,
                status=event.status,
                response_type=event.mime_type,
                params=event.params,
            )

    async def _show_demo_feedback(
        self, action_name: str, action_args: dict, dom_state: Any
    ) -> None:
        """
        Show visual feedback for demo mode before executing an action.

        Args:
            action_name: Name of the action being performed
            action_args: Arguments for the action
            dom_state: Current DOM state (passed to avoid race conditions)

        Displays:
        - Tooltip with action name and target description
        - Element highlight using CDP for accurate targeting
        """
        if not self._demo_mode:
            return

        try:
            # Build description from action args
            description = ""
            if "index" in action_args:
                description = f"element [{action_args['index']}]"
            elif "url" in action_args:
                description = action_args["url"][:50]
            elif "text" in action_args:
                description = f'"{action_args["text"][:30]}..."'

            # Show action tooltip
            await self._demo_mode.show_action(action_name, description)

            # Highlight target element using CDP for accurate visual feedback
            if action_name in ("click", "type_text", "hover") and "index" in action_args:
                index = action_args["index"]
                # Use passed dom_state to avoid race condition
                element_info = dom_state.selector_map.get(index)

                if element_info and "backend_node_id" in element_info:
                    await self._demo_mode.highlight_element_cdp(
                        element_info["backend_node_id"],
                        duration=1.0,
                    )
                    # Small delay for visibility
                    await asyncio.sleep(0.5)

        except Exception as e:
            logger.debug(f"Demo feedback failed: {e}")


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
        errors: list[dict] | None = None,
        network_failures: list[dict] | None = None,
        previous_url: str | None = None,
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

        # Scroll info
        scroll_str = ""
        if hasattr(dom_state, "scroll_info") and dom_state.scroll_info:
            info = dom_state.scroll_info
            scroll_str = (
                f"Scroll: {info.get('x', 0)}, {info.get('y', 0)} "
                f"(Viewport: {info.get('width', 0)}x{info.get('height', 0)})"
            )

        user_content += f"""<browser_state>
URL: {url}
Previous URL: {previous_url or "unknown"}
Elements: {element_count}
{scroll_str}

Interactive elements:
{dom_text}
</browser_state>\n\n"""

        # Errors
        if errors:
            error_lines = []
            for err in errors:
                if err.get("type") == "exception":
                    error_lines.append(
                        f"- [JS] {err.get('message')} at {err.get('url')}:{err.get('line')}"
                    )
                else:
                    error_lines.append(f"- [Console] {err.get('message')}")

            if error_lines:
                user_content += (
                    "<browser_errors>\n" + "\n".join(error_lines) + "\n</browser_errors>\n\n"
                )

        # Network Failures
        if network_failures:
            fail_lines = []
            for fail in network_failures:
                fail_lines.append(f"- [Failed] {fail.get('url')} ({fail.get('error')})")

            if fail_lines:
                user_content += (
                    "<network_activity>\n" + "\n".join(fail_lines) + "\n</network_activity>\n\n"
                )

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
