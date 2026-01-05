"""
LLM Base - Abstract interface for LLM providers.

Defines the interface that all LLM providers must implement.
"""

from abc import ABC, abstractmethod


class BaseLLM(ABC):
    """Abstract base class for LLM providers."""

    @abstractmethod
    async def chat_completion(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        **kwargs,
    ) -> dict:
        """
        Generate chat completion.

        Args:
            messages: Chat messages
            tools: Tool definitions
            tool_choice: Tool selection mode

        Returns:
            Response dict with 'content' and/or 'tool_calls'
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close client connections."""
        pass
