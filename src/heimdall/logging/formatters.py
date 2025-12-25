"""
Log Formatters - Custom formatters for structured output.

Provides formatters for different output contexts.
"""

import json
import logging
from datetime import UTC, datetime


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured log output.

    Useful for log aggregation and analysis.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields
        if hasattr(record, "step_num"):
            log_data["step"] = record.step_num
        if hasattr(record, "action"):
            log_data["action"] = record.action
        if hasattr(record, "element_id"):
            log_data["element_id"] = record.element_id

        # Add exception info
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class CompactFormatter(logging.Formatter):
    """
    Compact single-line formatter for console output.
    """

    LEVEL_SYMBOLS = {
        "DEBUG": "·",
        "INFO": "→",
        "WARNING": "⚠",
        "ERROR": "✗",
        "CRITICAL": "☠",
    }

    def format(self, record: logging.LogRecord) -> str:
        symbol = self.LEVEL_SYMBOLS.get(record.levelname, "?")
        timestamp = datetime.now().strftime("%H:%M:%S")
        return f"{timestamp} {symbol} {record.getMessage()}"


class StepFormatter(logging.Formatter):
    """
    Formatter for step-based output with visual hierarchy.
    """

    def format(self, record: logging.LogRecord) -> str:
        # Build prefix based on context
        prefix = ""

        if hasattr(record, "step_num"):
            prefix = f"[Step {record.step_num}] "
        elif hasattr(record, "action"):
            prefix = f"  └─ {record.action}: "
        elif record.name.endswith(".cdp"):
            prefix = "    · "

        return f"{prefix}{record.getMessage()}"


def create_file_handler(
    path: str,
    formatter: logging.Formatter | None = None,
    level: int = logging.DEBUG,
) -> logging.FileHandler:
    """
    Create a file handler with specified formatter.

    Args:
        path: Log file path
        formatter: Log formatter (defaults to JSONFormatter)
        level: Logging level

    Returns:
        Configured file handler
    """
    handler = logging.FileHandler(path)
    handler.setLevel(level)
    handler.setFormatter(formatter or JSONFormatter())
    return handler
