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

    async def highlight_element_cdp(
        self,
        backend_node_id: int,
        duration: float = 1.0,
    ) -> None:
        """
        Highlight element using CDP for accurate visual feedback on the actual element.

        Uses CDP DOM.getBoxModel to get precise element coordinates, then creates
        a visually striking overlay directly on the element.

        Args:
            backend_node_id: CDP backend node ID of the element
            duration: How long to show highlight (seconds)
        """
        if not self._enabled:
            return

        try:
            # Get element's box model from CDP
            box_result = await self._session.cdp_client.send.DOM.getBoxModel(
                {"backendNodeId": backend_node_id},
                session_id=self._session.session_id,
            )

            box_model = box_result.get("model", {})
            border = box_model.get("border", [])

            if len(border) < 8:
                logger.debug("Could not get element border box")
                return

            # Border quad: [x1, y1, x2, y2, x3, y3, x4, y4] (clockwise from top-left)
            # Use min/max to handle CSS transforms (rotation, skew, etc.)
            xs = border[0::2]  # x coordinates at indices 0, 2, 4, 6
            ys = border[1::2]  # y coordinates at indices 1, 3, 5, 7
            x = min(xs)
            y = min(ys)
            width = max(xs) - x
            height = max(ys) - y

            # Create overlay at exact element position
            js = f"""
            (function() {{
                const overlayId = 'heimdall-highlight-overlay-' + Date.now();
                
                // Create overlay container - minimal clean style
                const overlay = document.createElement('div');
                overlay.id = overlayId;
                overlay.style.cssText = `
                    position: fixed;
                    left: {x}px;
                    top: {y}px;
                    width: {width}px;
                    height: {height}px;
                    pointer-events: none;
                    z-index: 2147483647;
                    border: 2px solid rgba(59, 130, 246, 0.8);
                    border-radius: 3px;
                    box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2),
                                0 0 12px 2px rgba(59, 130, 246, 0.15);
                    animation: heimdall-fade 0.8s ease-in-out infinite;
                    background: rgba(59, 130, 246, 0.04);
                `;
                
                // Create minimal label
                const label = document.createElement('div');
                label.style.cssText = `
                    position: absolute;
                    top: -22px;
                    left: 0;
                    background: rgba(59, 130, 246, 0.9);
                    color: white;
                    padding: 2px 8px;
                    border-radius: 3px;
                    font-size: 10px;
                    font-weight: 500;
                    font-family: -apple-system, system-ui, sans-serif;
                    white-space: nowrap;
                    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.15);
                `;
                label.textContent = '● Target';
                overlay.appendChild(label);
                
                // Inject subtle animation
                const style = document.createElement('style');
                style.id = overlayId + '-style';
                style.textContent = `
                    @keyframes heimdall-fade {{
                        0%, 100% {{ 
                            opacity: 1;
                            box-shadow: 0 0 0 1px rgba(59, 130, 246, 0.2),
                                        0 0 12px 2px rgba(59, 130, 246, 0.15);
                        }}
                        50% {{ 
                            opacity: 0.85;
                            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.3),
                                        0 0 16px 4px rgba(59, 130, 246, 0.2);
                        }}
                    }}
                `;
                document.head.appendChild(style);
                document.body.appendChild(overlay);
                
                // Scroll element into view
                const element = document.elementFromPoint({x + width / 2}, {y + height / 2});
                if (element) {{
                    element.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                }}
                
                // Cleanup after duration
                setTimeout(() => {{
                    const el = document.getElementById(overlayId);
                    if (el) el.remove();
                    const st = document.getElementById(overlayId + '-style');
                    if (st) st.remove();
                }}, {int(duration * 1000)});
                
                return true;
            }})();
            """

            await self._session.execute_js(js)

        except Exception as e:
            logger.debug(f"CDP highlight failed: {e}")

    async def highlight_by_index(
        self,
        index: int,
        color: str = "#ff4444",
        duration: float = 1.0,
    ) -> None:
        """
        Highlight element by its DOM index with animated visual feedback.

        Args:
            index: Element index from DOM state
            color: Primary color for highlight (CSS color)
            duration: How long to show highlight (seconds)
        """
        if not self._enabled:
            return

        js = f"""
        (function() {{
            // Find element by heimdall index attribute
            let target = document.querySelector('[data-heimdall-index="{index}"]');
            
            if (!target) {{
                // Fallback: try to find by visible index markers
                const marker = document.evaluate(
                    "//*[contains(text(), '[{index}]')]",
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue;
                if (marker) target = marker;
            }}
            
            if (!target) return false;
            
            // Create unique animation name
            const animId = 'heimdall-pulse-' + Date.now();
            
            // Inject pulse animation CSS
            const style = document.createElement('style');
            style.id = animId + '-style';
            style.textContent = `
                @keyframes ${{animId}} {{
                    0% {{ 
                        box-shadow: 0 0 0 0 {color}88, 0 0 20px 5px {color}66;
                        transform: scale(1);
                    }}
                    50% {{ 
                        box-shadow: 0 0 0 8px {color}44, 0 0 30px 10px {color}44;
                        transform: scale(1.02);
                    }}
                    100% {{ 
                        box-shadow: 0 0 0 0 {color}88, 0 0 20px 5px {color}66;
                        transform: scale(1);
                    }}
                }}
            `;
            document.head.appendChild(style);
            
            // Store original styles
            const origStyles = {{
                outline: target.style.outline,
                outlineOffset: target.style.outlineOffset,
                boxShadow: target.style.boxShadow,
                zIndex: target.style.zIndex,
                position: target.style.position,
                animation: target.style.animation,
                transform: target.style.transform,
                transition: target.style.transition,
            }};
            
            // Apply highlight with pulsing glow
            Object.assign(target.style, {{
                outline: '3px solid {color}',
                outlineOffset: '4px',
                boxShadow: '0 0 0 0 {color}88, 0 0 25px 8px {color}66',
                zIndex: '999999',
                animation: animId + ' 0.6s ease-in-out infinite',
                transition: 'all 0.2s ease',
            }});
            
            if (getComputedStyle(target).position === 'static') {{
                target.style.position = 'relative';
            }}
            
            // Create floating label
            const label = document.createElement('div');
            label.id = animId + '-label';
            label.textContent = '→ Target [{index}]';
            Object.assign(label.style, {{
                position: 'absolute',
                top: '-30px',
                left: '50%',
                transform: 'translateX(-50%)',
                background: '{color}',
                color: 'white',
                padding: '4px 12px',
                borderRadius: '4px',
                fontSize: '12px',
                fontWeight: 'bold',
                fontFamily: '-apple-system, system-ui, sans-serif',
                whiteSpace: 'nowrap',
                zIndex: '9999999',
                pointerEvents: 'none',
                boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
            }});
            
            // Position label relative to target
            const rect = target.getBoundingClientRect();
            
            // Calculate fixed position to avoid layout issues/void elements
            let top = rect.top - 35;
            if (rect.top < 40) {{
                top = rect.bottom + 10;
            }}
            
            Object.assign(label.style, {{
                position: 'fixed',
                top: top + 'px',
                left: (rect.left + rect.width / 2) + 'px',
                transform: 'translateX(-50%)',
                zIndex: '2147483647'
            }});
            
            document.body.appendChild(label);
            
            // Scroll into view smoothly
            target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
            
            // Cleanup after duration
            setTimeout(() => {{
                // Restore original styles
                Object.assign(target.style, origStyles);
                
                // Remove label and animation style
                const labelEl = document.getElementById(animId + '-label');
                if (labelEl) labelEl.remove();
                
                const styleEl = document.getElementById(animId + '-style');
                if (styleEl) styleEl.remove();
            }}, {int(duration * 1000)});
            
            return true;
        }})();
        """

        try:
            await self._session.execute_js(js)
        except Exception as e:
            logger.debug(f"Highlight by index failed: {e}")

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
