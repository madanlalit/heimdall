"""
Agent Factory.

Helpers for creating agent components.
"""

import os
from importlib.util import find_spec
from typing import TYPE_CHECKING

from heimdall.agent.llm import AnthropicLLM, OpenAILLM
from heimdall.config import (
    DEFAULT_ANTHROPIC_MODEL,
    DEFAULT_BEDROCK_MODEL,
    DEFAULT_GOOGLE_MODEL,
    DEFAULT_GROQ_MODEL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENROUTER_MODEL,
    LLMProvider,
)

if TYPE_CHECKING:
    from heimdall.agent.llm import BaseLLM


def _module_available(module_name: str) -> bool:
    """Return True if an optional dependency module can be imported."""
    return find_spec(module_name) is not None


def _resolve_auto_provider() -> LLMProvider:
    """
    Resolve provider from installed SDKs and env keys.

    Priority:
      1) Providers that have both their API credentials and SDK installed
      2) First available installed SDK fallback
    """
    if os.getenv("OPENROUTER_API_KEY") and _module_available("openai"):
        return "openrouter"
    if os.getenv("OPENAI_API_KEY") and _module_available("openai"):
        return "openai"
    if os.getenv("ANTHROPIC_API_KEY") and _module_available("anthropic"):
        return "anthropic"
    if os.getenv("GOOGLE_API_KEY") and _module_available("google.genai"):
        return "google"
    if os.getenv("GROQ_API_KEY") and _module_available("groq"):
        return "groq"
    if _module_available("boto3") and (
        os.getenv("AWS_ACCESS_KEY_ID")
        or os.getenv("AWS_PROFILE")
        or os.getenv("AWS_REGION")
        or os.getenv("AWS_DEFAULT_REGION")
    ):
        return "bedrock"

    # Fallback to any installed provider SDK
    if _module_available("openai"):
        return "openai"
    if _module_available("anthropic"):
        return "anthropic"
    if _module_available("google.genai"):
        return "google"
    if _module_available("groq"):
        return "groq"
    if _module_available("boto3"):
        return "bedrock"

    raise ImportError(
        "No LLM provider SDK is installed. Install one with: "
        'pip install "heimdall[openai]" or "heimdall[openrouter]" or '
        '"heimdall[anthropic]" or "heimdall[google]" or "heimdall[groq]" or '
        '"heimdall[bedrock]".'
    )


def create_llm_client(provider: LLMProvider, model: str | None = None) -> "BaseLLM":
    """
    Create LLM client based on provider and model.

    Args:
        provider: 'auto', 'openai', 'anthropic', 'openrouter', 'google', 'groq', or 'bedrock'
        model: Optional model name override

    Returns:
        Configured LLM client
    """
    resolved_provider = _resolve_auto_provider() if provider == "auto" else provider

    if resolved_provider == "anthropic":
        return AnthropicLLM(model=model or DEFAULT_ANTHROPIC_MODEL)
    elif resolved_provider == "openrouter":
        # Import here to avoid circular dependencies or if it's not always available
        from heimdall.agent.llm import OpenRouterLLM

        return OpenRouterLLM(model=model or DEFAULT_OPENROUTER_MODEL)
    elif resolved_provider == "google":
        from heimdall.agent.llm import GoogleLLM

        return GoogleLLM(model=model or DEFAULT_GOOGLE_MODEL)
    elif resolved_provider == "groq":
        from heimdall.agent.llm import GroqLLM

        return GroqLLM(model=model or DEFAULT_GROQ_MODEL)
    elif resolved_provider == "bedrock":
        from heimdall.agent.llm import BedrockLLM

        return BedrockLLM(model=model or DEFAULT_BEDROCK_MODEL)
    else:
        return OpenAILLM(model=model or DEFAULT_OPENAI_MODEL)
