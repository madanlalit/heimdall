"""
Heimdall Exceptions.

Centralized exception hierarchy for the application.
"""

class HeimdallError(Exception):
    """Base exception for all Heimdall errors."""
    pass


class ConfigurationError(HeimdallError):
    """Raised when configuration is invalid or missing."""
    pass


class BrowserError(HeimdallError):
    """Raised when browser operations fail."""
    pass


class LLMError(HeimdallError):
    """Raised when LLM communication fails."""
    pass


class ActionError(HeimdallError):
    """Raised when an action execution fails."""
    pass


class DOMError(HeimdallError):
    """Raised when DOM operations fail."""
    pass
