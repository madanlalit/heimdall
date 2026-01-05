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


# Retry configuration
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
                # Not a retryable error, return immediately
                return result

            last_error = result.error

        except Exception as e:
            last_error = str(e)

        # Wait before retry with exponential backoff
        if attempt < max_retries:
            wait_time = delay * (2**attempt)
            logger.debug(
                f"Retry {attempt + 1}/{max_retries} for {element_context} "
                f"after {wait_time:.1f}s (error: {last_error})"
            )
            await asyncio.sleep(wait_time)

    # All retries exhausted
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

    # Check if URL is allowed
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
    """Go back in browser history."""
    try:
        await session.execute_js("window.history.back()")
        await asyncio.sleep(0.5)

        return ActionResult.ok("Went back")
    except Exception as e:
        return ActionResult.fail(f"Go back failed: {e}")


@action("Refresh/reload the current page")
async def refresh_page(session: "BrowserSession") -> ActionResult:
    """Refresh the current page to get fresh content."""
    try:
        await session.execute_js("window.location.reload()")
        await asyncio.sleep(1.0)  # Wait for page to reload

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

    # Display the agent's question prominently
    console.print()
    console.print(
        Panel(
            f"[bold yellow]🤖 Agent needs help:[/bold yellow]\n\n{question}",
            title="[bold blue]Human Input Required[/bold blue]",
            border_style="blue",
        )
    )

    # Get human response
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
    """Go forward in browser history."""
    try:
        await session.execute_js("window.history.forward()")
        await asyncio.sleep(0.5)
        return ActionResult.ok("Went forward")
    except Exception as e:
        return ActionResult.fail(f"Go forward failed: {e}")
