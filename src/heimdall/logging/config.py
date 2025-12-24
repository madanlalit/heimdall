"""
Logging Configuration - Structured logging with Rich console.

Provides pretty, structured logging for debugging Heimdall agent runs.
"""

import logging
from typing import Literal

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# Custom theme for Heimdall logs
HEIMDALL_THEME = Theme(
    {
        "info": "cyan",
        "warning": "yellow",
        "error": "bold red",
        "debug": "dim",
        "step": "bold green",
        "action": "bold magenta",
        "cdp": "dim cyan",
    }
)

# Shared console instance
console = Console(theme=HEIMDALL_THEME)


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    show_path: bool = False,
) -> None:
    """
    Configure logging with Rich console handler.

    Args:
        level: Logging level
        show_path: Show file path in log messages
    """
    # Configure Rich handler
    handler = RichHandler(
        console=console,
        show_time=True,
        show_level=True,
        show_path=show_path,
        rich_tracebacks=True,
        tracebacks_show_locals=True,
        markup=True,
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    # Configure root heimdall logger
    heimdall_logger = logging.getLogger("heimdall")
    heimdall_logger.setLevel(level)
    heimdall_logger.handlers = [handler]
    heimdall_logger.propagate = False

    # Also set level for submodules
    for name in ["heimdall.browser", "heimdall.agent", "heimdall.dom", "heimdall.watchdogs"]:
        logging.getLogger(name).setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with heimdall prefix.

    Args:
        name: Logger name (will be prefixed with 'heimdall.')

    Returns:
        Configured logger
    """
    if not name.startswith("heimdall."):
        name = f"heimdall.{name}"
    return logging.getLogger(name)


class HeimdallLogger:
    """
    Structured logger for Heimdall operations.

    Provides semantic logging methods for different operation types.
    """

    def __init__(self, name: str = "heimdall"):
        self._logger = get_logger(name)

    def step(self, step_num: int, instruction: str) -> None:
        """Log step start."""
        truncated = instruction[:80] + ("..." if len(instruction) > 80 else "")
        self._logger.info(f"[step]Step {step_num}[/step]: {truncated}")

    def action(self, name: str, target: str = "", result: str = "") -> None:
        """Log action execution."""
        msg = f"[action]{name}[/action]"
        if target:
            msg += f" → {target}"
        if result:
            msg += f" = {result}"
        self._logger.info(msg)

    def cdp(self, domain: str, command: str, params: dict | None = None) -> None:
        """Log CDP command (debug level)."""
        param_str = str(params)[:50] if params else ""
        self._logger.debug(f"[cdp]CDP[/cdp]: {domain}.{command}({param_str})")

    def element(self, action: str, backend_node_id: int, details: str = "") -> None:
        """Log element interaction."""
        msg = f"Element[{backend_node_id}] {action}"
        if details:
            msg += f" - {details}"
        self._logger.debug(msg)

    def navigation(self, url: str, status: str = "started") -> None:
        """Log navigation event."""
        self._logger.info(f"Navigation {status}: {url[:60]}{'...' if len(url) > 60 else ''}")

    def network(self, method: str, url: str, status: int | None = None) -> None:
        """Log network request."""
        msg = f"{method} {url[:50]}"
        if status:
            msg += f" → {status}"
        self._logger.debug(msg)

    def error(self, message: str, exc: Exception | None = None) -> None:
        """Log error."""
        self._logger.error(f"[error]{message}[/error]", exc_info=exc)

    def warning(self, message: str) -> None:
        """Log warning."""
        self._logger.warning(f"[warning]{message}[/warning]")

    def success(self, message: str) -> None:
        """Log success message."""
        self._logger.info(f"[green]✓[/green] {message}")

    def debug(self, message: str) -> None:
        """Log debug message."""
        self._logger.debug(message)

    def info(self, message: str) -> None:
        """Log info message."""
        self._logger.info(message)


# Default logger instance
logger = HeimdallLogger()
