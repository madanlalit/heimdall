"""
Export - JSON output generation for collected context.

Generates language-agnostic JSON suitable for script generation.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class TestResult(BaseModel):
    """Complete test result for export."""

    name: str = ""
    status: str = "unknown"  # passed, failed, error
    error: str | None = None

    steps: list[dict] = Field(default_factory=list)

    start_time: str = ""
    end_time: str = ""
    duration_ms: float = 0

    metadata: dict = Field(default_factory=dict)


class Exporter:
    """
    Exports collected context to JSON format.

    Generates language-agnostic output that can be used to
    generate automation scripts in any framework.
    """

    def __init__(self, output_dir: Path | str):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def export_result(
        self,
        result: TestResult,
        filename: str = "result.json",
    ) -> Path:
        """Export test result to JSON file."""
        path = self._output_dir / filename

        data = result.model_dump()
        path.write_text(json.dumps(data, indent=2))

        logger.info(f"Exported result to {path}")
        return path

    def export_steps(
        self,
        steps: list[dict],
        filename: str = "steps.json",
    ) -> Path:
        """Export steps to JSON file."""
        path = self._output_dir / filename

        data = {
            "steps": steps,
            "count": len(steps),
            "exported_at": datetime.now().isoformat(),
        }

        path.write_text(json.dumps(data, indent=2))

        logger.info(f"Exported {len(steps)} steps to {path}")
        return path

    def export_selectors(
        self,
        steps: list[dict],
        filename: str = "selectors.json",
    ) -> Path:
        """
        Export just the selectors for each action.

        Useful for generating minimal test scripts.
        """
        selectors = []

        for step in steps:
            for action in step.get("actions", []):
                element = action.get("element")
                if element and element.get("selectors"):
                    selectors.append(
                        {
                            "step": step.get("step_number"),
                            "action": action.get("action"),
                            "selectors": element.get("selectors"),
                            "tag": element.get("tag"),
                        }
                    )

        path = self._output_dir / filename
        path.write_text(json.dumps(selectors, indent=2))

        logger.info(f"Exported {len(selectors)} selectors to {path}")
        return path

    def export_screenshots(
        self,
        steps: list[dict],
        subdir: str = "screenshots",
    ) -> list[Path]:
        """Export screenshots to files."""
        import base64

        screenshot_dir = self._output_dir / subdir
        screenshot_dir.mkdir(exist_ok=True)

        paths = []

        for step in steps:
            step_num = step.get("step_number", 0)

            for name in ["screenshot_before", "screenshot_after"]:
                data = step.get(name, "")
                if data:
                    suffix = "before" if "before" in name else "after"
                    path = screenshot_dir / f"step_{step_num}_{suffix}.png"

                    try:
                        img_data = base64.b64decode(data)
                        path.write_bytes(img_data)
                        paths.append(path)
                    except Exception as e:
                        logger.debug(f"Could not save screenshot: {e}")

        logger.info(f"Exported {len(paths)} screenshots")
        return paths
