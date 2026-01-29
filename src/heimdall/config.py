"""
Heimdall Configuration.

Centralizes default values and configuration settings.
"""

from typing import Literal

# Default LLM Models
DEFAULT_OPENAI_MODEL = "gpt-4"
DEFAULT_ANTHROPIC_MODEL = "claude-3-5-sonnet-20241022"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"

# Browser Configuration
DEFAULT_BROWSER_WIDTH = 1280
DEFAULT_BROWSER_HEIGHT = 800
DEFAULT_TIMEOUT_MS = 30000

# Agent Defaults
DEFAULT_MAX_STEPS = 50
DEFAULT_MAX_RETRIES = 3
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5

LLMProvider = Literal["openai", "anthropic", "openrouter", "groq"]
