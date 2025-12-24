"""
Browser Session - CDPClient wrapper for Heimdall.

Provides a simplified interface to Chrome DevTools Protocol via cdp-use.
Manages connection lifecycle, domain enablement, and core browser operations.
"""

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

logger = logging.getLogger(__name__)


class BrowserConfig(BaseModel):
    """Configuration for browser session."""

    headless: bool = True
    executable_path: str | Path | None = None
    user_data_dir: str | Path | None = None
    window_size: tuple[int, int] = (1280, 800)
    args: list[str] = Field(default_factory=list)

    # Timeouts
    navigation_timeout: float = 30.0
    action_timeout: float = 10.0


class BrowserSession(BaseModel):
    """
    Browser session wrapping cdp-use CDPClient.

    Usage:
        session = BrowserSession(config=BrowserConfig(headless=False))
        await session.start()
        await session.navigate("https://example.com")
        # ... do work ...
        await session.stop()
    """

    model_config = {"arbitrary_types_allowed": True}

    config: BrowserConfig = Field(default_factory=BrowserConfig)

    # Private state
    _cdp_client: Any = PrivateAttr(default=None)
    _chrome_process: Any = PrivateAttr(default=None)
    _ws_url: str | None = PrivateAttr(default=None)
    _session_id: str | None = PrivateAttr(default=None)
    _target_id: str | None = PrivateAttr(default=None)
    _connected: bool = PrivateAttr(default=False)

    @property
    def is_connected(self) -> bool:
        """Check if session is connected to browser."""
        return self._connected and self._cdp_client is not None

    @property
    def cdp_client(self) -> Any:
        """Get the CDP client. Raises if not connected."""
        if not self._cdp_client:
            raise RuntimeError("Browser session not started. Call start() first.")
        return self._cdp_client

    @property
    def session_id(self) -> str:
        """Get current session ID."""
        if not self._session_id:
            raise RuntimeError("No active session.")
        return self._session_id

    @property
    def target_id(self) -> str:
        """Get current target ID."""
        if not self._target_id:
            raise RuntimeError("No active target.")
        return self._target_id

    async def start(self, cdp_url: str | None = None) -> None:
        """
        Start browser session.

        Args:
            cdp_url: Optional CDP WebSocket URL. If not provided, launches Chrome.
        """
        from cdp_use import CDPClient

        if self._connected:
            logger.warning("Session already started, skipping.")
            return

        # Get or launch browser
        if cdp_url:
            self._ws_url = cdp_url
        else:
            self._ws_url = await self._launch_chrome()

        logger.info(f"Connecting to Chrome at {self._ws_url}")

        # Create CDP client
        self._cdp_client = CDPClient(self._ws_url)
        await self._cdp_client.start()

        # Enable required CDP domains
        await self._enable_domains()

        # Get initial target (first page)
        targets = await self._cdp_client.send.Target.getTargets()
        for target in targets.get("targetInfos", []):
            if target.get("type") == "page":
                self._target_id = target["targetId"]
                break

        if not self._target_id:
            # Create new page if none exists
            result = await self._cdp_client.send.Target.createTarget({"url": "about:blank"})
            self._target_id = result["targetId"]

        # Attach to target
        result = await self._cdp_client.send.Target.attachToTarget(
            {"targetId": self._target_id, "flatten": True}
        )
        self._session_id = result.get("sessionId")

        # Enable domains on this session
        await self._enable_session_domains()

        self._connected = True
        logger.info(f"Browser session started (target: {self._target_id[:8]}...)")

    async def stop(self) -> None:
        """Stop browser session and cleanup."""
        if not self._connected:
            return

        try:
            if self._cdp_client:
                await self._cdp_client.stop()
        except Exception as e:
            logger.warning(f"Error stopping CDP client: {e}")

        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"Error terminating Chrome: {e}")
                self._chrome_process.kill()

        self._cdp_client = None
        self._chrome_process = None
        self._ws_url = None
        self._session_id = None
        self._target_id = None
        self._connected = False

        logger.info("Browser session stopped")

    async def navigate(self, url: str, wait_until: str = "load") -> None:
        """
        Navigate to URL and wait for page load.

        Args:
            url: URL to navigate to
            wait_until: Wait condition - "load" or "domcontentloaded"
        """
        logger.debug(f"Navigating to {url}")

        # Navigate
        await self._cdp_client.send.Page.navigate({"url": url}, session_id=self._session_id)

        # Wait for load
        await self._wait_for_load(wait_until)

        logger.debug(f"Navigation complete: {url}")

    async def screenshot(self, full_page: bool = False) -> bytes:
        """
        Capture screenshot of current page.

        Args:
            full_page: If True, capture full scrollable page

        Returns:
            PNG image data as bytes
        """
        import base64

        params: dict[str, Any] = {"format": "png"}

        if full_page:
            # Get full page dimensions
            metrics = await self._cdp_client.send.Page.getLayoutMetrics(session_id=self._session_id)
            content_size = metrics.get("contentSize", {})
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": content_size.get("width", 1280),
                "height": content_size.get("height", 800),
                "scale": 1,
            }
            params["captureBeyondViewport"] = True

        result = await self._cdp_client.send.Page.captureScreenshot(
            params, session_id=self._session_id
        )

        return base64.b64decode(result["data"])

    async def execute_js(self, expression: str) -> Any:
        """
        Execute JavaScript in page context.

        Args:
            expression: JavaScript code to execute

        Returns:
            Result of JS evaluation
        """
        result = await self._cdp_client.send.Runtime.evaluate(
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=self._session_id,
        )

        if "exceptionDetails" in result:
            raise RuntimeError(f"JS error: {result['exceptionDetails']}")

        return result.get("result", {}).get("value")

    async def get_url(self) -> str:
        """Get current page URL."""
        return await self.execute_js("window.location.href")

    async def get_title(self) -> str:
        """Get current page title."""
        return await self.execute_js("document.title")

    async def _launch_chrome(self) -> str:
        """Launch Chrome with CDP enabled and return WebSocket URL."""

        # Find free port
        port = self._find_free_port()

        # Build Chrome arguments
        chrome_args = [
            str(self.config.executable_path)
            if self.config.executable_path
            else self._find_chrome_executable(),
            f"--remote-debugging-port={port}",
            f"--window-size={self.config.window_size[0]},{self.config.window_size[1]}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--disable-popup-blocking",
            "--disable-translate",
            "--disable-sync",
        ]

        if self.config.headless:
            chrome_args.append("--headless=new")

        if self.config.user_data_dir:
            chrome_args.append(f"--user-data-dir={self.config.user_data_dir}")

        chrome_args.extend(self.config.args)
        chrome_args.append("about:blank")

        logger.debug(f"Launching Chrome: {' '.join(chrome_args[:5])}...")

        self._chrome_process = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for CDP to be ready
        ws_url = await self._wait_for_cdp(port)
        return ws_url

    async def _wait_for_cdp(self, port: int, timeout: float = 30.0) -> str:
        """Wait for Chrome CDP to become available."""
        import httpx

        start = asyncio.get_event_loop().time()
        url = f"http://localhost:{port}/json/version"

        async with httpx.AsyncClient() as client:
            while asyncio.get_event_loop().time() - start < timeout:
                try:
                    response = await client.get(url, timeout=1.0)
                    if response.status_code == 200:
                        data = response.json()
                        return data["webSocketDebuggerUrl"]
                except Exception:
                    await asyncio.sleep(0.1)

        raise TimeoutError(f"Chrome CDP not available after {timeout}s")

    async def _enable_domains(self) -> None:
        """Enable CDP domains on root client."""
        await self._cdp_client.send.Target.setDiscoverTargets({"discover": True})

    async def _enable_session_domains(self) -> None:
        """Enable CDP domains on the session."""
        session_id = self._session_id

        await asyncio.gather(
            self._cdp_client.send.Page.enable(session_id=session_id),
            self._cdp_client.send.DOM.enable(session_id=session_id),
            self._cdp_client.send.Network.enable(session_id=session_id),
            self._cdp_client.send.Runtime.enable(session_id=session_id),
            self._cdp_client.send.Accessibility.enable(session_id=session_id),
            self._cdp_client.send.DOMSnapshot.enable(session_id=session_id),
        )

    async def _wait_for_load(self, wait_until: str = "load", timeout: float | None = None) -> None:
        """Wait for page to finish loading."""
        timeout = timeout or self.config.navigation_timeout

        # Simple approach: wait for document.readyState
        target_state = "interactive" if wait_until == "domcontentloaded" else "complete"

        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            state = await self.execute_js("document.readyState")
            if state == target_state or state == "complete":
                return
            await asyncio.sleep(0.1)

        logger.warning(f"Page load timeout after {timeout}s")

    def _find_free_port(self) -> int:
        """Find a free port for CDP."""
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            s.listen(1)
            return s.getsockname()[1]

    def _find_chrome_executable(self) -> str:
        """Find Chrome executable path."""
        import platform
        import shutil

        system = platform.system()

        if system == "Darwin":
            paths = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
            ]
        elif system == "Linux":
            paths = [
                "google-chrome",
                "google-chrome-stable",
                "chromium",
                "chromium-browser",
            ]
        elif system == "Windows":
            paths = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]
        else:
            paths = []

        for path in paths:
            if Path(path).exists() or shutil.which(path):
                return path

        raise RuntimeError("Chrome not found. Install Chrome or set executable_path in config.")

    # ===== Context Manager Support =====

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
