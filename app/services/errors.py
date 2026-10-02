"""Domain errors raised by services and translated to HTTP responses in app.main."""


class ServiceError(Exception):
    status_code = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFound(ServiceError):
    status_code = 404


class Forbidden(ServiceError):
    status_code = 403


class Conflict(ServiceError):
    status_code = 409


class RuleViolation(ServiceError):
    """A request that is well-formed but breaks a business rule."""

    status_code = 422
