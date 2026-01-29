"""
Agent Factory.

Helpers for creating agent components.
"""

from typing import TYPE_CHECKING

from heimdall.agent.llm import AnthropicLLM, OpenAILLM
from heimdall.config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    LLMProvider,
)

if TYPE_CHECKING:
    from heimdall.agent.llm import BaseLLM


def create_llm_client(provider: LLMProvider, model: str | None = None) -> "BaseLLM":
    """
    Create LLM client based on provider and model.

    Args:
        provider: 'openai', 'anthropic', 'openrouter', or 'groq'
        model: Optional model name override

    Returns:
        Configured LLM client
    """
    if provider == "anthropic":
        return AnthropicLLM(model=model or DEFAULT_ANTHROPIC_MODEL)
    elif provider == "openrouter":
        # Import here to avoid circular dependencies or if it's not always available
        from heimdall.agent.llm import OpenRouterLLM

        return OpenRouterLLM(model=model or DEFAULT_OPENROUTER_MODEL)
    elif provider == "groq":
        from heimdall.agent.llm import GroqLLM

        return GroqLLM(model=model or DEFAULT_GROQ_MODEL)
    else:
        return OpenAILLM(model=model or DEFAULT_OPENAI_MODEL)
