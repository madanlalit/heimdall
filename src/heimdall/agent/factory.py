"""
Agent Factory.

Helpers for creating agent components.
"""
from typing import TYPE_CHECKING

from heimdall.agent.llm import AnthropicLLM, OpenAILLM
from heimdall.config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
)

if TYPE_CHECKING:
    from heimdall.agent.llm import BaseLLM

def create_llm_client(
    provider: str,
    model: str | None = None
) -> "BaseLLM":
    """
    Create LLM client based on provider and model.
    
    Args:
        provider: 'openai', 'anthropic', or 'openrouter'
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
    else:
        return OpenAILLM(model=model or DEFAULT_OPENAI_MODEL)
