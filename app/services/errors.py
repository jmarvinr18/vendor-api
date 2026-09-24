"""Domain errors raised by the service layer.

Services never build HTTP responses; create_app() registers one handler that maps these
errors to status codes. That keeps the business logic usable from the CLI, jobs and tests.
"""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, message: str, errors: dict | None = None):
        super().__init__(message)
        self.message = message
        self.errors = errors


class Unauthorized(ServiceError):
    status_code = 401


class NotFound(ServiceError):
    status_code = 404


class Conflict(ServiceError):
    status_code = 409


class ValidationFailed(ServiceError):
    status_code = 422


class StorageUnavailable(ServiceError):
    """The document store could not be reached or rejected the request."""

    status_code = 503


class TooManyRequests(ServiceError):
    status_code = 429


class AgentUnavailable(ServiceError):
    """The AI agent service could not be reached or rejected the request."""

    status_code = 503
