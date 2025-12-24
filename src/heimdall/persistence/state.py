"""
Persistence - State management for long-running tasks.

Provides state saving/loading and progress tracking via files.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TaskProgress(BaseModel):
    """Task progress tracking."""

    completed: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)
    current: str = ""


class PersistedState(BaseModel):
    """Persistable agent state."""

    task: str = ""
    step_count: int = 0
    done: bool = False
    actions_taken: list[dict] = Field(default_factory=list)
    progress: TaskProgress = Field(default_factory=TaskProgress)
    last_url: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


class StateManager:
    """
    Manages persistent state for resumable tasks.

    Files created in workspace:
    - .heimdall_state.json - Serialized agent state
    - todo.md - Human-readable progress
    - results.md - Step results log
    """

    def __init__(self, workspace: Path | str):
        self._workspace = Path(workspace)
        self._workspace.mkdir(parents=True, exist_ok=True)

        self._state_file = self._workspace / ".heimdall_state.json"
        self._todo_file = self._workspace / "todo.md"
        self._results_file = self._workspace / "results.md"

    async def save_state(self, state: PersistedState) -> None:
        """Save agent state to file."""
        self._state_file.write_text(state.model_dump_json(indent=2))
        logger.debug(f"State saved to {self._state_file}")

    async def load_state(self) -> PersistedState | None:
        """Load agent state from file."""
        if not self._state_file.exists():
            return None

        try:
            data = json.loads(self._state_file.read_text())
            state = PersistedState.model_validate(data)
            logger.debug(f"State loaded from {self._state_file}")
            return state
        except Exception as e:
            logger.warning(f"Could not load state: {e}")
            return None

    async def clear_state(self) -> None:
        """Remove saved state."""
        if self._state_file.exists():
            self._state_file.unlink()

    async def update_todo(self, progress: TaskProgress) -> None:
        """Update todo.md with progress."""
        content = "# Task Progress\n\n"

        if progress.current:
            content += f"## Current\n\n- [ ] {progress.current}\n\n"

        if progress.completed:
            content += "## Completed\n\n"
            for item in progress.completed:
                content += f"- [x] {item}\n"
            content += "\n"

        if progress.pending:
            content += "## Pending\n\n"
            for item in progress.pending:
                content += f"- [ ] {item}\n"
            content += "\n"

        self._todo_file.write_text(content)
        logger.debug(
            f"Todo updated: {len(progress.completed)} done, {len(progress.pending)} pending"
        )

    async def append_result(
        self,
        step_num: int,
        action: str,
        success: bool,
        message: str = "",
    ) -> None:
        """Append result to results.md."""
        status = "✓" if success else "✗"
        timestamp = datetime.now().strftime("%H:%M:%S")

        entry = f"\n## Step {step_num} ({timestamp})\n\n"
        entry += f"- Action: {action}\n"
        entry += f"- Status: {status}\n"
        if message:
            entry += f"- Result: {message}\n"

        # Append or create file
        mode = "a" if self._results_file.exists() else "w"
        with open(self._results_file, mode) as f:
            if mode == "w":
                f.write("# Heimdall Results Log\n")
            f.write(entry)

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def has_saved_state(self) -> bool:
        """Check if saved state exists."""
        return self._state_file.exists()
