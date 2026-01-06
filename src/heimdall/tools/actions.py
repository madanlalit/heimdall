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


DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY = 0.5  # seconds


async def with_retry(
    action_fn,
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    delay: float = DEFAULT_RETRY_DELAY,
    element_context: str = "",
    **kwargs,
) -> ActionResult:
    """
    Execute an action with automatic retry on failure.

    Uses exponential backoff between retries.

    Args:
        action_fn: The async action function to execute
        max_retries: Maximum retry attempts (default: 2)
        delay: Initial delay between retries (default: 0.5s)
        element_context: Element description for error messages

    Returns:
        ActionResult from the action
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await action_fn(*args, **kwargs)

            # If action returned success, or it's a known permanent failure, return
            if result.success:
                return result

            # Check if the error is retryable
            error_msg = result.error or ""
            retryable_errors = [
                "not visible",
                "failed to resolve",
                "no geometry found",
                "timed out",
            ]

            if not any(err in error_msg.lower() for err in retryable_errors):
                return result

            last_error = result.error

        except Exception as e:
            last_error = str(e)

        if attempt < max_retries:
            wait_time = delay * (2**attempt)
            logger.debug(
                f"Retry {attempt + 1}/{max_retries} for {element_context} "
                f"after {wait_time:.1f}s (error: {last_error})"
            )
            await asyncio.sleep(wait_time)

    error_detail = f"Failed after {max_retries + 1} attempts"
    if element_context:
        error_detail += f" on {element_context}"
    if last_error:
        error_detail += f": {last_error}"

    return ActionResult.fail(error_detail)


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

    async def _do_click():
        try:
            # element.click() handles scrolling internally
            await element.click()
            return ActionResult.ok(
                f"Clicked element {index}",
                element=element_info,
            )
        except Exception as e:
            return ActionResult.fail(f"Click failed: {e}")

    return await with_retry(
        _do_click,
        element_context=f"element {index} ({element_info.get('tag', 'unknown')})",
    )


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

    async def _do_type():
        try:
            # element.fill() handles scrolling internally
            await element.fill(text, clear=clear)

            return ActionResult.ok(
                f"Typed '{text[:20]}{'...' if len(text) > 20 else ''}' into element {index}",
                element=element_info,
                text=text,
            )
        except Exception as e:
            return ActionResult.fail(f"Type failed: {e}")

    return await with_retry(
        _do_type,
        element_context=f"element {index} ({element_info.get('tag', 'unknown')})",
    )


@action("Navigate to a URL")
async def navigate(
    url: str,
    session: "BrowserSession",
    allowed_domains: list[str] | None = None,
) -> ActionResult:
    """Navigate to a URL."""
    from heimdall.utils.domain import is_url_allowed

    if allowed_domains and not is_url_allowed(url, allowed_domains):
        return ActionResult.fail(
            f"Navigation blocked: {url} is not in allowed domains {allowed_domains}"
        )

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
    """Go back in browser history using CDP."""
    try:
        # Get navigation history
        result = await session.cdp_client.send.Page.getNavigationHistory(
            session_id=session.session_id,
        )
        current_index = result.get("currentIndex", 0)
        entries = result.get("entries", [])

        if current_index <= 0:
            return ActionResult.fail("Cannot go back: already at first page")

        # Navigate to previous entry
        prev_entry = entries[current_index - 1]
        await session.cdp_client.send.Page.navigateToHistoryEntry(
            {"entryId": prev_entry["id"]},
            session_id=session.session_id,
        )
        await session.wait_for_stable()

        return ActionResult.ok("Went back")
    except Exception as e:
        return ActionResult.fail(f"Go back failed: {e}")


@action("Refresh/reload the current page")
async def refresh_page(session: "BrowserSession") -> ActionResult:
    """Refresh the current page to get fresh content using CDP."""
    try:
        await session.cdp_client.send.Page.reload(
            {},
            session_id=session.session_id,
        )
        await session.wait_for_stable()

        return ActionResult.ok("Page refreshed")
    except Exception as e:
        return ActionResult.fail(f"Refresh failed: {e}")


@action("Scroll the page")
async def scroll(
    direction: str,
    session: "BrowserSession",
    amount: int = 500,
) -> ActionResult:
    """
    Scroll the page with verification and fallback.

    Args:
        direction: 'up', 'down', 'left', 'right'
        amount: Scroll amount in pixels
    """
    if direction not in ("up", "down", "left", "right"):
        return ActionResult.fail(f"Invalid direction: {direction}")

    # Calculate scroll deltas
    x_scroll = 0
    y_scroll = 0

    if direction == "up":
        y_scroll = -amount
    elif direction == "down":
        y_scroll = amount
    elif direction == "left":
        x_scroll = -amount
    elif direction == "right":
        x_scroll = amount

    try:
        # Get initial scroll position
        initial_pos = await session.execute_js(
            "[window.scrollX || window.pageXOffset, window.scrollY || window.pageYOffset]"
        )

        # Try CDP scroll gesture first (more human-like)
        cdp_success = False
        try:
            layout = await session.cdp_client.send.Page.getLayoutMetrics(
                session_id=session.session_id
            )
            viewport = layout.get("layoutViewport", {})
            center_x = int(viewport.get("clientWidth", 800) / 2)
            center_y = int(viewport.get("clientHeight", 600) / 2)

            # CDP uses opposite sign convention for yDistance
            await session.cdp_client.send.Input.synthesizeScrollGesture(
                {
                    "x": center_x,
                    "y": center_y,
                    "xDistance": -x_scroll,
                    "yDistance": -y_scroll,
                    "speed": 800,  # Smooth scroll speed
                },
                session_id=session.session_id,
            )
            cdp_success = True
        except Exception:
            # CDP failed, will use JS fallback
            pass

        # Fallback to JavaScript scroll if CDP failed
        if not cdp_success:
            await session.execute_js(
                f"window.scrollBy({{left: {x_scroll}, top: {y_scroll}, behavior: 'smooth'}})"
            )
            # Wait for smooth scroll to complete
            await asyncio.sleep(0.3)

        # Verify scroll happened
        final_pos = await session.execute_js(
            "[window.scrollX || window.pageXOffset, window.scrollY || window.pageYOffset]"
        )

        # Calculate actual scroll distance
        actual_x = (final_pos[0] if final_pos else 0) - (initial_pos[0] if initial_pos else 0)
        actual_y = (final_pos[1] if final_pos else 0) - (initial_pos[1] if initial_pos else 0)

        if actual_x == 0 and actual_y == 0:
            return ActionResult.ok(
                f"Scrolled {direction} (at boundary or content not scrollable)",
                at_boundary=True,
            )

        actual_dist = abs(actual_y) if direction in ("up", "down") else abs(actual_x)
        return ActionResult.ok(
            f"Scrolled {direction} by {actual_dist}px",
            actual_scroll=(actual_x, actual_y),
        )

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
async def done(message: str = "Task completed", success: bool = True) -> ActionResult:
    """Mark the current task as complete with success/failure status."""
    return ActionResult.ok(message, done=True, success=success)


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

    async def _do_hover():
        try:
            # Scroll into view first since hover() doesn't scroll internally
            await element.scroll_into_view()
            await element.hover()
            return ActionResult.ok(f"Hovered element {index}")
        except Exception as e:
            return ActionResult.fail(f"Hover failed: {e}")

    return await with_retry(
        _do_hover,
        element_context=f"element {index} ({element_info.get('tag', 'unknown')})",
    )


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


@action("Ask human for guidance when stuck or need help")
async def ask_human(
    question: str,
) -> ActionResult:
    """
    Pause execution and ask the human for guidance.

    Use this when:
    - You are stuck and can't find an element
    - You're unsure which path to take
    - You need clarification on the task
    - Login is required but you don't have credentials

    Args:
        question: Clear question describing what help you need

    Returns:
        The human's response with guidance
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Prompt

    console = Console()

    console.print()
    console.print(
        Panel(
            f"[bold yellow]🤖 Agent needs help:[/bold yellow]\n\n{question}",
            title="[bold blue]Human Input Required[/bold blue]",
            border_style="blue",
        )
    )

    try:
        import asyncio
        from functools import partial

        # Use partial to avoid blocking the event loop with synchronous I/O
        prompt_func = partial(
            Prompt.ask,
            "[bold green]Your guidance[/bold green]",
            console=console,
        )
        response: str = await asyncio.to_thread(prompt_func)

        if not response.strip():
            return ActionResult.fail("No guidance provided")

        console.print("[dim]Continuing with your guidance...[/dim]\n")

        return ActionResult.ok(
            f"Human guidance received: {response}",
            human_response=response,
            guidance=response,
        )
    except (KeyboardInterrupt, EOFError):
        return ActionResult.fail("Human input cancelled")


