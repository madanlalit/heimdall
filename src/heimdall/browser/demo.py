"""
Demo Mode - Visual feedback during browser automation.

Provides element highlighting and floating tooltips to show
what the agent is doing.
"""

import contextlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession

logger = logging.getLogger(__name__)


class DemoMode:
    """
    Visual feedback overlay for browser automation.

    Shows:
    - Element highlighting before interaction
    - Floating tooltips with action descriptions
    """

    def __init__(self, session: "BrowserSession"):
        self._session = session
        self._enabled = True

    async def highlight_element(
        self,
        backend_node_id: int,
        color: str = "#ff0000",
        duration: float = 0.5,
    ) -> None:
        """
        Highlight an element with a colored border.

        Args:
            backend_node_id: Element to highlight
            color: Border color (CSS color)
            duration: How long to show highlight (seconds)
        """
        if not self._enabled:
            return

        js = f"""
        (function() {{
            // Find element by backend node id (requires prior setup)
            const elements = document.querySelectorAll('*');
            let target = null;
            
            // Try to find by various means
            for (const el of elements) {{
                if (el.__heimdall_backend_id === {backend_node_id}) {{
                    target = el;
                    break;
                }}
            }}
            
            if (!target) {{
                // Fallback: use CDP to get element reference
                return false;
            }}
            
            // Store original styles
            const originalOutline = target.style.outline;
            const originalOutlineOffset = target.style.outlineOffset;
            
            // Apply highlight
            target.style.outline = '3px solid {color}';
            target.style.outlineOffset = '2px';
            
            // Remove after duration
            setTimeout(() => {{
                target.style.outline = originalOutline;
                target.style.outlineOffset = originalOutlineOffset;
            }}, {int(duration * 1000)});
            
            return true;
        }})();
        """

        try:
            await self._session.execute_js(js)
        except Exception as e:
            logger.debug(f"Highlight failed: {e}")

    async def highlight_by_selector(
        self,
        selector: str,
        color: str = "#ff0000",
        duration: float = 0.5,
    ) -> None:
        """Highlight element by CSS selector."""
        if not self._enabled:
            return

        js = f"""
        (function() {{
            const el = document.querySelector('{selector}');
            if (!el) return false;
            
            const orig = el.style.outline;
            const origOffset = el.style.outlineOffset;
            
            el.style.outline = '3px solid {color}';
            el.style.outlineOffset = '2px';
            
            setTimeout(() => {{
                el.style.outline = orig;
                el.style.outlineOffset = origOffset;
            }}, {int(duration * 1000)});
            
            return true;
        }})();
        """

        try:
            await self._session.execute_js(js)
        except Exception as e:
            logger.debug(f"Highlight by selector failed: {e}")

    async def show_tooltip(
        self,
        text: str,
        x: int,
        y: int,
        duration: float = 2.0,
    ) -> None:
        """
        Show a floating tooltip.

        Args:
            text: Tooltip text
            x: X position
            y: Y position
            duration: How long to show (seconds)
        """
        if not self._enabled:
            return

        # Escape text for JS
        text = text.replace("'", "\\'").replace("\n", " ")

        js = f"""
        (function() {{
            // Remove existing tooltip
            const existing = document.getElementById('heimdall-tooltip');
            if (existing) existing.remove();
            
            // Create tooltip
            const tooltip = document.createElement('div');
            tooltip.id = 'heimdall-tooltip';
            tooltip.textContent = '{text}';
            tooltip.style.cssText = `
                position: fixed;
                left: {x}px;
                top: {y}px;
                background: rgba(0, 0, 0, 0.85);
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 14px;
                font-family: -apple-system, system-ui, sans-serif;
                z-index: 999999;
                pointer-events: none;
                box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
                max-width: 300px;
                word-wrap: break-word;
            `;
            
            document.body.appendChild(tooltip);
            
            // Remove after duration
            setTimeout(() => tooltip.remove(), {int(duration * 1000)});
            
            return true;
        }})();
        """

        try:
            await self._session.execute_js(js)
        except Exception as e:
            logger.debug(f"Tooltip failed: {e}")

    async def show_action(
        self,
        action_name: str,
        target_description: str = "",
    ) -> None:
        """
        Show action being performed.

        Args:
            action_name: Name of action (e.g., "click", "type")
            target_description: Description of target element
        """
        text = action_name
        if target_description:
            text += f": {target_description}"

        # Show in top-right corner
        await self.show_tooltip(text, x=20, y=20, duration=1.5)

    async def clear(self) -> None:
        """Remove all demo mode overlays."""
        js = """
        (function() {
            // Remove tooltip
            const tooltip = document.getElementById('heimdall-tooltip');
            if (tooltip) tooltip.remove();
            
            // Remove any remaining highlights by restoring styles
            document.querySelectorAll('[data-heimdall-highlighted]').forEach(el => {
                el.style.outline = '';
                el.style.outlineOffset = '';
                el.removeAttribute('data-heimdall-highlighted');
            });
        })();
        """

        with contextlib.suppress(Exception):
            await self._session.execute_js(js)

    def enable(self) -> None:
        """Enable demo mode."""
        self._enabled = True

    def disable(self) -> None:
        """Disable demo mode."""
        self._enabled = False

    @property
    def is_enabled(self) -> bool:
        """Check if demo mode is enabled."""
        return self._enabled
