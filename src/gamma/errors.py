from __future__ import annotations


class GammaError(RuntimeError):
    """Base application error for Gamma."""


class ConfigurationError(GammaError):
    """Raised when runtime configuration is invalid or incomplete."""


class ExternalServiceError(GammaError):
    """Raised when an external dependency fails or returns unusable data."""


class ConversationError(GammaError):
    """Raised when the conversation pipeline cannot complete a response."""
