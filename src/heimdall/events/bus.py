"""
EventBus - Lightweight pub/sub event system for Heimdall.

A simple, async-compatible event bus for loose coupling between components.
Does NOT use bubus - this is our own ~100 line implementation.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar
from uuid import uuid4

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class Event:
    """Base class for all events."""

    id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def event_type(self) -> str:
        """Get event type name."""
        return self.__class__.__name__


class EventBus:
    """
    Lightweight async event bus.

    Usage:
        bus = EventBus()

        # Subscribe
        async def on_navigate(event: NavigationEvent):
            print(f"Navigating to {event.url}")

        bus.on(NavigationEvent, on_navigate)

        # Emit
        await bus.emit(NavigationEvent(url="https://example.com"))
    """

    def __init__(self):
        self._handlers: dict[type, list[Callable]] = defaultdict(list)
        self._once_handlers: dict[type, list[Callable]] = defaultdict(list)

    def on(self, event_type: type[T], handler: Callable[[T], Any]) -> Callable:
        """
        Register event handler.

        Args:
            event_type: Type of event to handle
            handler: Async or sync callable

        Returns:
            The handler (for decorator usage)
        """
        self._handlers[event_type].append(handler)
        logger.debug(f"Registered handler for {event_type.__name__}")
        return handler

    def once(self, event_type: type[T], handler: Callable[[T], Any]) -> Callable:
        """
        Register one-time event handler.

        Args:
            event_type: Type of event to handle
            handler: Async or sync callable

        Returns:
            The handler (for decorator usage)
        """
        self._once_handlers[event_type].append(handler)
        return handler

    def off(self, event_type: type[T], handler: Callable[[T], Any]) -> None:
        """
        Remove event handler.

        Args:
            event_type: Type of event
            handler: Handler to remove
        """
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)
        if handler in self._once_handlers[event_type]:
            self._once_handlers[event_type].remove(handler)

    async def emit(self, event: T) -> list[Any]:
        """
        Emit event to all registered handlers.

        Args:
            event: Event instance to emit

        Returns:
            List of handler results
        """
        event_type = type(event)
        results = []

        # Get all handlers (regular + once)
        handlers = list(self._handlers.get(event_type, []))
        once_handlers = list(self._once_handlers.get(event_type, []))

        # Clear once handlers before calling (prevents re-entry issues)
        if once_handlers:
            self._once_handlers[event_type] = []

        all_handlers = handlers + once_handlers

        if not all_handlers:
            logger.debug(f"No handlers for {event_type.__name__}")
            return results

        logger.debug(f"Emitting {event_type.__name__} to {len(all_handlers)} handlers")

        for handler in all_handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(event)
                else:
                    result = handler(event)
                results.append(result)
            except Exception as e:
                logger.error(f"Error in handler for {event_type.__name__}: {e}")
                # Continue with other handlers

        return results

    def emit_sync(self, event: T) -> None:
        """
        Schedule event emission without waiting.

        Args:
            event: Event instance to emit
        """
        asyncio.create_task(self.emit(event))

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()
        self._once_handlers.clear()
        logger.debug("Cleared all event handlers")

    @property
    def handler_count(self) -> int:
        """Total number of registered handlers."""
        return sum(len(h) for h in self._handlers.values()) + sum(
            len(h) for h in self._once_handlers.values()
        )