@action("Search the web using Google")
async def search(
    query: str,
    session: "BrowserSession",
) -> ActionResult:
    """
    Search the web using Google.

    Args:
        query: Search query string
    """
    import urllib.parse

    search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"

    try:
        await session.navigate(search_url)
        return ActionResult.ok(
            f"Searched for: {query}",
            query=query,
            url=search_url,
        )
    except Exception as e:
        return ActionResult.fail(f"Search failed: {e}")


@action("Select an option from a dropdown by value or text")
async def select_option(
    index: int,
    value: str,
    session: "BrowserSession",
    dom_state: "SerializedDOM",
) -> ActionResult:
    """
    Select an option from a dropdown/select element.

    Args:
        index: Element index of the select dropdown
        value: Value or visible text of the option to select
    """
    if index not in dom_state.selector_map:
        return ActionResult.fail(f"Invalid element index: {index}")

    element_info = dom_state.selector_map[index]
    backend_node_id = element_info["backend_node_id"]

    from heimdall.browser.element import Element

    element = Element(session, backend_node_id)

    try:
        await element.scroll_into_view()
        selected_text = await element.select_option(value)
        return ActionResult.ok(f"Selected '{selected_text}' from dropdown {index}")

    except Exception as e:
        return ActionResult.fail(f"Select option failed: {e}")


