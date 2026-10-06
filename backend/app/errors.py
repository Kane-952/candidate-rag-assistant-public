class ServiceError(Exception):
    """Only safe, user-facing messages belong here (never raw provider errors)."""


class IndexUnavailable(ServiceError):
    pass


class ModelUnavailable(ServiceError):
    pass


class LLMUnavailable(ServiceError):
    pass


class RequestCancelled(ServiceError):
    pass
