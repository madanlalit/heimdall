"""
Browser events for CDP communication.

These are lower-level events for browser operations.
"""

from dataclasses import dataclass

from heimdall.events.bus import Event


@dataclass
class PageLoadEvent(Event):
    """Page finished loading."""

    url: str = ""
    target_id: str = ""


@dataclass
class FrameNavigatedEvent(Event):
    """Frame navigated to new URL."""

    frame_id: str = ""
    url: str = ""


@dataclass
class ConsoleMessageEvent(Event):
    """Console message from page."""

    level: str = ""  # log, warning, error, info
    text: str = ""
    url: str = ""
    line: int = 0


@dataclass
class DialogEvent(Event):
    """JavaScript dialog appeared."""

    dialog_type: str = ""  # alert, confirm, prompt, beforeunload
    message: str = ""
    default_prompt: str = ""


@dataclass
class DownloadStartedEvent(Event):
    """File download started."""

    url: str = ""
    filename: str = ""
    guid: str = ""


@dataclass
class DownloadCompletedEvent(Event):
    """File download completed."""

    guid: str = ""
    path: str = ""
