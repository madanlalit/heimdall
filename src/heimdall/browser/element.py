"""
Element - Browser element operations for Heimdall.

Provides methods to interact with DOM elements via CDP.
Based on browser-use patterns with fallback strategies.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

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
        Click the element.

        Uses multiple strategies with fallback:
        1. Get bounding box and click center
        2. Use DOM.focus + scrollIntoView if needed
        3. Fallback to dispatchEvent
        """
        bbox = await self.get_bounding_box()
        if not bbox:
            raise RuntimeError(f"Element {self._backend_node_id} not visible")

        x, y = int(bbox.center_x), int(bbox.center_y)

        # Calculate modifier flags
        modifier_flags = 0
        if modifiers:
            if "Alt" in modifiers:
                modifier_flags |= 1
            if "Control" in modifiers:
                modifier_flags |= 2
            if "Meta" in modifiers:
                modifier_flags |= 4
            if "Shift" in modifiers:
                modifier_flags |= 8

        client = self._session.cdp_client
        session_id = self._session.session_id

        # Mouse down
        await client.send.Input.dispatchMouseEvent(
            {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": button,
                "clickCount": click_count,
                "modifiers": modifier_flags,
            },
            session_id=session_id,
        )

        # Small delay for realism
        await asyncio.sleep(0.05)

        # Mouse up
        await client.send.Input.dispatchMouseEvent(
            {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": button,
                "clickCount": click_count,
                "modifiers": modifier_flags,
            },
            session_id=session_id,
        )

        logger.debug(f"Clicked element {self._backend_node_id} at ({x}, {y})")

    async def fill(self, text: str, clear: bool = True) -> None:
        """
        Type text into the element.

        Args:
            text: Text to type
            clear: If True, clear existing content first
        """
        # Try to focus element first (may fail for contenteditable divs)
        try:
            await self.focus()
        except Exception as e:
            # Fallback: click to focus (works for contenteditable divs like ChatGPT)
            logger.debug(f"DOM.focus failed ({e}), using click to focus")
            await self.click()
            await asyncio.sleep(0.1)  # Wait for focus to take effect

        client = self._session.cdp_client
        session_id = self._session.session_id

        if clear:
            await self._clear_field()

        # Type each character
        for char in text:
            await client.send.Input.dispatchKeyEvent(
                {
                    "type": "keyDown",
                    "text": char,
                },
                session_id=session_id,
            )
            await client.send.Input.dispatchKeyEvent(
                {
                    "type": "keyUp",
                    "text": char,
                },
                session_id=session_id,
            )
            # Small delay between keystrokes
            await asyncio.sleep(0.02)

        logger.debug(f"Typed {len(text)} chars into element {self._backend_node_id}")

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
        """Scroll element into view."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Get object ID for the element
        result = await client.send.DOM.resolveNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )
        object_id = result.get("object", {}).get("objectId")

        if object_id:
            await client.send.Runtime.callFunctionOn(
                {
                    "objectId": object_id,
                    "functionDeclaration": """
                        function() {
                            this.scrollIntoView({
                                behavior: 'instant',
                                block: 'center',
                                inline: 'center'
                            });
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

    async def _clear_field(self) -> None:
        """Clear text field content."""
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Select all + delete
        await client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown",
                "key": "a",
                "code": "KeyA",
                "modifiers": 4 if __import__("platform").system() == "Darwin" else 2,  # Cmd/Ctrl
            },
            session_id=session_id,
        )
        await client.send.Input.dispatchKeyEvent(
            {"type": "keyUp", "key": "a", "code": "KeyA"},
            session_id=session_id,
        )

        await client.send.Input.dispatchKeyEvent(
            {"type": "keyDown", "key": "Backspace", "code": "Backspace"},
            session_id=session_id,
        )
        await client.send.Input.dispatchKeyEvent(
            {"type": "keyUp", "key": "Backspace", "code": "Backspace"},
            session_id=session_id,
        )

    async def select_option(self, value: str) -> str:
        """
        Select an option by value or text.

        Args:
            value: Value or visible text of the option

        Returns:
            The text of the selected option
        """
        client = self._session.cdp_client
        session_id = self._session.session_id

        # Get object ID
        result = await client.send.DOM.resolveNode(
            {"backendNodeId": self._backend_node_id},
            session_id=session_id,
        )
        object_id = result.get("object", {}).get("objectId")

        if not object_id:
            raise RuntimeError(f"Could not resolve element {self._backend_node_id}")

        # Execute JS on the element
        result = await client.send.Runtime.callFunctionOn(
            {
                "objectId": object_id,
                "functionDeclaration": """
                    function(value) {
                        const node = this;
                        if (node.tagName !== 'SELECT') throw new Error('Not a select element');

                        for (let opt of node.options) {
                            if (opt.value === value || opt.text === value) {
                                opt.selected = true;
                                node.dispatchEvent(new Event('change', { bubbles: true }));
                                return opt.text;
                            }
                        }
                        throw new Error('Option not found: ' + value);
                    }
                """,
                "arguments": [{"value": value}],
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=session_id,
        )

        if "exceptionDetails" in result:
            raise RuntimeError(f"Selection failed: {result['exceptionDetails']}")

        return result.get("result", {}).get("value", "")
