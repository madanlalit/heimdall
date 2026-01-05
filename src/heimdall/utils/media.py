import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def save_screenshot_async(data: bytes, path: str | Path) -> None:
    """
    Save screenshot data to a file asynchronously to avoid blocking the event loop.

    Args:
        data: Raw image bytes
        path: Destination path
    """
    try:
        path = Path(path)
        # Ensure directory exists
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)

        # Run blocking I/O in a separate thread
        await asyncio.to_thread(_write_file, path, data)
    except Exception as e:
        logger.error(f"Failed to save screenshot to {path}: {e}")


def _write_file(path: Path, data: bytes) -> None:
    """Blocking file write helper."""
    with open(path, "wb") as f:
        f.write(data)
