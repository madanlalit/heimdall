"""
File System - Simple file system for agent data persistence.

Manages files like todo.md for the agent to track progress.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class FileSystem:
    """Simple file system for agent data persistence."""

    def __init__(self, base_dir: str | Path | None = None):
        """
        Initialize file system.

        Args:
            base_dir: Optional override for data directory.
                      If None, uses a hidden .heimdall directory in current working directory.
        """
        if base_dir:
            self.data_dir = Path(base_dir)
        else:
            self.data_dir = Path.cwd() / ".heimdall"

        self.data_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Using data directory: {self.data_dir}")

        self._init_todo()

    def _init_todo(self) -> None:
        """Initialize todo.md file if it doesn't exist."""
        todo_path = self.data_dir / "todo.md"
        if not todo_path.exists():
            todo_path.write_text("# Agent Todo\n\n")

    @property
    def todo_path(self) -> Path:
        """Get path to todo.md file."""
        return self.data_dir / "todo.md"

    def read_todo(self) -> str:
        """Read todo.md contents."""
        try:
            return self.todo_path.read_text()
        except Exception as e:
            logger.warning(f"Failed to read todo.md: {e}")
            return ""

    def write_todo(self, content: str) -> None:
        """Write content to todo.md (replaces entire file)."""
        try:
            self.todo_path.write_text(content)
        except Exception as e:
            logger.error(f"Failed to write todo.md: {e}")

    def update_todo(self, tasks: list[str]) -> None:
        """Update todo.md with a list of tasks."""
        if not tasks:
            content = "# Agent Todo\n\n_No pending tasks_\n"
        else:
            lines = ["# Agent Todo\n"]
            for task in tasks:
                lines.append(f"- [ ] {task}\n")
            content = "".join(lines)

        self.write_todo(content)

    def read_file(self, filename: str) -> str | None:
        """Read a file from the data directory."""
        file_path = self.data_dir / filename
        if not file_path.exists():
            return None
        try:
            return file_path.read_text()
        except Exception as e:
            logger.warning(f"Failed to read {filename}: {e}")
            return None

    def write_file(self, filename: str, content: str) -> bool:
        """Write content to a file in the data directory."""
        try:
            file_path = self.data_dir / filename
            file_path.write_text(content)
            return True
        except Exception as e:
            logger.error(f"Failed to write {filename}: {e}")
            return False

    def append_file(self, filename: str, content: str) -> bool:
        """Append content to a file in the data directory."""
        try:
            file_path = self.data_dir / filename
            with open(file_path, "a") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"Failed to append to {filename}: {e}")
            return False

    def list_files(self) -> list[str]:
        """List all files in the data directory."""
        return [f.name for f in self.data_dir.iterdir() if f.is_file()]

    def get_dir(self) -> Path:
        """Get the data directory path."""
        return self.data_dir

    def cleanup(self) -> None:
        """Remove all files in the data directory."""
        import shutil

        if self.data_dir.exists():
            shutil.rmtree(self.data_dir)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._init_todo()
