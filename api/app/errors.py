import logging

from sqlalchemy.exc import OperationalError

log = logging.getLogger(__name__)

# SQLite's primary result codes for another connection holding a lock (the low byte of
# an extended code): contention that clears by itself
_SQLITE_BUSY, _SQLITE_LOCKED = 5, 6


class ApiError(Exception):
    """Domain error rendered as {"error": {"code", "message"}} with the given HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def validation_error(message: str) -> ApiError:
    return ApiError("VALIDATION_ERROR", message, 422)


def not_found(entity: str, entity_id: str) -> ApiError:
    return ApiError("NOT_FOUND", f"{entity} {entity_id} not found", 404)


def conflict(message: str) -> ApiError:
    return ApiError("CONFLICT", message, 409)


def unavailable(message: str) -> ApiError:
    """The server could not complete the request for now (the database is busy, say);
    the same request may succeed if retried."""
    return ApiError("UNAVAILABLE", message, 503)


def database_unavailable(operation: str, exc: OperationalError) -> ApiError:
    """The 503 for a write the database refused, which the caller has rolled back.

    Logged server-side with the operation and SQLite's own error name and message,
    which carry no SQL and no parameter values; neither does this line, so no bearer,
    token hash or agent-reported value reaches the log. The public message says
    "busy" only for lock contention (`SQLITE_BUSY`, `SQLITE_LOCKED`), which clears by
    itself: a read-only file, a full disk or a missing column needs the owner.
    """
    orig = exc.orig
    code = getattr(orig, "sqlite_errorcode", None)
    name = getattr(orig, "sqlite_errorname", None) or type(orig).__name__
    detail = orig.args[0] if getattr(orig, "args", None) else ""
    busy = isinstance(code, int) and code & 0xFF in (_SQLITE_BUSY, _SQLITE_LOCKED)
    log.log(
        logging.WARNING if busy else logging.ERROR,
        "%s: the database refused the write (%s: %s); rolled back, answered 503",
        operation,
        name,
        detail,
    )
    return unavailable(
        "Database busy; retry the request" if busy else "Database unavailable; retry later"
    )


def internal_error(operation: str, exc: Exception) -> ApiError:
    """A failure the server caught before committing anything, answered in the REST
    error shape rather than as a bare 500. Logged with the operation and the
    exception's type only: its text may quote what the agent sent."""
    log.error(
        "%s: failed before the commit (%s); rolled back, answered 500",
        operation,
        type(exc).__name__,
    )
    return ApiError(
        "INTERNAL_ERROR", "The server failed to answer this request; nothing was stored", 500
    )
