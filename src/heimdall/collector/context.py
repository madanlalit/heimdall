"""
Context Collector - Captures step context for output.

Aggregates DOM state, screenshots, network activity, and actions
for each step of agent execution.
"""

import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.dom.service import SerializedDOM

logger = logging.getLogger(__name__)


class ElementContext(BaseModel):
    """Captured context for an element interaction."""

    backend_node_id: int
    tag: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    selectors: dict[str, str] = Field(default_factory=dict)
    bounding_box: dict[str, float] | None = None


class ActionContext(BaseModel):
    """Captured context for an action."""

    action: str
    params: dict = Field(default_factory=dict)
    success: bool = True
    message: str = ""
    error: str | None = None
    element: ElementContext | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class StepContext(BaseModel):
    """Complete context for a step."""

    step_number: int
    instruction: str = ""
    actions: list[ActionContext] = Field(default_factory=list)

    # DOM state
    dom_text: str = ""
    element_count: int = 0

    # Screenshots
    screenshot_before: str = ""  # Base64 or path
    screenshot_after: str = ""

    # Network
    network_requests: list[dict] = Field(default_factory=list)

    # Timing
    start_time: str = ""
    end_time: str = ""
    duration_ms: float = 0


class Collector:
    """
    Collects context during agent execution.

    Captures:
    - Before/after screenshots
    - DOM state
    - Network activity
    - Action details with element selectors
    """

    def __init__(
        self,
        session: "BrowserSession",
        output_dir: Path | str | None = None,
        capture_screenshots: bool = True,
        capture_network: bool = True,
    ):
        self._session = session
        self._output_dir = Path(output_dir) if output_dir else None
        self._capture_screenshots = capture_screenshots
        self._capture_network = capture_network

        self._steps: list[StepContext] = []
        self._current_step: StepContext | None = None
        self._network_requests: list[dict] = []

    async def start_step(
        self,
        step_number: int,
        instruction: str = "",
        dom_state: "SerializedDOM | None" = None,
    ) -> None:
        """Start capturing for a new step."""
        self._current_step = StepContext(
            step_number=step_number,
            instruction=instruction,
            start_time=datetime.now().isoformat(),
        )

        # Capture DOM state
        if dom_state:
            self._current_step.dom_text = dom_state.text
            self._current_step.element_count = dom_state.element_count

        # Capture before screenshot
        if self._capture_screenshots:
            try:
                data = await self._session.screenshot()
                self._current_step.screenshot_before = base64.b64encode(data).decode()
            except Exception as e:
                logger.debug(f"Before screenshot failed: {e}")

        # Clear network buffer
        self._network_requests.clear()

        logger.debug(f"Started capturing step {step_number}")

    async def record_action(
        self,
        action: str,
        params: dict,
        success: bool,
        message: str = "",
        error: str | None = None,
        element_info: dict | None = None,
    ) -> None:
        """Record an action within the current step."""
        if not self._current_step:
            logger.warning("No active step for action recording")
            return

        element = None
        if element_info:
            element = ElementContext(
                backend_node_id=element_info.get("backend_node_id", 0),
                tag=element_info.get("tag", ""),
                attributes=element_info.get("attributes", {}),
                selectors=element_info.get("selectors", {}),
            )

        action_ctx = ActionContext(
            action=action,
            params=params,
            success=success,
            message=message,
            error=error,
            element=element,
        )

        self._current_step.actions.append(action_ctx)

    async def record_network_request(
        self,
        url: str,
        method: str,
        status: int | None = None,
        response_type: str = "",
    ) -> None:
        """Record a network request."""
        self._network_requests.append(
            {
                "url": url,
                "method": method,
                "status": status,
                "type": response_type,
                "timestamp": datetime.now().isoformat(),
            }
        )

    async def end_step(self) -> StepContext | None:
        """End current step and return context."""
        if not self._current_step:
            return None

        self._current_step.end_time = datetime.now().isoformat()

        # Calculate duration
        try:
            start = datetime.fromisoformat(self._current_step.start_time)
            end = datetime.fromisoformat(self._current_step.end_time)
            self._current_step.duration_ms = (end - start).total_seconds() * 1000
        except Exception:
            pass

        # Capture after screenshot
        if self._capture_screenshots:
            try:
                data = await self._session.screenshot()
                self._current_step.screenshot_after = base64.b64encode(data).decode()
            except Exception as e:
                logger.debug(f"After screenshot failed: {e}")

        # Copy network requests
        self._current_step.network_requests = list(self._network_requests)

        # Store step
        step = self._current_step
        self._steps.append(step)
        self._current_step = None

        logger.debug(f"Ended step {step.step_number} ({step.duration_ms:.0f}ms)")
        return step

    def get_all_steps(self) -> list[StepContext]:
        """Get all captured steps."""
        return list(self._steps)

    def export(self) -> dict:
        """Export collected context as dict."""
        return {
            "steps": [s.model_dump() for s in self._steps],
            "total_steps": len(self._steps),
            "timestamp": datetime.now().isoformat(),
        }

    def clear(self) -> None:
        """Clear all captured data."""
        self._steps.clear()
        self._current_step = None
        self._network_requests.clear()
