"""
Core Actions - Browser actions for Heimdall agent.

Implements click, type, navigate, scroll, and other browser actions.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from heimdall.tools.registry import ActionResult, action

if TYPE_CHECKING:
    from heimdall.browser.session import BrowserSession
    from heimdall.dom.service import SerializedDOM

logger = logging.getLogger(__name__)


@action("Click element by index from the DOM list")
async def click(
    index: int,
    session: "BrowserSession",
    dom_state: "SerializedDOM",
) -> ActionResult:
    """Click on an element by its index."""
    from heimdall.browser.element import Element

    if index not in dom_state.selector_map:
        return ActionResult.fail(f"Invalid element index: {index}")

    element_info = dom_state.selector_map[index]
    backend_node_id = element_info["backend_node_id"]

    element = Element(session, backend_node_id)

    try:
        await element.scroll_into_view()
        await asyncio.sleep(0.1)
        await element.click()

        return ActionResult.ok(
            f"Clicked element {index}",
            element=element_info,
        )
    except Exception as e:
        return ActionResult.fail(f"Click failed: {e}")


@action("Type text into element by index")
async def type_text(
    index: int,
    text: str,
    session: "BrowserSession",
    dom_state: "SerializedDOM",
    clear: bool = True,
) -> ActionResult:
    """Type text into an input element."""
    from heimdall.browser.element import Element

    if index not in dom_state.selector_map:
        return ActionResult.fail(f"Invalid element index: {index}")

    element_info = dom_state.selector_map[index]
    backend_node_id = element_info["backend_node_id"]

    element = Element(session, backend_node_id)

    try:
        await element.scroll_into_view()
        await element.fill(text, clear=clear)

        return ActionResult.ok(
            f"Typed '{text[:20]}{'...' if len(text) > 20 else ''}' into element {index}",
            element=element_info,
            text=text,
        )
    except Exception as e:
        return ActionResult.fail(f"Type failed: {e}")


@action("Navigate to a URL")
async def navigate(
    url: str,
    session: "BrowserSession",
) -> ActionResult:
    """Navigate to a URL."""
    try:
        await session.navigate(url)
        current_url = await session.get_url()

        return ActionResult.ok(
            f"Navigated to {url}",
            url=current_url,
        )
    except Exception as e:
        return ActionResult.fail(f"Navigation failed: {e}")


@action("Go back to previous page")
async def go_back(session: "BrowserSession") -> ActionResult:
    """Go back in browser history."""
    try:
        await session.execute_js("window.history.back()")
        await asyncio.sleep(0.5)

        return ActionResult.ok("Went back")
    except Exception as e:
        return ActionResult.fail(f"Go back failed: {e}")


@action("Scroll the page")
async def scroll(
    direction: str,
    session: "BrowserSession",
    amount: int = 500,
) -> ActionResult:
    """
    Scroll the page.

    Args:
        direction: 'up', 'down', 'left', 'right'
        amount: Scroll amount in pixels
    """
    scroll_code = {
        "up": f"window.scrollBy(0, -{amount})",
        "down": f"window.scrollBy(0, {amount})",
        "left": f"window.scrollBy(-{amount}, 0)",
        "right": f"window.scrollBy({amount}, 0)",
    }

    if direction not in scroll_code:
        return ActionResult.fail(f"Invalid direction: {direction}")

    try:
        await session.execute_js(scroll_code[direction])
        return ActionResult.ok(f"Scrolled {direction} by {amount}px")
    except Exception as e:
        return ActionResult.fail(f"Scroll failed: {e}")


@action("Wait for a specified time")
async def wait(seconds: float = 1.0) -> ActionResult:
    """Wait for specified seconds."""
    await asyncio.sleep(seconds)
    return ActionResult.ok(f"Waited {seconds}s")


@action("Take a screenshot")
async def screenshot(
    session: "BrowserSession",
    full_page: bool = False,
) -> ActionResult:
    """Take a screenshot of the current page."""
    try:
        data = await session.screenshot(full_page=full_page)
        return ActionResult.ok(
            "Screenshot captured",
            size=len(data),
        )
    except Exception as e:
        return ActionResult.fail(f"Screenshot failed: {e}")


@action("Get current page URL")
async def get_url(session: "BrowserSession") -> ActionResult:
    """Get the current page URL."""
    try:
        url = await session.get_url()
        return ActionResult.ok(url, url=url)
    except Exception as e:
        return ActionResult.fail(f"Get URL failed: {e}")


@action("Get current page title")
async def get_title(session: "BrowserSession") -> ActionResult:
    """Get the current page title."""
    try:
        title = await session.get_title()
        return ActionResult.ok(title, title=title)
    except Exception as e:
        return ActionResult.fail(f"Get title failed: {e}")


@action("Execute JavaScript code")
async def execute_js(
    code: str,
    session: "BrowserSession",
) -> ActionResult:
    """Execute JavaScript in the page."""
    try:
        result = await session.execute_js(code)
        return ActionResult.ok(str(result) if result else "", result=result)
    except Exception as e:
        return ActionResult.fail(f"JS execution failed: {e}")


@action("Mark task as complete")
async def done(message: str = "Task completed") -> ActionResult:
    """Mark the current task as complete."""
    return ActionResult.ok(message, done=True)


@action("Hover over element by index")
async def hover(
    index: int,
    session: "BrowserSession",
    dom_state: "SerializedDOM",
) -> ActionResult:
    """Hover over an element."""
    from heimdall.browser.element import Element

    if index not in dom_state.selector_map:
        return ActionResult.fail(f"Invalid element index: {index}")

    element_info = dom_state.selector_map[index]
    backend_node_id = element_info["backend_node_id"]

    element = Element(session, backend_node_id)

    try:
        await element.scroll_into_view()
        await element.hover()

        return ActionResult.ok(f"Hovered element {index}")
    except Exception as e:
        return ActionResult.fail(f"Hover failed: {e}")


@action("Press a keyboard key")
async def press_key(
    key: str,
    session: "BrowserSession",
) -> ActionResult:
    """Press a keyboard key (e.g., 'Enter', 'Tab', 'Escape')."""
    try:
        await session.cdp_client.send.Input.dispatchKeyEvent(
            {
                "type": "keyDown",
                "key": key,
            },
            session_id=session.session_id,
        )
        await session.cdp_client.send.Input.dispatchKeyEvent(
            {
                "type": "keyUp",
                "key": key,
            },
            session_id=session.session_id,
        )

        return ActionResult.ok(f"Pressed {key}")
    except Exception as e:
        return ActionResult.fail(f"Key press failed: {e}")
