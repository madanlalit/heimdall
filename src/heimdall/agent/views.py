"""
Agent Views - Pydantic models for structured agent output.

Defines the output format that makes the agent stateful by explicitly
tracking thinking, evaluation, memory, and goals across steps.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class ActionResult(BaseModel):
    """Result of executing an action."""

    # Action completion
    is_done: bool = False
    success: bool | None = None

    error: str | None = None

    extracted_content: str | None = None

    long_term_memory: str | None = None


class AgentBrain(BaseModel):
    """The agent's current mental state."""

    thinking: str | None = None
    evaluation_previous_goal: str
    memory: str
    next_goal: str


class AgentOutput(BaseModel):
    """
    Structured output from the agent LLM.

    This format makes the agent stateful by requiring explicit:
    - thinking: Extended reasoning about current state
    - evaluation_previous_goal: Analysis of what happened last step
    - memory: Working memory to track progress across steps
    - next_goal: Clear statement of next objective
    - action: Actions to execute
    """

    thinking: str | None = Field(
        default=None,
        description="Extended reasoning about current state, analyzing browser and history",
    )
    evaluation_previous_goal: str | None = Field(
        default=None,
        description="Evaluation of last action: success, failure, or uncertain with explanation",
    )
    memory: str | None = Field(
        default=None,
        description="Working memory to track progress (e.g., 'Visited 2/5 sites, found X')",
    )
    todo: list[str] | None = Field(
        default=None,
        description="List of remaining tasks to complete the overall goal",
    )
    next_goal: str | None = Field(
        default=None,
        description="Clear statement of next objective and action to achieve it",
    )
    action: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of actions to execute",
    )

    @property
    def current_state(self) -> AgentBrain:
        """Get the agent's mental state as AgentBrain."""
        return AgentBrain(
            thinking=self.thinking,
            evaluation_previous_goal=self.evaluation_previous_goal or "",
            memory=self.memory or "",
            next_goal=self.next_goal or "",
        )


class StepMetadata(BaseModel):
    """Metadata for a single step including timing information."""

    step_start_time: float
    step_end_time: float
    step_number: int

    @property
    def duration_seconds(self) -> float:
        """Calculate step duration in seconds."""
        return self.step_end_time - self.step_start_time


class BrowserStateSnapshot(BaseModel):
    """Snapshot of browser state for history."""

    url: str | None = None
    title: str | None = None
    element_count: int = 0
    screenshot_path: str | None = None
    screenshot_b64: str | None = None
    interacted_element: list[dict] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "url": self.url,
            "title": self.title,
            "element_count": self.element_count,
            "screenshot_path": self.screenshot_path,
            "screenshot_b64": self.screenshot_b64,
            "interacted_element": self.interacted_element,
        }


class AgentHistory(BaseModel):
    """History item for each agent step."""

    step_number: int
    model_input: list[dict[str, Any]] | None = None
    model_output: AgentOutput | None = None
    results: list[ActionResult] = Field(default_factory=list)
    state: BrowserStateSnapshot = Field(default_factory=BrowserStateSnapshot)
    metadata: StepMetadata | None = None
    state_message: str | None = None

    def format_for_prompt(self) -> str:
        """Format this history item for inclusion in the prompt."""
        if not self.model_output:
            return ""

        output = self.model_output
        lines = [f"<step_{self.step_number}>"]

        if output.evaluation_previous_goal:
            lines.append(f"Evaluation of Previous Step: {output.evaluation_previous_goal}")

        if output.memory:
            lines.append(f"Memory: {output.memory}")

        if output.todo:
            lines.append(f"Todo: {', '.join(output.todo)}")

        if output.next_goal:
            lines.append(f"Next Goal: {output.next_goal}")

        # Format action results
        if self.results:
            action_results = []
            for _i, (action, result) in enumerate(zip(output.action, self.results, strict=False)):
                action_name = list(action.keys())[0] if action else "unknown"
                if result.success:
                    status = "Success"
                    if result.extracted_content:
                        status += f" - {result.extracted_content}"
                else:
                    status = f"Failed: {result.error}"
                action_results.append(f"{action_name} → {status}")

            if action_results:
                lines.append(f"Action Results: {'; '.join(action_results)}")

        lines.append(f"</step_{self.step_number}>")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "step_number": self.step_number,
            "model_input": self.model_input,
            "model_output": self.model_output.model_dump() if self.model_output else None,
            "results": [r.model_dump() for r in self.results],
            "state": self.state.to_dict(),
            "metadata": self.metadata.model_dump() if self.metadata else None,
            "state_message": self.state_message,
        }


class AgentHistoryList(BaseModel):
    """List of agent history items."""

    history: list[AgentHistory] = Field(default_factory=list)

    def __len__(self) -> int:
        return len(self.history)

    def add(self, item: AgentHistory) -> None:
        """Add a history item."""
        self.history.append(item)

    def format_for_prompt(self, max_items: int | None = None) -> str:
        """Format history for inclusion in prompt."""
        items = self.history
        if max_items:
            items = items[-max_items:]

        return "\n".join(item.format_for_prompt() for item in items if item.model_output)

    def last_output(self) -> AgentOutput | None:
        """Get the last model output."""
        if self.history:
            return self.history[-1].model_output
        return None

    def is_done(self) -> bool:
        """Check if the last action was a done action."""
        if self.history and self.history[-1].results:
            return self.history[-1].results[-1].is_done
        return False

    def is_successful(self) -> bool | None:
        """Check if the task completed successfully."""
        if self.history and self.history[-1].results:
            last_result = self.history[-1].results[-1]
            if last_result.is_done:
                return last_result.success
        return None

    def total_duration_seconds(self) -> float:
        """Get total duration of all steps in seconds."""
        total = 0.0
        for h in self.history:
            if h.metadata:
                total += h.metadata.duration_seconds
        return total

    def screenshot_paths(self, n_last: int | None = None) -> list[str | None]:
        """Get all screenshot paths from history."""
        if n_last is None:
            return [h.state.screenshot_path for h in self.history]
        else:
            return [h.state.screenshot_path for h in self.history[-n_last:]]

    def agent_steps(self) -> list[str]:
        """Format agent history as readable step descriptions."""
        steps = []
        for i, h in enumerate(self.history):
            step_text = f"Step {i + 1}:\n"

            if h.model_output and h.model_output.action:
                actions_json = json.dumps(h.model_output.action, indent=1)
                step_text += f"Actions: {actions_json}\n"

            if h.results:
                for j, result in enumerate(h.results):
                    if result.extracted_content:
                        step_text += f"Result {j + 1}: {result.extracted_content}\n"
                    if result.error:
                        step_text += f"Error {j + 1}: {result.error}\n"

            steps.append(step_text)
        return steps

    def save_to_file(self, filepath: str | Path) -> None:
        """Save history to JSON file with proper serialization."""
        try:
            Path(filepath).parent.mkdir(parents=True, exist_ok=True)
            data = {"history": [h.to_dict() for h in self.history]}
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            raise e

    @classmethod
    def load_from_file(cls, filepath: str | Path) -> "AgentHistoryList":
        """Load history from JSON file."""
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        return cls.model_validate(data)
