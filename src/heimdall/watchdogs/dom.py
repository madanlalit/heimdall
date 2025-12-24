"""
DOM Watchdog - Monitors DOM mutation activity.

Detects when DOM structure changes and stabilizes.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from heimdall.watchdogs.base import BaseWatchdog

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.events.bus import EventBus

logger = logging.getLogger(__name__)


class DOMWatchdog(BaseWatchdog):
    """
    Monitors DOM structure changes.

    Uses MutationObserver via JavaScript to detect DOM changes
    and emit events when the DOM stabilizes.
    """

    def __init__(
        self,
        session: "BrowserSession",
        event_bus: "EventBus",
        poll_interval: float = 0.2,
        stability_threshold: float = 0.3,
    ):
        super().__init__(session, event_bus, poll_interval)
        self._stability_threshold = stability_threshold
        self._last_mutation_time = 0.0
        self._mutation_count = 0
        self._was_stable = True
        self._observer_installed = False

    async def _initialize(self) -> None:
        """Install MutationObserver in page."""
        await self._install_observer()

    async def _install_observer(self) -> None:
        """Inject MutationObserver script."""
        if self._observer_installed:
            return

        try:
            js = """
            (() => {
                if (window.__heimdall_dom_observer) return;
                
                window.__heimdall_mutation_count = 0;
                window.__heimdall_last_mutation = Date.now();
                
                window.__heimdall_dom_observer = new MutationObserver((mutations) => {
                    window.__heimdall_mutation_count += mutations.length;
                    window.__heimdall_last_mutation = Date.now();
                });
                
                window.__heimdall_dom_observer.observe(document.body || document.documentElement, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                });
            })();
            """
            await self._session.execute_js(js)
            self._observer_installed = True
            self._last_mutation_time = asyncio.get_event_loop().time()
            logger.debug("DOM observer installed")

        except Exception as e:
            logger.warning(f"Could not install DOM observer: {e}")

    async def _check(self) -> None:
        """Check for DOM stability."""
        try:
            # Get mutation stats from page
            result = await self._session.execute_js("""
                (() => ({
                    count: window.__heimdall_mutation_count || 0,
                    lastMutation: window.__heimdall_last_mutation || 0
                }))()
            """)

            if not result:
                return

            mutation_count = result.get("count", 0)
            # lastMutation available but not currently used

            # Detect new mutations
            if mutation_count > self._mutation_count:
                self._mutation_count = mutation_count
                self._last_mutation_time = asyncio.get_event_loop().time()
                self._was_stable = False

                from heimdall.events.types import DOMChangedEvent

                await self._bus.emit(
                    DOMChangedEvent(
                        target_id=self._session.target_id,
                        added_nodes=mutation_count - self._mutation_count,
                    )
                )

            # Check for stability
            now = asyncio.get_event_loop().time()
            time_since_mutation = now - self._last_mutation_time
            is_stable = time_since_mutation >= self._stability_threshold

            if is_stable and not self._was_stable:
                logger.debug(f"DOM stabilized ({self._mutation_count} mutations)")

            self._was_stable = is_stable

        except Exception as e:
            logger.debug(f"DOM check error: {e}")

    @property
    def is_stable(self) -> bool:
        """Check if DOM is currently stable."""
        now = asyncio.get_event_loop().time()
        return (now - self._last_mutation_time) >= self._stability_threshold

    async def wait_for_stable(self, timeout: float = 10.0) -> bool:
        """
        Wait for DOM to become stable.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if stable, False if timeout
        """
        start = asyncio.get_event_loop().time()

        while asyncio.get_event_loop().time() - start < timeout:
            if self.is_stable:
                return True
            await asyncio.sleep(0.1)

        logger.warning("DOM stability timeout")
        return False

    async def reset_counter(self) -> None:
        """Reset mutation counter (call after intentional DOM changes)."""
        try:
            await self._session.execute_js("window.__heimdall_mutation_count = 0;")
            self._mutation_count = 0
        except Exception:
            pass
