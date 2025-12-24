"""
Error Watchdog - Monitors for crashes and errors.

Detects page crashes, unresponsiveness, and JavaScript errors.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from heimdall.watchdogs.base import BaseWatchdog

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.events.bus import EventBus

logger = logging.getLogger(__name__)


class ErrorWatchdog(BaseWatchdog):
    """
    Monitors for browser errors and crashes.

    Detects:
    - Page crashes/unresponsiveness
    - JavaScript errors
    - CDP connection issues
    """

    def __init__(
        self,
        session: "BrowserSession",
        event_bus: "EventBus",
        poll_interval: float = 1.0,
        unresponsive_threshold: float = 5.0,
    ):
        super().__init__(session, event_bus, poll_interval)
        self._unresponsive_threshold = unresponsive_threshold
        self._last_response_time = 0.0
        self._consecutive_failures = 0
        self._js_errors: list[dict] = []
        self._registered = False

    async def _initialize(self) -> None:
        """Register CDP event handlers for error tracking."""
        self._last_response_time = asyncio.get_event_loop().time()

        if self._registered:
            return

        try:
            client = self._session.cdp_client
            session_id = self._session.session_id

            # Register console error handler
            await client.register.Runtime.exceptionThrown(
                self._on_exception,
                session_id=session_id,
            )

            # Register console message handler for errors
            await client.register.Runtime.consoleAPICalled(
                self._on_console,
                session_id=session_id,
            )

            self._registered = True
            logger.debug("ErrorWatchdog registered CDP handlers")

        except Exception as e:
            logger.warning(f"Could not register error handlers: {e}")

    async def _on_exception(self, params: dict) -> None:
        """Handle JavaScript exception."""
        exception = params.get("exceptionDetails", {})
        text = exception.get("text", "Unknown error")
        url = exception.get("url", "")
        line = exception.get("lineNumber", 0)

        error_info = {
            "type": "exception",
            "message": text,
            "url": url,
            "line": line,
        }
        self._js_errors.append(error_info)

        from heimdall.events.types import ErrorEvent

        await self._bus.emit(
            ErrorEvent(
                error_type="javascript",
                message=text,
                details=error_info,
            )
        )

        logger.warning(f"JS Exception: {text} at {url}:{line}")

    async def _on_console(self, params: dict) -> None:
        """Handle console messages (looking for errors)."""
        level = params.get("type", "")

        if level == "error":
            args = params.get("args", [])
            message = " ".join(str(arg.get("value", arg.get("description", ""))) for arg in args)

            error_info = {
                "type": "console_error",
                "message": message,
            }
            self._js_errors.append(error_info)

            logger.warning(f"Console error: {message[:100]}")

    async def _check(self) -> None:
        """Check for page responsiveness."""
        try:
            # Simple liveness check
            await asyncio.wait_for(
                self._session.execute_js("1"),
                timeout=self._unresponsive_threshold,
            )

            self._last_response_time = asyncio.get_event_loop().time()
            self._consecutive_failures = 0

        except TimeoutError:
            self._consecutive_failures += 1

            if self._consecutive_failures >= 3:
                from heimdall.events.types import CrashEvent

                await self._bus.emit(
                    CrashEvent(
                        reason="Page unresponsive",
                    )
                )
                logger.error("Page appears to be crashed/unresponsive")

        except Exception as e:
            self._consecutive_failures += 1
            logger.debug(f"Liveness check failed: {e}")

    @property
    def is_healthy(self) -> bool:
        """Check if page appears healthy."""
        return self._consecutive_failures < 3

    @property
    def js_errors(self) -> list[dict]:
        """Get list of captured JavaScript errors."""
        return list(self._js_errors)

    def clear_errors(self) -> None:
        """Clear captured errors."""
        self._js_errors.clear()
