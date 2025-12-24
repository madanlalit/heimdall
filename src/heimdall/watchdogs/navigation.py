"""
Navigation Watchdog - Monitors URL changes and page load state.

Detects navigation events and waits for page stability.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from heimdall.watchdogs.base import BaseWatchdog

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.events.bus import EventBus

logger = logging.getLogger(__name__)


class NavigationWatchdog(BaseWatchdog):
    """
    Monitors page navigation and load state.

    Emits events when:
    - URL changes
    - Page finishes loading
    - Navigation fails
    """

    def __init__(
        self,
        session: "BrowserSession",
        event_bus: "EventBus",
        poll_interval: float = 0.1,
    ):
        super().__init__(session, event_bus, poll_interval)
        self._last_url: str | None = None
        self._last_ready_state: str | None = None
        self._navigating = False

    async def _initialize(self) -> None:
        """Capture initial URL state."""
        try:
            self._last_url = await self._session.get_url()
            self._last_ready_state = await self._get_ready_state()
            logger.debug(f"NavigationWatchdog initialized: {self._last_url}")
        except Exception as e:
            logger.warning(f"Could not get initial URL: {e}")

    async def _check(self) -> None:
        """Check for URL or load state changes."""
        try:
            current_url = await self._session.get_url()
            current_ready_state = await self._get_ready_state()

            # URL changed
            if current_url != self._last_url:
                from heimdall.events.types import NavigationCompletedEvent, NavigationStartedEvent

                old_url = self._last_url
                self._last_url = current_url
                self._navigating = True

                await self._bus.emit(
                    NavigationStartedEvent(
                        url=current_url,
                        target_id=self._session.target_id,
                    )
                )

                logger.debug(f"Navigation detected: {old_url} → {current_url}")

            # Page finished loading after navigation
            if self._navigating and current_ready_state == "complete":
                from heimdall.events.types import NavigationCompletedEvent

                self._navigating = False
                await self._bus.emit(
                    NavigationCompletedEvent(
                        url=current_url,
                        target_id=self._session.target_id,
                        success=True,
                    )
                )

                logger.debug(f"Navigation complete: {current_url}")

            self._last_ready_state = current_ready_state

        except Exception as e:
            logger.debug(f"Navigation check error: {e}")

    async def _get_ready_state(self) -> str:
        """Get document.readyState."""
        try:
            return await self._session.execute_js("document.readyState")
        except Exception:
            return "unknown"

    async def wait_for_navigation(self, timeout: float = 30.0) -> bool:
        """
        Wait for a navigation to complete.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if navigation completed, False if timeout
        """
        start = asyncio.get_event_loop().time()
        initial_url = self._last_url

        while asyncio.get_event_loop().time() - start < timeout:
            # URL changed and page is complete
            if self._last_url != initial_url and self._last_ready_state == "complete":
                return True

            # Already on complete and URL changed
            current_url = await self._session.get_url()
            current_state = await self._get_ready_state()

            if current_url != initial_url and current_state == "complete":
                return True

            await asyncio.sleep(0.1)

        return False

    async def wait_for_load(self, timeout: float = 30.0) -> bool:
        """
        Wait for current page to finish loading.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if loaded, False if timeout
        """
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            state = await self._get_ready_state()
            if state == "complete":
                return True
            await asyncio.sleep(0.1)

        return False