@action("Focus on an element (useful before typing)")
async def focus(
    index: int,
    session: "BrowserSession",
    dom_state: "SerializedDOM",
) -> ActionResult:
    """
    Focus on an element. Useful for focusing input fields before typing.

    Args:
        index: Element index to focus
    """
    from heimdall.browser.element import Element

    if index not in dom_state.selector_map:
        return ActionResult.fail(f"Invalid element index: {index}")

    element_info = dom_state.selector_map[index]
    backend_node_id = element_info["backend_node_id"]

    element = Element(session, backend_node_id)

    try:
        await element.scroll_into_view()
        await element.focus()
        return ActionResult.ok(f"Focused element {index}")
    except Exception as e:
        return ActionResult.fail(f"Focus failed: {e}")


@action("Go forward in browser history")
async def go_forward(session: "BrowserSession") -> ActionResult:
    """Go forward in browser history using CDP."""
    try:
        # Get navigation history
        result = await session.cdp_client.send.Page.getNavigationHistory(
            session_id=session.session_id,
        )
        current_index = result.get("currentIndex", 0)
        entries = result.get("entries", [])

        if current_index >= len(entries) - 1:
            return ActionResult.fail("Cannot go forward: already at last page")

        # Navigate to next entry
        next_entry = entries[current_index + 1]
        await session.cdp_client.send.Page.navigateToHistoryEntry(
            {"entryId": next_entry["id"]},
            session_id=session.session_id,
        )
        await session.wait_for_stable()

        return ActionResult.ok("Went forward")
    except Exception as e:
        return ActionResult.fail(f"Go forward failed: {e}")


# ===== Tab Management Actions =====


@action("Open a new browser tab")
async def new_tab(
    session: "BrowserSession",
    url: str = "about:blank",
) -> ActionResult:
    """
    Open a new browser tab.

    Args:
        url: URL to open in the new tab (default: about:blank)
    """
    try:
        tab_info = await session.create_tab(url)
        return ActionResult.ok(
            f"Opened new tab: {url}",
            tab_id=tab_info.target_id,
            url=url,
        )
    except Exception as e:
        return ActionResult.fail(f"Failed to open new tab: {e}")


@action("Switch to a different browser tab by index")
async def switch_tab(
    tab_index: int,
    session: "BrowserSession",
) -> ActionResult:
    """
    Switch to a different browser tab.

    Args:
        tab_index: Index of the tab to switch to (0-based, from get_tabs list)
    """
    try:
        tabs = session.get_tabs()
        if tab_index < 0 or tab_index >= len(tabs):
            return ActionResult.fail(
                f"Invalid tab index: {tab_index}. Available tabs: 0-{len(tabs) - 1}"
            )

        target_tab = tabs[tab_index]
        await session.switch_tab(target_tab.target_id)

        return ActionResult.ok(
            f"Switched to tab {tab_index}: {target_tab.url}",
            tab_index=tab_index,
            tab_id=target_tab.target_id,
            url=target_tab.url,
        )
    except Exception as e:
        return ActionResult.fail(f"Failed to switch tab: {e}")


@action("Close a browser tab by index")
async def close_tab(
    tab_index: int,
    session: "BrowserSession",
) -> ActionResult:
    """
    Close a browser tab.

    Args:
        tab_index: Index of the tab to close (0-based, from get_tabs list)
    """
    try:
        tabs = session.get_tabs()
        if tab_index < 0 or tab_index >= len(tabs):
            return ActionResult.fail(
                f"Invalid tab index: {tab_index}. Available tabs: 0-{len(tabs) - 1}"
            )

        if len(tabs) <= 1:
            return ActionResult.fail("Cannot close the last tab")

        target_tab = tabs[tab_index]
        await session.close_tab(target_tab.target_id)

        return ActionResult.ok(
            f"Closed tab {tab_index}: {target_tab.url}",
            closed_tab_index=tab_index,
            closed_url=target_tab.url,
        )
    except Exception as e:
        return ActionResult.fail(f"Failed to close tab: {e}")


@action("List all open browser tabs")
async def get_tabs(
    session: "BrowserSession",
) -> ActionResult:
    """
    Get a list of all open browser tabs.

    Returns information about each tab including index, URL, title, and active status.
    """
    try:
        # Refresh tab info from browser
        tabs = await session.refresh_tabs()

        if not tabs:
            return ActionResult.ok("No tabs open", tabs=[])

        # Format tabs for display
        tab_list = []
        for i, tab in enumerate(tabs):
            tab_list.append(
                {
                    "index": i,
                    "url": tab.url,
                    "title": tab.title,
                    "is_active": tab.is_active,
                    "tab_id": tab.target_id,
                }
            )

        # Build message
        lines = [f"Open tabs ({len(tabs)}):"]
        for t in tab_list:
            active_marker = " [ACTIVE]" if t["is_active"] else ""
            title = t["title"] or "(no title)"
            lines.append(f"  [{t['index']}] {title} - {t['url']}{active_marker}")

        return ActionResult.ok(
            "\n".join(lines),
            tabs=tab_list,
            count=len(tabs),
        )
    except Exception as e:
        return ActionResult.fail(f"Failed to get tabs: {e}")
