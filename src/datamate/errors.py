"""Typed failures at trust boundaries. Never serialize exception text as an answer."""


class DatamateError(Exception):
    """Base class for expected, recoverable agent failures."""


class InvalidInput(DatamateError):
    pass


class DataQualityError(DatamateError):
    pass


class UnsafeProgram(DatamateError):
    pass


class QueryTimeout(DatamateError):
    pass


class ResourceExceeded(DatamateError):
    pass


class ModelUnavailable(DatamateError):
    pass


class InvalidModelOutput(DatamateError):
    pass


class OfficialRulesUnverified(DatamateError):
    pass


class OutputValidationError(DatamateError):
    pass
