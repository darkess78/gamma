from __future__ import annotations


class GammaError(RuntimeError):
    """Base application error for Gamma."""


class ConfigurationError(GammaError):
    """Raised when runtime configuration is invalid or incomplete."""


class ExternalServiceError(GammaError):
    """Raised when an external dependency fails or returns unusable data."""


class ContextOverflowError(ExternalServiceError):
    """Raised when a provider rejects an otherwise valid oversized prompt."""


class ContextBudgetError(ConfigurationError):
    """Raised when mandatory prompt layers cannot fit a model budget."""


class ConversationError(GammaError):
    """Raised when the conversation pipeline cannot complete a response."""
