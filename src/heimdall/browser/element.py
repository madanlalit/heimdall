"""
Element - Browser element operations for Heimdall.

Provides methods to interact with DOM elements via CDP.
Based on browser-use patterns with fallback strategies.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession

logger = logging.getLogger(__name__)

ModifierType = Literal["Alt", "Control", "Meta", "Shift"]


@dataclass
class BoundingBox:
    """Element bounding box."""

    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


async def dispatch_mouse_click(
    session: "BrowserSession",
    x: int,
    y: int,
    button: Literal["left", "right", "middle"] = "left",
    click_count: int = 1,
    modifiers: int = 0,
) -> None:
    """Dispatch a mouse click at viewport CSS coordinates."""
    client = session.cdp_client
    session_id = session.session_id

    await client.send.Input.dispatchMouseEvent(
        {"type": "mouseMoved", "x": x, "y": y},
        session_id=session_id,
    )
    await asyncio.sleep(0.03)

    try:
        await asyncio.wait_for(
            client.send.Input.dispatchMouseEvent(
                {
                    "type": "mousePressed",
                    "x": x,
                    "y": y,
                    "button": button,
                    "clickCount": click_count,
                    "modifiers": modifiers,
                },
                session_id=session_id,
            ),
            timeout=1.0,
        )
        await asyncio.sleep(0.05)
    except TimeoutError:
        logger.debug("mousePressed timed out")

    try:
        await asyncio.wait_for(
            client.send.Input.dispatchMouseEvent(
                {
                    "type": "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": button,
                    "clickCount": click_count,
                    "modifiers": modifiers,
                },
                session_id=session_id,
            ),
            timeout=2.0,
        )
    except TimeoutError:
        logger.debug("mouseReleased timed out")


class Element:
    """
    Browser element operations using BackendNodeId.

    Provides robust methods for clicking, typing, and other interactions
    with fallback strategies for reliability.
    """

    def __init__(
        self,
        session: "BrowserSession",
        backend_node_id: int,
        node_id: int | None = None,
    ):
        self._session = session
        self._backend_node_id = backend_node_id
        self._node_id = node_id

    @property
    def backend_node_id(self) -> int:
        return self._backend_node_id

    async def click(
        self,
        button: Literal["left", "right", "middle"] = "left",
        click_count: int = 1,
        modifiers: list[ModifierType] | None = None,
    ) -> None:
        """
        Click the element using multiple strategies with fallback.

        Strategies: DOM.getContentQuads -> DOM.getBoxModel
        -> JS getBoundingClientRect -> JS .click()

        Includes pre-click verification:
        - Pointer events check
        - Hit target verification
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Check pointer-events first
        pointer_events_ok = await self._check_pointer_events()
        if not pointer_events_ok:
            logger.warning(
                f"Element {self._backend_node_id} has pointer-events: none, "
                "falling back to JS click"
            )
            await self._js_click()
            return

        # Get viewport dimensions for visibility checks
        try:
            layout_metrics = await client.send.Page.getLayoutMetrics(session_id=session_id)
            viewport_width = layout_metrics["layoutViewport"]["clientWidth"]
            viewport_height = layout_metrics["layoutViewport"]["clientHeight"]
        except Exception:
            viewport_width, viewport_height = 1920, 1080  # Fallback defaults

        quads: list[list[float]] = []

        # Method 1: DOM.getContentQuads
        try:
            result = await client.send.DOM.getContentQuads(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            if result.get("quads"):
                quads = result["quads"]
                logger.debug(f"Got {len(quads)} quads via getContentQuads")
        except Exception as e:
            logger.debug(f"getContentQuads failed: {e}")

        # Method 2: DOM.getBoxModel fallback
        if not quads:
            try:
                result = await client.send.DOM.getBoxModel(
                    {"backendNodeId": self._backend_node_id},
                    session_id=session_id,
                )
                model = result.get("model", {})
                content = model.get("content", [])
                if len(content) >= 8:
                    quads = [content]
                    logger.debug("Got geometry via getBoxModel")
            except Exception as e:
                logger.debug(f"getBoxModel failed: {e}")

        # Method 3: JS getBoundingClientRect fallback
        if not quads:
            try:
                result = await client.send.DOM.resolveNode(
                    {"backendNodeId": self._backend_node_id},
                    session_id=session_id,
                )
                object_id = result.get("object", {}).get("objectId")
                if object_id:
                    bounds_result = await client.send.Runtime.callFunctionOn(
                        {
                            "objectId": object_id,
                            "functionDeclaration": """
                                function() {
                                    const rect = this.getBoundingClientRect();
                                    return {x: rect.left, y: rect.top, 
                                            width: rect.width, height: rect.height};
                                }
                            """,
                            "returnByValue": True,
                        },
                        session_id=session_id,
                    )
                    rect = bounds_result.get("result", {}).get("value", {})
                    if rect.get("width") and rect.get("height"):
                        x, y = rect["x"], rect["y"]
                        w, h = rect["width"], rect["height"]
                        quads = [[x, y, x + w, y, x + w, y + h, x, y + h]]
                        logger.debug("Got geometry via JS getBoundingClientRect")
            except Exception as e:
                logger.debug(f"JS getBoundingClientRect failed: {e}")

        # Method 4: JS .click() fallback
        if not quads:
            logger.debug("No geometry found, falling back to JS click")
            await self._js_click()
            return

        # Find the best quad (largest visible area within viewport)
        best_x, best_y = self._find_best_click_point(quads, viewport_width, viewport_height)

        # Scroll into view first
        try:
            await client.send.DOM.scrollIntoViewIfNeeded(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            await asyncio.sleep(0.05)
        except Exception:
            # Fallback to JS scrollIntoView
            await self.scroll_into_view()

        # Re-calculate click point after scroll (element may have moved)
        try:
            result = await client.send.DOM.getContentQuads(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            if result.get("quads"):
                quads = result["quads"]
                best_x, best_y = self._find_best_click_point(quads, viewport_width, viewport_height)
        except Exception:
            pass  # Use previous coordinates

        # Verify hit target - check if our element will receive the click
        hit_target_ok, interceptor = await self._verify_hit_target(best_x, best_y)
        if not hit_target_ok:
            logger.warning(
                f"Click at ({best_x}, {best_y}) would hit '{interceptor}' "
                f"instead of element {self._backend_node_id}, using JS click"
            )
            await self._js_click()
            return

        # Calculate modifier flags
        modifier_flags = self._calculate_modifier_flags(modifiers)

        # Perform the click
        try:
            await self._dispatch_click(best_x, best_y, button, click_count, modifier_flags)
            logger.debug(f"Clicked element {self._backend_node_id} at ({best_x}, {best_y})")
        except Exception as e:
            # Final fallback to JS click
            logger.debug(f"CDP click failed ({e}), falling back to JS click")
            await self._js_click()

    def _find_best_click_point(
        self,
        quads: list[list[float]],
        viewport_width: float,
        viewport_height: float,
    ) -> tuple[int, int]:
        """Find the best click point from quads, preferring visible area."""
        best_quad = None
        best_area = 0

        for quad in quads:
            if len(quad) < 8:
                continue

            # Calculate quad bounds
            xs = [quad[i] for i in range(0, 8, 2)]
            ys = [quad[i] for i in range(1, 8, 2)]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            # Skip if completely outside viewport
            if max_x < 0 or max_y < 0 or min_x > viewport_width or min_y > viewport_height:
                continue

            # Calculate visible area (intersection with viewport)
            visible_min_x = max(0, min_x)
            visible_max_x = min(viewport_width, max_x)
            visible_min_y = max(0, min_y)
            visible_max_y = min(viewport_height, max_y)

            visible_area = int((visible_max_x - visible_min_x) * (visible_max_y - visible_min_y))

            if visible_area > best_area:
                best_area = visible_area
                best_quad = quad

        if not best_quad:
            if not quads or len(quads[0]) < 8:
                # Fallback to center of viewport if no valid geometry
                return int(viewport_width / 2), int(viewport_height / 2)
            best_quad = quads[0]  # Use first quad if none visible

        # Calculate center of best quad (average of all points)
        xs = [best_quad[i] for i in range(0, len(best_quad), 2)]
        ys = [best_quad[i] for i in range(1, len(best_quad), 2)]
        center_x = sum(xs) / len(xs)
        center_y = sum(ys) / len(ys)

        # Ensure within viewport
        center_x = max(0, min(viewport_width - 1, center_x))
        center_y = max(0, min(viewport_height - 1, center_y))

        return int(center_x), int(center_y)

    def _calculate_modifier_flags(self, modifiers: list[ModifierType] | None) -> int:
        """Calculate CDP modifier flags bitmask."""
        flags = 0
        if modifiers:
            modifier_map = {"Alt": 1, "Control": 2, "Meta": 4, "Shift": 8}
            for mod in modifiers:
                flags |= modifier_map.get(mod, 0)
        return flags

    async def _dispatch_click(
        self,
        x: int,
        y: int,
        button: Literal["left", "right", "middle"],
        click_count: int,
        modifiers: int,
    ) -> None:
        """Dispatch mouse click events via CDP."""
        await dispatch_mouse_click(
            self._session,
            x=x,
            y=y,
            button=button,
            click_count=click_count,
            modifiers=modifiers,
        )

    async def _js_click(self) -> None:
        """Click element using JavaScript as fallback."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        result = await client.send.DOM.resolveNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )
        object_id = result.get("object", {}).get("objectId")

        if not object_id:
            raise RuntimeError(
                f"Cannot click element {self._backend_node_id}: failed to resolve node"
            )

        await client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": "function() { this.click(); }",
            },
            session_id=session_id,
        )
        await asyncio.sleep(0.05)
        logger.debug(f"JS clicked element {self._backend_node_id}")

    async def _check_pointer_events(self) -> bool:
        """
        Check if element has clickable pointer-events style.

        Returns:
            True if pointer-events allows clicking, False if 'none'
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        try:
            result = await client.send.DOM.resolveNode(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            object_id = result.get("object", {}).get("objectId")
            if not object_id:
                return True  # Assume clickable if can't resolve

            style_result = await client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": """
                        function() {
                            const style = window.getComputedStyle(this);
                            return style.pointerEvents;
                        }
                    """,
                    "returnByValue": True,
                },
                session_id=session_id,
            )
            pointer_events = style_result.get("result", {}).get("value", "auto")
            return pointer_events != "none"
        except Exception as e:
            logger.debug(f"pointer-events check failed: {e}")
            return True  # Assume clickable on error

    async def _verify_hit_target(self, x: int, y: int) -> tuple[bool, str]:
        """
        Verify that elementFromPoint at (x, y) returns this element or a child.

        Args:
            x: X coordinate to check
            y: Y coordinate to check

        Returns:
            Tuple of (is_target_ok, interceptor_description)
            - is_target_ok: True if this element will receive the click
            - interceptor_description: Description of what would intercept, or empty string
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        try:
            result = await client.send.DOM.resolveNode(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            object_id = result.get("object", {}).get("objectId")
            if not object_id:
                return True, ""  # Can't verify, assume OK

            # Check if element at point is this element or a descendant
            check_result = await client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": f"""
                        function() {{
                            const hitElement = document.elementFromPoint({x}, {y});
                            if (!hitElement) {{
                                return {{ ok: false, interceptor: 'no element at point' }};
                            }}
                            // Check if hit element is this element or a descendant
                            if (this === hitElement || this.contains(hitElement)) {{
                                return {{ ok: true, interceptor: '' }};
                            }}
                            // Check if this element is a descendant of hit element
                            // (click would still work in some cases)
                            if (hitElement.contains(this)) {{
                                return {{ ok: true, interceptor: '' }};
                            }}
                            // Something else is at the point
                            const tag = hitElement.tagName.toLowerCase();
                            const id = hitElement.id ? '#' + hitElement.id : '';
                            const cls = hitElement.className ? 
                                '.' + hitElement.className.split(' ')[0] : '';
                            return {{ 
                                ok: false, 
                                interceptor: tag + id + cls 
                            }};
                        }}
                    """,
                    "returnByValue": True,
                },
                session_id=session_id,
            )
            value = check_result.get("result", {}).get("value", {})
            return value.get("ok", True), value.get("interceptor", "")
        except Exception as e:
            logger.debug(f"hit target verification failed: {e}")
            return True, ""  # Assume OK on error

    async def fill(self, text: str, clear: bool = True) -> None:
        """
        Type text into the element with human-like key events.

        Uses proper keyDown → char → keyUp sequence for reliable input,
        with multi-strategy focus and clear operations.

        Args:
            text: Text to type
            clear: If True, clear existing content first
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Scroll into view first
        try:
            await client.send.DOM.scrollIntoViewIfNeeded(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            await asyncio.sleep(0.05)
        except Exception:
            await self.scroll_into_view()

        # Multi-strategy focus
        await self._focus_robust()

        # Clear existing content if requested
        if clear:
            await self._clear_field_robust()
            await asyncio.sleep(0.05)

        # Type text using Input.insertText for reliability
        # This is more reliable than individual key events for most inputs
        if text:
            logger.info(
                f"Typing into element {self._backend_node_id}: '{text[:30]}...' (clear={clear})"
            )
            await client.send.Input.insertText(
                {"text": text},
                session_id=session_id,
            )

        logger.debug(f"Typed {len(text)} chars into element {self._backend_node_id}")

    async def _focus_robust(self) -> None:
        """Focus element with multiple fallback strategies."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Strategy 1: CDP DOM.focus
        try:
            await client.send.DOM.focus(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            logger.debug(f"CDP focused element {self._backend_node_id}")
            return
        except Exception as e:
            logger.debug(f"CDP focus failed: {e}")

        # Strategy 2: JS focus()
        try:
            result = await client.send.DOM.resolveNode(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            object_id = result.get("object", {}).get("objectId")
            if object_id:
                await client.send.Runtime.callFunctionOn(
                    {
                        "objectId": object_id,
                        "functionDeclaration": "function() { this.focus(); }",
                    },
                    session_id=session_id,
                )
                logger.debug(f"JS focused element {self._backend_node_id}")
                return
        except Exception as e:
            logger.debug(f"JS focus failed: {e}")

        # Strategy 3: Click to focus
        logger.debug("Falling back to click-to-focus")
        await self.click()
        await asyncio.sleep(0.1)

    async def _clear_field_robust(self) -> None:
        """Clear text field using multiple strategies.

        Strategy 1: Ctrl/Cmd+A + Backspace (most reliable for React/contenteditable)
        Strategy 2: Triple-click (clickCount=3) + Delete key
        Strategy 3: JS-based clearing (fallback for regular inputs)

        IMPORTANT: Keyboard-based clearing is preferred because it properly
        syncs with React's internal state, unlike JS DOM manipulation which
        React may override during re-render.
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Strategy 1: Keyboard shortcuts Ctrl/Cmd+A + Backspace (best for React)
        # This is now the PRIMARY strategy because it properly syncs with
        # React/contenteditable elements that maintain their own state
        try:
            await self._clear_field_keyboard()
            await asyncio.sleep(0.05)  # Give React time to sync
            logger.info("Cleared field via keyboard shortcuts (Ctrl+A + Backspace)")
            return
        except Exception as e:
            logger.debug(f"Keyboard clear failed: {e}")

        # Strategy 2: Triple-click + Delete
        try:
            bbox = await self.get_bounding_box()
            if bbox:
                x, y = int(bbox.center_x), int(bbox.center_y)

                # Single triple-click (clickCount=3 selects all text)
                await client.send.Input.dispatchMouseEvent(
                    {
                        "type": "mousePressed",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 3,
                    },
                    session_id=session_id,
                )
                await client.send.Input.dispatchMouseEvent(
                    {
                        "type": "mouseReleased",
                        "x": x,
                        "y": y,
                        "button": "left",
                        "clickCount": 3,
                    },
                    session_id=session_id,
                )
                await asyncio.sleep(0.02)

                # Delete selected text
                await client.send.Input.dispatchKeyEvent(
                    {
                        "type": "keyDown",
                        "key": "Delete",
                        "code": "Delete",
                    },
                    session_id=session_id,
                )
                await client.send.Input.dispatchKeyEvent(
                    {"type": "keyUp", "key": "Delete", "code": "Delete"},
                    session_id=session_id,
                )
                logger.info("Cleared field via triple-click + Delete")
                return
        except Exception as e:
            logger.debug(f"Triple-click clear failed: {e}")

        # Strategy 3: JavaScript value/content clearing (fallback for regular inputs)
        try:
            result = await client.send.DOM.resolveNode(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            object_id = result.get("object", {}).get("objectId")
            if object_id:
                clear_result = await client.send.Runtime.callFunctionOn(
                    {
                        "objectId": object_id,
                        "functionDeclaration": """
                            function() {
                                // Check if it's a contenteditable element
                                const ce = this.getAttribute('contenteditable');
                                const hasContentEditable = ce === 'true' || ce === '' ||
                                                          this.isContentEditable === true;

                                if (hasContentEditable) {
                                    // For contenteditable elements, clear all content
                                    while (this.firstChild) {
                                        this.removeChild(this.firstChild);
                                    }
                                    this.textContent = "";
                                    this.innerHTML = "";

                                    // Focus and position cursor at the beginning
                                    this.focus();
                                    const selection = window.getSelection();
                                    const range = document.createRange();
                                    range.setStart(this, 0);
                                    range.setEnd(this, 0);
                                    selection.removeAllRanges();
                                    selection.addRange(range);

                                    // Dispatch events
                                    this.dispatchEvent(new Event("input", { bubbles: true }));
                                    this.dispatchEvent(new Event("change", { bubbles: true }));

                                    return {
                                        cleared: true, method: 'contenteditable',
                                        finalText: this.textContent
                                    };
                                } else if (this.value !== undefined) {
                                    // For regular inputs with value property
                                    try {
                                        this.select();
                                    } catch (e) {
                                        // ignore
                                    }
                                    this.value = "";
                                    this.dispatchEvent(new Event("input", { bubbles: true }));
                                    this.dispatchEvent(new Event("change", { bubbles: true }));
                                    return {cleared: true, method: 'value', finalText: this.value};
                                } else {
                                    return {
                                        cleared: false, method: 'none',
                                        error: 'Not a supported input type'
                                    };
                                }
                            }
                        """,
                        "returnByValue": True,
                    },
                    session_id=session_id,
                )

                clear_info = clear_result.get("result", {}).get("value", {})
                if clear_info.get("cleared"):
                    final_text = clear_info.get("finalText", "")
                    if not final_text or not final_text.strip():
                        logger.info(f"Cleared field via JS ({clear_info.get('method')})")
                        return
                    logger.warning(f"JS clear incomplete, field still has: '{final_text[:50]}'")
                else:
                    logger.warning(f"JS clear failed: {clear_info.get('error', 'unknown')}")
        except Exception as e:
            logger.warning(f"JS clear exception: {e}")

    async def _clear_field_keyboard(self) -> None:
        """Clear field using keyboard shortcuts (Ctrl+A + Backspace)."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        import platform

        modifier = 4 if platform.system() == "Darwin" else 2  # Cmd vs Ctrl

        # Select all
        await client.send.Input.dispatchKeyEvent(
            {"type": "keyDown", "key": "a", "code": "KeyA", "modifiers": modifier},
            session_id=session_id,
        )
        await client.send.Input.dispatchKeyEvent(
            {"type": "keyUp", "key": "a", "code": "KeyA"},
            session_id=session_id,
        )

        await asyncio.sleep(0.02)

        # Backspace to delete
        await client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown",
                "key": "Backspace",
                "code": "Backspace",
                "windowsVirtualKeyCode": 8,
            },
            session_id=session_id,
        )
        await client.send.Input.dispatchKeyEvent(
            {"type": "keyUp", "key": "Backspace", "code": "Backspace"},
            session_id=session_id,
        )
        logger.debug("Cleared field via keyboard shortcuts")

    async def _type_char(self, char: str) -> None:
        """Type a single character with proper key event sequence."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Get key info for the character
        key, code, key_code, modifiers = self._get_key_info(char)

        # Step 1: keyDown (no text)
        await client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown",
                "key": key,
                "code": code,
                "windowsVirtualKeyCode": key_code,
                "modifiers": modifiers,
            },
            session_id=session_id,
        )

        await asyncio.sleep(0.001)

        # Step 2: char event (with text)
        await client.send.Input.dispatchKeyEvent(
            {
                "type": "char",
                "text": char,
                "key": char,
                "modifiers": modifiers,
            },
            session_id=session_id,
        )

        # Step 3: keyUp (no text)
        await client.send.Input.dispatchKeyEvent(
            {
                "type": "keyUp",
                "key": key,
                "code": code,
                "windowsVirtualKeyCode": key_code,
                "modifiers": modifiers,
            },
            session_id=session_id,
        )

    async def _type_special_key(self, key: str, key_code: int) -> None:
        """Type a special key like Enter, Tab, etc."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        await client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown",
                "key": key,
                "code": key,
                "windowsVirtualKeyCode": key_code,
            },
            session_id=session_id,
        )
        await asyncio.sleep(0.001)

        # For Enter, also send a char event with carriage return
        if key == "Enter":
            await client.send.Input.dispatchKeyEvent(
                {"type": "char", "text": "\r", "key": "Enter"},
                session_id=session_id,
            )

        await client.send.Input.dispatchKeyEvent(
            {
                "type": "keyUp",
                "key": key,
                "code": key,
                "windowsVirtualKeyCode": key_code,
            },
            session_id=session_id,
        )

    def _get_key_info(self, char: str) -> tuple[str, str, int, int]:
        """Get key name, code, virtual key code, and modifiers for a character."""
        # Modifiers: 1=Alt, 2=Ctrl, 4=Meta, 8=Shift

        # Upper case letters
        if char.isalpha() and char.isupper():
            base = char
            code = f"Key{base}"
            key_code = ord(base)
            return char, code, key_code, 8  # Add Shift modifier

        # Lower case letters
        if char.isalpha():
            base = char.upper()
            code = f"Key{base}"
            key_code = ord(base)
            return char, code, key_code, 0

        # Numbers
        if char.isdigit():
            code = f"Digit{char}"
            key_code = ord(char)
            return char, code, key_code, 0

        # Special characters mapping
        # Maps char to (code, key_code, modifiers)
        special_chars = {
            " ": ("Space", 32, 0),
            "\n": ("Enter", 13, 0),
            "\r": ("Enter", 13, 0),
            "\t": ("Tab", 9, 0),
            # Standard Punctuation
            "-": ("Minus", 189, 0),
            "=": ("Equal", 187, 0),
            "[": ("BracketLeft", 219, 0),
            "]": ("BracketRight", 221, 0),
            "\\": ("Backslash", 220, 0),
            ";": ("Semicolon", 186, 0),
            "'": ("Quote", 222, 0),
            ",": ("Comma", 188, 0),
            ".": ("Period", 190, 0),
            "/": ("Slash", 191, 0),
            "`": ("Backquote", 192, 0),
            # Shifted Punctuation (US Layout)
            "_": ("Minus", 189, 8),  # Shift + -
            "+": ("Equal", 187, 8),  # Shift + =
            "{": ("BracketLeft", 219, 8),  # Shift + [
            "}": ("BracketRight", 221, 8),  # Shift + ]
            "|": ("Backslash", 220, 8),  # Shift + \
            ":": ("Semicolon", 186, 8),  # Shift + ;
            '"': ("Quote", 222, 8),  # Shift + '
            "<": ("Comma", 188, 8),  # Shift + ,
            ">": ("Period", 190, 8),  # Shift + .
            "?": ("Slash", 191, 8),  # Shift + /
            "~": ("Backquote", 192, 8),  # Shift + `
            # Shifted Numbers
            "!": ("Digit1", 49, 8),  # Shift + 1
            "@": ("Digit2", 50, 8),  # Shift + 2
            "#": ("Digit3", 51, 8),  # Shift + 3
            "$": ("Digit4", 52, 8),  # Shift + 4
            "%": ("Digit5", 53, 8),  # Shift + 5
            "^": ("Digit6", 54, 8),  # Shift + 6
            "&": ("Digit7", 55, 8),  # Shift + 7
            "*": ("Digit8", 56, 8),  # Shift + 8
            "(": ("Digit9", 57, 8),  # Shift + 9
            ")": ("Digit0", 48, 8),  # Shift + 0
        }

        if char in special_chars:
            code, key_code, modifiers = special_chars[char]
            return char, code, key_code, modifiers

        # Default: use the character itself
        return char, "", 0, 0

    async def hover(self) -> None:
        """Hover over the element."""
        bbox = await self.get_bounding_box()
        if not bbox:
            raise RuntimeError(f"Element {self._backend_node_id} not visible")

        x, y = int(bbox.center_x), int(bbox.center_y)

        await self._session.cdp_client.send.Input.dispatchMouseEvent(
            {
                "type": "mouseMoved",
                "x": x,
                "y": y,
            },
            session_id=self._session.session_id,
        )

        logger.debug(f"Hovered element {self._backend_node_id} at ({x}, {y})")

    async def focus(self) -> None:
        """Focus the element."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Get node ID from backend node ID
        result = await client.send.DOM.describeNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )
        node_id = result.get("node", {}).get("nodeId")

        try:
            if node_id:
                await client.send.DOM.focus(
                    {"nodeId": node_id},
                    session_id=session_id,
                )
            else:
                await client.send.DOM.focus(
                    {"backendNodeId": self._backend_node_id},
                    session_id=session_id,
                )
            logger.debug(f"Focused element {self._backend_node_id}")
        except Exception as e:
            # DOM.focus can fail for contenteditable elements
            # Try JavaScript focus as fallback
            logger.debug(f"DOM.focus failed ({e}), trying JS focus")
            result = await client.send.DOM.resolveNode(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )
            object_id = result.get("object", {}).get("objectId")
            if object_id:
                await client.send.Runtime.callFunctionOn(
                    {
                        "objectId": object_id,
                        "functionDeclaration": "function() { this.focus(); }",
                        "returnByValue": True,
                    },
                    session_id=session_id,
                )
                logger.debug(f"JS focused element {self._backend_node_id}")
            else:
                raise RuntimeError(f"Could not focus element {self._backend_node_id}") from None

    async def scroll_into_view(self) -> None:
        """
        Scroll element into view with container-aware scrolling.

        First scrolls any scrollable parent containers, then scrolls
        the element into the viewport.
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Get object ID for the element
        result = await client.send.DOM.resolveNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )
        object_id = result.get("object", {}).get("objectId")

        if object_id:
            # Smart scroll: find scrollable container and scroll it first
            await client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": """
                        function() {
                            // Find scrollable parent container
                            function getScrollableParent(el) {
                                if (!el || el === document.body) return null;
                                const parent = el.parentElement;
                                if (!parent) return null;
                                
                                const style = window.getComputedStyle(parent);
                                const overflowY = style.overflowY;
                                const overflowX = style.overflowX;
                                
                                // Check if parent is scrollable
                                const isScrollable = (
                                    (overflowY === 'auto' || overflowY === 'scroll' ||
                                     overflowX === 'auto' || overflowX === 'scroll') &&
                                    (parent.scrollHeight > parent.clientHeight ||
                                     parent.scrollWidth > parent.clientWidth)
                                );
                                
                                if (isScrollable) return parent;
                                return getScrollableParent(parent);
                            }
                            
                            // Scroll container first if found
                            const container = getScrollableParent(this);
                            if (container) {
                                this.scrollIntoView({
                                    behavior: 'instant',
                                    block: 'center',
                                    inline: 'center'
                                });
                            } else {
                                // No scrollable container, scroll normally
                                this.scrollIntoView({
                                    behavior: 'instant',
                                    block: 'center',
                                    inline: 'center'
                                });
                            }
                        }
                    """,
                    "returnByValue": True,
                },
                session_id=session_id,
            )

        logger.debug(f"Scrolled element {self._backend_node_id} into view")

    async def get_bounding_box(self) -> BoundingBox | None:
        """Get element bounding box."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        try:
            result = await client.send.DOM.getBoxModel(
                {"backendNodeId": self._backend_node_id},
                session_id=session_id,
            )

            model = result.get("model", {})
            content = model.get("content", [])

            if len(content) >= 8:
                # content is [x1, y1, x2, y2, x3, y3, x4, y4]
                x = min(content[0], content[2], content[4], content[6])
                y = min(content[1], content[3], content[5], content[7])
                width = max(content[0], content[2], content[4], content[6]) - x
                height = max(content[1], content[3], content[5], content[7]) - y

                return BoundingBox(x=x, y=y, width=width, height=height)
        except Exception as e:
            logger.debug(f"Could not get bounding box: {e}")

        return None

    async def get_attribute(self, name: str) -> str | None:
        """Get element attribute value."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        result = await client.send.DOM.describeNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )

        attributes = result.get("node", {}).get("attributes", [])

        # Attributes come as [name1, value1, name2, value2, ...]
        for i in range(0, len(attributes), 2):
            if attributes[i] == name:
                return attributes[i + 1]

        return None

    async def _resolve_object_id(self) -> str:
        """Resolve the DOM node into a runtime object ID."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        result = await client.send.DOM.resolveNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )
        object_id = result.get("object", {}).get("objectId")

        if not object_id:
            raise RuntimeError(f"Could not resolve element {self._backend_node_id}")

        return object_id

    @staticmethod
    def _runtime_error_message(result: dict[str, Any]) -> str:
        """Extract a readable error message from a CDP Runtime response."""
        details = result.get("exceptionDetails", {}) or {}
        exception = details.get("exception", {}) or {}
        return str(exception.get("description") or details.get("text") or details)

    async def _run_dropdown_interaction(
        self,
        mode: Literal["inspect", "select"],
        value: str | None = None,
        open_if_needed: bool = False,
    ) -> dict[str, Any]:
        """Inspect or interact with native and custom dropdown-like controls."""
        client = self._session.cdp_client
        session_id = self._session.session_id
        object_id = await self._resolve_object_id()

        result = await client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": r"""
                    async function(args) {
                        const node = this;
                        const normalize = (value) =>
                            String(value ?? '').replace(/\s+/g, ' ').trim();
                        const normalizedNeedle = normalize(args.value).toLowerCase();
                        const isElement = (value) => value && value.nodeType === Node.ELEMENT_NODE;
                        const isVisible = (element) => {
                            if (!isElement(element) || !element.isConnected) return false;
                            const style = window.getComputedStyle(element);
                            if (!style) return false;
                            if (
                                style.display === 'none' ||
                                style.visibility === 'hidden'
                            ) {
                                return false;
                            }
                            if (element.getAttribute('aria-hidden') === 'true') return false;
                            const rect = element.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        };
                        const isDisabled = (element) => (
                            element.hasAttribute('disabled') ||
                            element.getAttribute('aria-disabled') === 'true'
                        );
                        const optionSelector = [
                            'option',
                            '[role="option"]',
                            '[role="treeitem"]',
                            '[role="gridcell"]',
                            '[role="menuitem"]',
                            '[role="menuitemcheckbox"]',
                            '[role="menuitemradio"]'
                        ].join(',');
                        const rootSelector = [
                            '[role="listbox"]',
                            '[role="menu"]',
                            '[role="tree"]',
                            '[role="grid"]',
                            '[data-state="open"]',
                            '[data-headlessui-state~="open"]'
                        ].join(',');
                        const roots = [];
                        const seenRoots = new Set();
                        const addRoot = (element) => {
                            if (!isElement(element) || seenRoots.has(element)) return;
                            seenRoots.add(element);
                            roots.push(element);
                        };
                        const toOption = (element) => {
                            const label = normalize(
                                element.innerText ||
                                element.textContent ||
                                element.getAttribute('aria-label') ||
                                element.getAttribute('label') ||
                                element.value ||
                                element.getAttribute('data-value') ||
                                element.title
                            );
                            const value = normalize(
                                element.value ??
                                element.getAttribute('value') ??
                                element.getAttribute('data-value') ??
                                element.getAttribute('aria-label') ??
                                label
                            );
                            return {
                                label,
                                value,
                                selected:
                                    element.selected === true ||
                                    element.getAttribute('aria-selected') === 'true' ||
                                    element.getAttribute('aria-checked') === 'true',
                                disabled: isDisabled(element),
                                role: normalize(element.getAttribute('role')),
                                tag: normalize(element.tagName)
                            };
                        };
                        const controlledIds = (element) => [
                            element.getAttribute('aria-controls'),
                            element.getAttribute('aria-owns')
                        ]
                            .filter(Boolean)
                            .join(' ')
                            .split(/\s+/)
                            .map((value) => normalize(value))
                            .filter(Boolean);
                        const collectOptions = (root) => {
                            if (!isElement(root)) return [];
                            if (root.tagName === 'SELECT') {
                                return Array.from(root.options).map((element) => ({
                                    element,
                                    ...toOption(element)
                                }));
                            }
                            if (root.tagName === 'DATALIST') {
                                return Array.from(root.options || []).map((element) => ({
                                    element,
                                    ...toOption(element)
                                }));
                            }
                            const candidates = root.matches(optionSelector)
                                ? [root]
                                : Array.from(root.querySelectorAll(optionSelector));
                            return candidates
                                .filter((element) => isVisible(element) || root === node)
                                .map((element) => ({ element, ...toOption(element) }))
                                .filter((option) => option.label || option.value);
                        };
                        const addControlledRoots = (element) => {
                            controlledIds(element).forEach((id) => {
                                addRoot(document.getElementById(id));
                            });
                            const activeDescendant = normalize(
                                element.getAttribute('aria-activedescendant')
                            );
                            if (!activeDescendant) return;
                            const active = document.getElementById(activeDescendant);
                            if (!active) return;
                            addRoot(
                                active.closest(
                                    '[role="listbox"], [role="menu"], [role="tree"], [role="grid"]'
                                )
                            );
                            addRoot(active);
                        };
                        const nodeRect = node.getBoundingClientRect();
                        const nodeCenter = {
                            x: nodeRect.left + nodeRect.width / 2,
                            y: nodeRect.top + nodeRect.height / 2
                        };
                        const distance = (element) => {
                            const rect = element.getBoundingClientRect();
                            const bx = rect.left + rect.width / 2;
                            const by = rect.top + rect.height / 2;
                            return Math.hypot(nodeCenter.x - bx, nodeCenter.y - by);
                        };
                        const addNearbyRoots = () => {
                            Array.from(document.querySelectorAll(rootSelector))
                                .filter((element) => isVisible(element))
                                .filter((element) => element !== node)
                                .filter((element) => {
                                    return (
                                        element.matches(optionSelector) ||
                                        element.querySelector(optionSelector)
                                    );
                                })
                                .map((element) => ({
                                    element,
                                    distance: distance(element)
                                }))
                                .sort((left, right) => left.distance - right.distance)
                                .slice(0, 5)
                                .forEach(({ element }) => addRoot(element));
                        };
                        const addIntrinsicRoots = () => {
                            if (
                                node.tagName === 'SELECT' ||
                                node.tagName === 'DATALIST'
                            ) {
                                addRoot(node);
                            }
                            addRoot(
                                node.closest(
                                    '[role="listbox"], [role="menu"], [role="tree"], [role="grid"]'
                                )
                            );
                            if (node.matches(optionSelector)) {
                                addRoot(node);
                            }
                            addControlledRoots(node);
                            if (!node.id) return;
                            Array.from(document.querySelectorAll('[aria-labelledby]'))
                                .filter((element) => {
                                    const labelledBy = normalize(
                                        element.getAttribute('aria-labelledby')
                                    );
                                    if (!labelledBy) return false;
                                    return labelledBy.split(/\s+/).includes(node.id);
                                })
                                .forEach((element) => addRoot(element));
                        };
                        const snapshot = () => {
                            const seenOptions = new Set();
                            const options = [];
                            roots.forEach((root) => {
                                collectOptions(root).forEach((entry) => {
                                    const key = [
                                        entry.label,
                                        entry.value,
                                        entry.role,
                                        entry.tag,
                                        entry.element.id || ''
                                    ].join('::');
                                    if (seenOptions.has(key)) return;
                                    seenOptions.add(key);
                                    options.push(entry);
                                });
                            });
                            return options;
                        };
                        const refreshOptions = () => {
                            roots.length = 0;
                            seenRoots.clear();
                            addIntrinsicRoots();
                            addNearbyRoots();
                            return snapshot();
                        };
                        const maybeOpen = async () => {
                            if (
                                node.tagName === 'SELECT' ||
                                node.matches('[role="listbox"], [role="menu"]')
                            ) {
                                return {
                                    opened: false,
                                    options: refreshOptions()
                                };
                            }
                            node.focus?.();
                            if (typeof node.showPicker === 'function') {
                                try {
                                    node.showPicker();
                                } catch (error) {
                                }
                            }
                            node.click?.();
                            const maxWaitMs = 500;
                            const pollIntervalMs = 50;
                            let waitedMs = 0;
                            let options = refreshOptions();
                            let opened =
                                node.getAttribute('aria-expanded') === 'true' ||
                                roots.length > 0;

                            while (!options.length && waitedMs < maxWaitMs) {
                                await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
                                waitedMs += pollIntervalMs;
                                options = refreshOptions();
                                opened =
                                    opened ||
                                    node.getAttribute('aria-expanded') === 'true' ||
                                    roots.length > 0;
                            }

                            return {
                                opened,
                                options
                            };
                        };

                        let options = refreshOptions();
                        let opened = false;

                        if (!options.length && args.openIfNeeded) {
                            const openResult = await maybeOpen();
                            opened = openResult.opened;
                            options = openResult.options;
                        }

                        const serializableOptions = options.map(({ element, ...option }) => option);
                        const kind = node.tagName === 'SELECT'
                            ? 'select'
                            : serializableOptions.some((option) =>
                                option.role.startsWith('menuitem')
                            )
                                ? 'menu'
                                : 'custom';

                        if (args.mode === 'inspect') {
                            return {
                                kind,
                                opened,
                                options: serializableOptions
                            };
                        }

                        if (!normalizedNeedle) {
                            throw new Error('Option value is required');
                        }

                        const exactMatch = options.find((option) =>
                            normalize(option.label).toLowerCase() === normalizedNeedle ||
                            normalize(option.value).toLowerCase() === normalizedNeedle
                        );
                        const partialMatches = options.filter((option) =>
                            normalize(option.label).toLowerCase().includes(normalizedNeedle) ||
                            normalize(option.value).toLowerCase().includes(normalizedNeedle)
                        );
                        const match = exactMatch || (
                            partialMatches.length === 1 ? partialMatches[0] : null
                        );

                        if (!match) {
                            throw new Error(
                                `Option not found: ${args.value}. Available: ${serializableOptions
                                    .map((option) => option.label || option.value)
                                    .filter(Boolean)
                                    .join(', ')}`
                            );
                        }
                        if (match.disabled) {
                            throw new Error(`Option is disabled: ${match.label || match.value}`);
                        }

                        if (node.tagName === 'SELECT') {
                            const nativeOption = Array.from(node.options).find((element) =>
                                normalize(element.text).toLowerCase() === normalizedNeedle ||
                                normalize(element.value).toLowerCase() === normalizedNeedle
                            );
                            if (!nativeOption) {
                                throw new Error(`Option not found: ${args.value}`);
                            }
                            node.value = nativeOption.value;
                            nativeOption.selected = true;
                            node.dispatchEvent(new Event('input', { bubbles: true }));
                            node.dispatchEvent(new Event('change', { bubbles: true }));
                            return {
                                kind,
                                opened,
                                option: {
                                    label: normalize(nativeOption.text),
                                    value: normalize(nativeOption.value)
                                }
                            };
                        }

                        match.element.scrollIntoView?.({ block: 'nearest', inline: 'nearest' });
                        match.element.click?.();
                        match.element.dispatchEvent?.(new Event('input', { bubbles: true }));
                        match.element.dispatchEvent?.(new Event('change', { bubbles: true }));
                        return {
                            kind,
                            opened,
                            option: {
                                label: match.label,
                                value: match.value
                            }
                        };
                    }
                """,
                "arguments": [
                    {
                        "value": {
                            "mode": mode,
                            "value": value,
                            "openIfNeeded": open_if_needed,
                        }
                    }
                ],
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=session_id,
        )

        if "exceptionDetails" in result:
            raise RuntimeError(self._runtime_error_message(result))

        payload = result.get("result", {}).get("value")
        if not isinstance(payload, dict):
            raise RuntimeError("Dropdown interaction returned an invalid response")

        return payload

    async def get_dropdown_options(self, open_if_needed: bool = False) -> dict[str, Any]:
        """Return the available options for a native or custom dropdown."""
        return await self._run_dropdown_interaction(
            mode="inspect",
            open_if_needed=open_if_needed,
        )

    async def select_option(self, value: str) -> str:
        """
        Select an option by value or text.

        Args:
            value: Value or visible text of the option

        Returns:
            The text of the selected option
        """
        result = await self._run_dropdown_interaction(
            mode="select",
            value=value,
            open_if_needed=True,
        )
        option = result.get("option", {})
        if not isinstance(option, dict):
            raise RuntimeError("Dropdown selection returned an invalid response")

        selected = str(option.get("label") or option.get("value") or "").strip()
        if not selected:
            raise RuntimeError("Dropdown selection returned an empty option")

        return selected
