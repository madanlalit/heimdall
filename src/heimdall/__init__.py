"""
Heimdall - LLM-powered browser automation agent.

A robust agent that executes browser automation tasks via
LLM-driven interaction using Chrome DevTools Protocol.

Usage:
    from heimdall import BrowserSession, Agent, DomService
    from heimdall.agent.llm import OpenAILLM
    from heimdall.tools import registry

    async with BrowserSession() as session:
        await session.navigate("https://example.com")

        llm = OpenAILLM()
        dom_service = DomService(session)
        agent = Agent(session, dom_service, registry, llm)

        result = await agent.run("Click the login button")
"""

__version__ = "0.1.0"

from heimdall.agent import Agent, AgentConfig, AgentState
from heimdall.browser import BrowserConfig, BrowserSession, Element
from heimdall.dom import DomService, SerializedDOM
from heimdall.events import Event, EventBus
from heimdall.logging import logger, setup_logging
from heimdall.tools import ActionResult, action, registry

__all__ = [
    "__version__",
    "Agent",
    "BrowserSession",
    "BrowserConfig",
    "Element",
    "DomService",
    "SerializedDOM",
    "AgentConfig",
    "AgentState",
    "registry",
    "action",
    "ActionResult",
    "EventBus",
    "Event",
    "setup_logging",
    "logger",
]
