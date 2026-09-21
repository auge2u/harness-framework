"""Harness-specific exception hierarchy."""


class HarnessError(Exception):
    """Base exception for all harness errors."""
    pass


class ConfigValidationError(HarnessError):
    """Raised when harness configuration is invalid."""

    def __init__(self, message: str, errors: list = None):
        super().__init__(message)
        self.errors = errors or []


class PluginNotFoundError(HarnessError):
    """Raised when a requested plugin is not found in the registry."""
    pass


class PluginAlreadyRegisteredError(HarnessError):
    """Raised when attempting to register a plugin with a duplicate name."""
    pass


class ScenarioError(HarnessError):
    """Raised when scenario setup, execution, or teardown fails."""
    pass


class VerifierError(HarnessError):
    """Raised when a verifier encounters an error during verification."""
    pass


class LineageError(HarnessError):
    """Raised when lineage operations fail."""
    pass


class BudgetExceededError(HarnessError):
    """Raised when the cost budget is exceeded."""
    pass


class RateLimitExceededError(HarnessError):
    """Raised when a rate limit is exceeded."""
    pass


class SourceBlockedError(HarnessError):
    """Raised when a data source is blocked."""
    pass


class SourceNotFoundError(HarnessError):
    """Raised when a requested data source is not registered."""
    pass
