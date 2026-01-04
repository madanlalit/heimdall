"""
Network Watchdog - Monitors network request activity.

Tracks pending requests and detects network idle state.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from heimdall.watchdogs.base import BaseWatchdog

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.events.bus import EventBus

logger = logging.getLogger(__name__)


class NetworkWatchdog(BaseWatchdog):
    """
    Monitors network activity.

    Tracks pending XHR/fetch requests and emits NetworkIdleEvent
    when no requests are pending.
    """

    def __init__(
        self,
        session: "BrowserSession",
        event_bus: "EventBus",
        poll_interval: float = 0.1,
        idle_threshold: float = 0.5,
    ):
        super().__init__(session, event_bus, poll_interval)
        self._pending_requests: set[str] = set()
        self._idle_threshold = idle_threshold
        self._last_activity_time = 0.0
        self._was_idle = True
        self._registered = False

        # Track failed requests
        self._failed_requests: list[dict] = []
        self._request_url_map: dict[str, str] = {}  # Map requestId -> URL

    async def _initialize(self) -> None:
        """Register CDP event handlers for network tracking."""
        if self._registered:
            return

        try:
            client = self._session.cdp_client
            session_id = self._session.session_id

            # Register network event handlers
            await client.register.Network.requestWillBeSent(
                self._on_request_started,
                session_id=session_id,
            )
            await client.register.Network.loadingFinished(
                self._on_request_finished,
                session_id=session_id,
            )
            await client.register.Network.loadingFailed(
                self._on_request_failed,
                session_id=session_id,
            )

            self._registered = True
            self._last_activity_time = asyncio.get_event_loop().time()
            logger.debug("NetworkWatchdog registered CDP handlers")

        except Exception as e:
            logger.warning(f"Could not register network handlers: {e}")

    async def _on_request_started(self, params: dict) -> None:
        """Handle request started."""
        request_id = params.get("requestId", "")
        url = params.get("request", {}).get("url", "")

        # Ignore data URLs and extensions
        if url.startswith("data:") or url.startswith("chrome-extension:"):
            return

        self._pending_requests.add(request_id)
        self._request_url_map[request_id] = url
        self._last_activity_time = asyncio.get_event_loop().time()
        self._was_idle = False

        logger.debug(
            f"Request started: {request_id[:8]}... ({len(self._pending_requests)} pending)"
        )

    async def _on_request_finished(self, params: dict) -> None:
        """Handle request finished."""
        request_id = params.get("requestId", "")
        self._pending_requests.discard(request_id)
        # Keep URL in map for a bit or clear it? Better to clear to save memory,
        # but what if failure comes after finish (unlikely)?
        # Failure usually comes INSTEAD of finish.
        self._request_url_map.pop(request_id, None)

        self._last_activity_time = asyncio.get_event_loop().time()

        logger.debug(
            f"Request finished: {request_id[:8]}... ({len(self._pending_requests)} pending)"
        )

    async def _on_request_failed(self, params: dict) -> None:
        """Handle request failed."""
        request_id = params.get("requestId", "")
        error_text = params.get("errorText", "Unknown error")
        timestamp = params.get("timestamp", 0)

        url = self._request_url_map.get(request_id, "unknown")

        # Ignore cancelled requests (often just navigation)
        if error_text == "net::ERR_ABORTED":
            # Still remove from pending
            self._pending_requests.discard(request_id)
            self._request_url_map.pop(request_id, None)
            return

        failure_info = {"url": url, "error": error_text, "timestamp": timestamp}
        self._failed_requests.append(failure_info)

        # Cleanup
        self._pending_requests.discard(request_id)
        self._request_url_map.pop(request_id, None)

        self._last_activity_time = asyncio.get_event_loop().time()

        logger.warning(f"Request failed: {url} - {error_text}")

    async def _check(self) -> None:
        """Check for network idle state."""
        now = asyncio.get_event_loop().time()
        time_since_activity = now - self._last_activity_time

        # Emit idle event when no pending requests and threshold passed
        is_idle = len(self._pending_requests) == 0 and time_since_activity >= self._idle_threshold

        if is_idle and not self._was_idle:
            from heimdall.events.types import NetworkIdleEvent

            await self._bus.emit(
                NetworkIdleEvent(
                    target_id=self._session.target_id,
                )
            )
            logger.debug("Network idle")

        self._was_idle = is_idle

    @property
    def pending_count(self) -> int:
        """Number of pending requests."""
        return len(self._pending_requests)

    @property
    def is_idle(self) -> bool:
        """Check if network is currently idle."""
        now = asyncio.get_event_loop().time()
        time_since_activity = now - self._last_activity_time
        return len(self._pending_requests) == 0 and time_since_activity >= self._idle_threshold

    async def wait_for_idle(self, timeout: float = 30.0) -> bool:
        """
        Wait for network to become idle.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if idle, False if timeout
        """
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            if self.is_idle:
                return True
            await asyncio.sleep(0.1)

        logger.warning(f"Network idle timeout ({self.pending_count} pending)")
        return False

    @property
    def failed_requests(self) -> list[dict]:
        """Get list of failed requests."""
        return list(self._failed_requests)

    def clear_failed_requests(self) -> None:
        """Clear failed requests list."""
        self._failed_requests.clear()
