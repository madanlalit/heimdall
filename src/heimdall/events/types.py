"""
Event Types - Domain events for Heimdall browser automation.

These events enable loose coupling between components.
"""

from dataclasses import dataclass, field

from heimdall.events.bus import Event


@dataclass
class BrowserStartedEvent(Event):
    """Browser session started and connected."""

    cdp_url: str = ""
    target_id: str = ""


@dataclass
class BrowserStoppedEvent(Event):
    """Browser session stopped."""

    pass


# ===== Navigation Events =====


@dataclass
class NavigationStartedEvent(Event):
    """Navigation started to URL."""

    url: str = ""
    target_id: str = ""


@dataclass
class NavigationCompletedEvent(Event):
    """Navigation completed."""

    url: str = ""
    target_id: str = ""
    success: bool = True
    error: str | None = None


# ===== DOM Events =====


@dataclass
class DOMContentLoadedEvent(Event):
    """DOM content loaded."""

    target_id: str = ""


@dataclass
class DOMChangedEvent(Event):
    """DOM structure changed (mutation detected)."""

    target_id: str = ""
    added_nodes: int = 0
    removed_nodes: int = 0


# ===== Network Events =====


@dataclass
class NetworkRequestStartedEvent(Event):
    """Network request started."""

    request_id: str = ""
    url: str = ""
    method: str = "GET"


@dataclass
class NetworkRequestCompletedEvent(Event):
    """Network request completed."""

    request_id: str = ""
    url: str = ""
    status: int = 0
    mime_type: str = ""


@dataclass
class NetworkIdleEvent(Event):
    """No pending network requests."""

    target_id: str = ""


# ===== Action Events =====


@dataclass
class ActionStartedEvent(Event):
    """Browser action started."""

    action: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class ActionCompletedEvent(Event):
    """Browser action completed."""

    action: str = ""
    success: bool = True
    error: str | None = None
    duration_ms: float = 0


# ===== Element Events =====


@dataclass
class ElementClickedEvent(Event):
    """Element was clicked."""

    backend_node_id: int = 0
    x: int = 0
    y: int = 0


@dataclass
class ElementTypedEvent(Event):
    """Text was typed into element."""

    backend_node_id: int = 0
    text: str = ""


@dataclass
class ElementHighlightedEvent(Event):
    """Element was highlighted (demo mode)."""

    backend_node_id: int = 0
    color: str = "red"


# ===== Error Events =====


@dataclass
class ErrorEvent(Event):
    """Error occurred."""

    error_type: str = ""
    message: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class CrashEvent(Event):
    """Browser crashed or became unresponsive."""

    reason: str = ""


# ===== Agent Events =====


@dataclass
class StepStartedEvent(Event):
    """Agent step started."""

    step_number: int = 0
    instruction: str = ""


@dataclass
class StepCompletedEvent(Event):
    """Agent step completed."""

    step_number: int = 0
    success: bool = True
    actions_count: int = 0
