"""
Base Watchdog - Abstract base class for browser state monitors.

Watchdogs continuously monitor browser state and emit events
when specific conditions are detected.
"""

import asyncio
import contextlib
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.events.bus import EventBus

logger = logging.getLogger(__name__)


class BaseWatchdog(ABC):
    """
    Base class for browser state monitoring.

    Watchdogs run in the background and emit events when conditions change.
    """

    def __init__(
        self,
        session: "BrowserSession",
        event_bus: "EventBus",
        poll_interval: float = 0.1,
    ):
        self._session = session
        self._bus = event_bus
        self._poll_interval = poll_interval
        self._running = False
        self._task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        """Watchdog name for logging."""
        return self.__class__.__name__

    @property
    def is_running(self) -> bool:
        """Check if watchdog is running."""
        return self._running and self._task is not None

    async def start(self) -> None:
        """Start the watchdog."""
        if self._running:
            logger.warning(f"{self.name} already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.debug(f"{self.name} started")

    async def stop(self) -> None:
        """Stop the watchdog."""
        self._running = False

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        logger.debug(f"{self.name} stopped")

    async def _run_loop(self) -> None:
        """Main monitoring loop."""
        try:
            # Initialize watchdog state
            await self._initialize()

            while self._running:
                try:
                    await self._check()
                except Exception as e:
                    logger.error(f"{self.name} check error: {e}")

                await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            pass
        finally:
            await self._cleanup()

    async def _initialize(self) -> None:
        """Initialize watchdog state. Override in subclasses."""
        pass

    async def _cleanup(self) -> None:
        """Cleanup on stop. Override in subclasses."""
        pass

    @abstractmethod
    async def _check(self) -> None:
        """
        Check for condition changes.

        Called repeatedly while watchdog is running.
        Should emit events when conditions change.
        """
        pass
