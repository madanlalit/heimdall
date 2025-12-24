"""
Simple test script for Heimdall browser automation.

Run with:
    python examples/test_basic.py
"""

import asyncio
import os

from heimdall.browser import BrowserConfig, BrowserSession
from heimdall.dom import DomService
from heimdall.events import Event, EventBus
from heimdall.logging import setup_logging


async def test_browser_session():
    """Test basic browser session functionality."""
    print("\n=== Testing Browser Session ===\n")

    import tempfile

    # Configure logging
    setup_logging(level="DEBUG")

    # Create temp dir for Chrome profile (avoid conflicts with running Chrome)
    temp_dir = tempfile.mkdtemp(prefix="heimdall_chrome_")

    # Create browser config (headed so you can see it)
    config = BrowserConfig(
        headless=False,  # Set to True for headless
        window_size=(1280, 800),
        user_data_dir=temp_dir,  # Use temp dir to avoid profile conflicts
    )

    # Create and start session
    session = BrowserSession(config=config)

    try:
        print("Starting browser...")
        await session.start()
        print(f"✓ Browser started")

        # Navigate to a page
        print("\nNavigating to example.com...")
        await session.navigate("https://chatgpt.com")

        # Get page info
        url = await session.get_url()
        title = await session.get_title()
        print(f"✓ URL: {url}")
        print(f"✓ Title: {title}")

        # Take screenshot
        print("\nTaking screenshot...")
        screenshot_data = await session.screenshot()
        print(f"✓ Screenshot size: {len(screenshot_data)} bytes")

        # Test DOM service
        print("\nExtracting DOM...")
        dom_service = DomService(session)
        dom_state = await dom_service.get_state()
        print(f"✓ Found {dom_state.element_count} interactive elements")
        print(f"\nDOM Elements:\n{dom_state.text[:500]}...")

        # Execute some JS
        print("\nExecuting JavaScript...")
        viewport = await session.execute_js(
            "({ width: window.innerWidth, height: window.innerHeight })"
        )
        print(f"✓ Viewport: {viewport}")

        print("\n=== All tests passed! ===\n")

        # Keep browser open for a moment to see results
        await asyncio.sleep(3)

    finally:
        print("Stopping browser...")
        await session.stop()
        print("✓ Browser stopped")


async def test_event_bus():
    """Test EventBus functionality."""
    print("\n=== Testing EventBus ===\n")

    from heimdall.events import EventBus, Event
    from heimdall.events.types import NavigationStartedEvent, NavigationCompletedEvent

    bus = EventBus()
    received_events = []

    # Register handler
    async def on_navigation(event: NavigationStartedEvent):
        received_events.append(event)
        print(f"  → Received event: {event.event_type}")

    bus.on(NavigationStartedEvent, on_navigation)

    # Emit event
    print("Emitting NavigationStartedEvent...")
    await bus.emit(NavigationStartedEvent(url="https://chatgpt.com"))

    assert len(received_events) == 1
    print(f"✓ Event received correctly")

    print("\n=== EventBus tests passed! ===\n")


async def test_tools_registry():
    """Test Tools Registry functionality."""
    print("\n=== Testing Tools Registry ===\n")

    from heimdall.tools import registry

    # Get schema
    schema = registry.schema()
    print(f"Registered actions: {len(schema)}")

    for tool in schema:
        name = tool["function"]["name"]
        desc = tool["function"]["description"][:50]
        print(f"  • {name}: {desc}...")

    print(f"\n✓ {len(schema)} actions registered")
    print("\n=== Tools Registry tests passed! ===\n")


async def main():
    """Run all tests."""
    print("=" * 50)
    print("  HEIMDALL - Test Suite")
    print("=" * 50)

    # Test EventBus (no browser needed)
    await test_event_bus()

    # Test Tools Registry (no browser needed)
    await test_tools_registry()

    # Test Browser Session (requires Chrome)
    await test_browser_session()


if __name__ == "__main__":
    asyncio.run(main())
