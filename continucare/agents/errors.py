"""Expected failures at the controlled agent boundary."""


class AgentError(RuntimeError):
    """Base class for failures that may use a safe fallback."""


class AgentNotRegisteredError(AgentError):
    pass


class AgentTimeoutError(AgentError):
    pass


class AgentToolDeniedError(AgentError):
    pass


class ModelNotConfiguredError(AgentError):
    pass


class ModelRequestError(AgentError):
    """The provider request failed without exposing credentials or response bodies."""


class ModelResponseError(AgentError):
    """The provider returned data that did not satisfy the local contract."""
