"""
Logging Configuration - Structured logging setup.

Provides logging for debugging Heimdall agent runs.
Uses stdlib logging.
"""

import logging
from typing import Literal


def setup_logging(
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO",
    show_path: bool = False,
) -> None:
    """
    Configure logging.

    Args:
        level: Logging level
        show_path: Ignored, kept for compatibility
    """
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))

    heimdall_logger = logging.getLogger("heimdall")
    heimdall_logger.setLevel(level)
    heimdall_logger.handlers = [handler]
    heimdall_logger.propagate = False

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
    """

    def __init__(self, name: str = "heimdall"):
        self._logger = get_logger(name)

    def step(self, step_num: int, instruction: str) -> None:
        """Log step start."""
        truncated = instruction[:80] + ("..." if len(instruction) > 80 else "")
        self._logger.info(f"Step {step_num}: {truncated}")

    def action(self, name: str, target: str = "", result: str = "") -> None:
        """Log action execution."""
        msg = f"Action: {name}"
        if target:
            msg += f" → {target}"
        if result:
            msg += f" = {result}"
        self._logger.info(msg)

    def cdp(self, domain: str, command: str, params: dict | None = None) -> None:
        """Log CDP command (debug level)."""
        param_str = str(params)[:50] if params else ""
        self._logger.debug(f"CDP: {domain}.{command}({param_str})")

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
        self._logger.error(message, exc_info=exc)

    def warning(self, message: str) -> None:
        """Log warning."""
        self._logger.warning(message)

    def success(self, message: str) -> None:
        """Log success message."""
        self._logger.info(f"✓ {message}")

    def debug(self, message: str) -> None:
        """Log debug message."""
        self._logger.debug(message)

    def info(self, message: str) -> None:
        """Log info message."""
        self._logger.info(message)


# Default logger instance
logger = HeimdallLogger()
