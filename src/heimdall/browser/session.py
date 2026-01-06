"""
Browser Session - CDPClient wrapper for Heimdall.

Provides a simplified interface to Chrome DevTools Protocol via cdp-use.
Manages connection lifecycle, domain enablement, and core browser operations.
"""

import asyncio
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr

logger = logging.getLogger(__name__)


@dataclass
class TabInfo:
    """Information about a browser tab."""

    target_id: str
    url: str = "about:blank"
    title: str = ""
    session_id: str | None = None
    is_active: bool = False


class BrowserConfig(BaseModel):
    """Configuration for browser session."""

    headless: bool = True
    executable_path: str | Path | None = None
    user_data_dir: str | Path | None = None
    profile_directory: str = "Default"
    window_size: tuple[int, int] = (1280, 800)
    args: list[str] = Field(default_factory=list)
    disable_extensions: bool = True

    navigation_timeout: float = 30.0
    action_timeout: float = 10.0

    _original_user_data_dir: str | None = None
    _is_temp_profile: bool = False

    model_config = {"arbitrary_types_allowed": True}

    def model_post_init(self, __context: Any) -> None:
        """Copy Chrome profile to temp directory if using existing profile."""
        self._copy_profile()

    def _copy_profile(self) -> None:
        """
        Copy profile to temp directory if user_data_dir is an existing Chrome profile.

        This avoids SingletonLock conflicts that prevent Chrome from starting
        when another instance is using the same profile.
        """
        if self.user_data_dir is None:
            return

        user_data_str = str(self.user_data_dir)

        # Skip if already using a temp directory
        if "heimdall_chrome_" in user_data_str.lower():
            self._is_temp_profile = True
            return

        is_chrome = "chrome" in user_data_str.lower() or "chromium" in user_data_str.lower()
        if not is_chrome:
            return

        self._original_user_data_dir = user_data_str

        temp_dir = tempfile.mkdtemp(prefix="heimdall_chrome_")
        path_original_user_data = Path(self.user_data_dir)
        path_original_profile = path_original_user_data / self.profile_directory
        path_temp_profile = Path(temp_dir) / self.profile_directory

        if path_original_profile.exists():
            shutil.copytree(path_original_profile, path_temp_profile)

            # Copy Local State file (contains encryption keys for cookies)
            local_state_src = path_original_user_data / "Local State"
            local_state_dst = Path(temp_dir) / "Local State"
            if local_state_src.exists():
                shutil.copy(local_state_src, local_state_dst)

            logger.info(f"Copied profile '{self.profile_directory}' to temp directory: {temp_dir}")
        else:
            Path(temp_dir).mkdir(parents=True, exist_ok=True)
            path_temp_profile.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created new profile in temp directory: {temp_dir}")

        self.user_data_dir = temp_dir
        self._is_temp_profile = True


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

    # Tab tracking
    _tabs: dict[str, TabInfo] = PrivateAttr(default_factory=dict)

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

        # Initialize tab tracking
        self._tabs = {
            self._target_id: TabInfo(
                target_id=self._target_id,
                url="about:blank",
                title="",
                session_id=self._session_id,
                is_active=True,
            )
        }

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

        port = self._find_free_port()

        chrome_args = [
            str(self.config.executable_path)
            if self.config.executable_path
            else self._find_chrome_executable(),
            f"--remote-debugging-port={port}",
            f"--window-size={self.config.window_size[0]},{self.config.window_size[1]}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-popup-blocking",
            "--disable-translate",
            "--disable-sync",
        ]

        if self.config.disable_extensions:
            chrome_args.append("--disable-extensions")

        if self.config.headless:
            chrome_args.append("--headless=new")

        if self.config.user_data_dir:
            chrome_args.append(f"--user-data-dir={self.config.user_data_dir}")
            if self.config.profile_directory and self.config.profile_directory != "Default":
                chrome_args.append(f"--profile-directory={self.config.profile_directory}")

        chrome_args.extend(self.config.args)
        chrome_args.append("about:blank")

        logger.debug(f"Launching Chrome: {' '.join(chrome_args[:5])}...")

        self._chrome_process = subprocess.Popen(
            chrome_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

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

    async def wait_for_stable(
        self,
        network_idle_ms: int = 500,
        dom_idle_ms: int = 300,
        timeout: float = 10.0,
    ) -> None:
        """
        Wait for page to become stable.

        Waits for:
        1. No pending network requests for network_idle_ms
        2. No DOM mutations for dom_idle_ms

        Args:
            network_idle_ms: Time with no network activity to consider stable
            dom_idle_ms: Time with no DOM mutations to consider stable
            timeout: Max time to wait
        """
        # Inject stability detection script
        script = """
        (function() {
            if (window.__heimdallStabilityObserver) return;
            
            let lastNetworkActivity = Date.now();
            let lastDomMutation = Date.now();
            let pendingRequests = 0;
            
            // Track network
            const origFetch = window.fetch;
            window.fetch = function(...args) {
                pendingRequests++;
                lastNetworkActivity = Date.now();
                return origFetch.apply(this, args).finally(() => {
                    pendingRequests--;
                    lastNetworkActivity = Date.now();
                });
            };
            
            const origXHR = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.send = function(...args) {
                pendingRequests++;
                lastNetworkActivity = Date.now();
                this.addEventListener('loadend', () => {
                    pendingRequests--;
                    lastNetworkActivity = Date.now();
                });
                return origXHR.apply(this, args);
            };
            
            // Track DOM
            const observer = new MutationObserver(() => {
                lastDomMutation = Date.now();
            });
            observer.observe(document.body || document.documentElement, {
                childList: true, subtree: true, attributes: true
            });
            
            window.__heimdallStabilityObserver = {
                isStable: function(networkIdleMs, domIdleMs) {
                    const now = Date.now();
                    const networkIdle = pendingRequests === 0 && 
                        (now - lastNetworkActivity) >= networkIdleMs;
                    const domIdle = (now - lastDomMutation) >= domIdleMs;
                    return networkIdle && domIdle;
                }
            };
        })();
        """
        try:
            await self.execute_js(script)
        except Exception as e:
            logger.debug(f"Could not inject stability script: {e}")
            await asyncio.sleep(0.5)
            return

        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            try:
                js_expr = (
                    f"window.__heimdallStabilityObserver?.isStable"
                    f"({network_idle_ms}, {dom_idle_ms}) ?? true"
                )
                is_stable = await self.execute_js(js_expr)
                if is_stable:
                    logger.debug("Page stable")
                    return
            except Exception:
                pass
            await asyncio.sleep(0.1)

        logger.debug(f"Page stability timeout after {timeout}s")

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
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
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

    # ===== Tab Management =====

    def get_tabs(self) -> list[TabInfo]:
        """Get all open tabs."""
        return list(self._tabs.values())

    def get_current_tab(self) -> TabInfo | None:
        """Get the currently active tab."""
        if self._target_id and self._target_id in self._tabs:
            return self._tabs[self._target_id]
        return None

    async def create_tab(self, url: str = "about:blank") -> TabInfo:
        """
        Create a new browser tab.

        Args:
            url: URL to open in the new tab

        Returns:
            TabInfo for the new tab
        """
        # Create new target (tab)
        result = await self._cdp_client.send.Target.createTarget({"url": url})
        new_target_id = result["targetId"]

        # Attach to the new target
        attach_result = await self._cdp_client.send.Target.attachToTarget(
            {"targetId": new_target_id, "flatten": True}
        )
        new_session_id = attach_result.get("sessionId")

        # Enable domains on the new session
        await self._enable_session_domains_for(new_session_id)

        # Create tab info
        tab_info = TabInfo(
            target_id=new_target_id,
            url=url,
            title="",
            session_id=new_session_id,
            is_active=False,
        )
        self._tabs[new_target_id] = tab_info

        logger.info(f"Created new tab: {new_target_id[:8]}... -> {url}")
        return tab_info

    async def switch_tab(self, target_id: str) -> None:
        """
        Switch to a different tab.

        Args:
            target_id: Target ID of the tab to switch to
        """
        if target_id not in self._tabs:
            raise ValueError(f"Tab not found: {target_id}")

        if target_id == self._target_id:
            logger.debug("Already on this tab")
            return

        # Activate the target (brings it to front visually)
        await self._cdp_client.send.Target.activateTarget({"targetId": target_id})

        # Update active state
        for tab in self._tabs.values():
            tab.is_active = False
        self._tabs[target_id].is_active = True

        # Get or create session for the target
        tab_info = self._tabs[target_id]
        if not tab_info.session_id:
            attach_result = await self._cdp_client.send.Target.attachToTarget(
                {"targetId": target_id, "flatten": True}
            )
            tab_info.session_id = attach_result.get("sessionId")
            await self._enable_session_domains_for(tab_info.session_id)

        # Update current target and session
        self._target_id = target_id
        self._session_id = tab_info.session_id

        logger.info(f"Switched to tab: {target_id[:8]}...")

    async def close_tab(self, target_id: str) -> None:
        """
        Close a browser tab.

        Args:
            target_id: Target ID of the tab to close
        """
        if target_id not in self._tabs:
            raise ValueError(f"Tab not found: {target_id}")

        if len(self._tabs) <= 1:
            raise RuntimeError("Cannot close the last tab")

        was_active = target_id == self._target_id

        # Close the target
        await self._cdp_client.send.Target.closeTarget({"targetId": target_id})

        # Remove from tracking
        del self._tabs[target_id]

        logger.info(f"Closed tab: {target_id[:8]}...")

        # If we closed the active tab, switch to another one
        if was_active and self._tabs:
            next_tab_id = next(iter(self._tabs.keys()))
            await self.switch_tab(next_tab_id)

    async def _enable_session_domains_for(self, session_id: str | None) -> None:
        """Enable CDP domains on a specific session."""
        if not session_id:
            return

        await asyncio.gather(
            self._cdp_client.send.Page.enable(session_id=session_id),
            self._cdp_client.send.DOM.enable(session_id=session_id),
            self._cdp_client.send.Network.enable(session_id=session_id),
            self._cdp_client.send.Runtime.enable(session_id=session_id),
            self._cdp_client.send.Accessibility.enable(session_id=session_id),
            self._cdp_client.send.DOMSnapshot.enable(session_id=session_id),
        )

    async def refresh_tabs(self) -> list[TabInfo]:
        """
        Refresh tab information from browser.

        Returns:
            Updated list of tabs
        """
        targets = await self._cdp_client.send.Target.getTargets()

        # Update existing tabs and add new ones
        seen_ids: set[str] = set()
        for target in targets.get("targetInfos", []):
            if target.get("type") == "page":
                target_id = target["targetId"]
                seen_ids.add(target_id)

                if target_id in self._tabs:
                    # Update existing tab info
                    self._tabs[target_id].url = target.get("url", "")
                    self._tabs[target_id].title = target.get("title", "")
                else:
                    # New tab discovered
                    self._tabs[target_id] = TabInfo(
                        target_id=target_id,
                        url=target.get("url", ""),
                        title=target.get("title", ""),
                        is_active=(target_id == self._target_id),
                    )

        # Remove tabs that no longer exist
        for target_id in list(self._tabs.keys()):
            if target_id not in seen_ids:
                del self._tabs[target_id]

        return self.get_tabs()

    # ===== Context Manager Support =====

    async def __aenter__(self) -> "BrowserSession":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
