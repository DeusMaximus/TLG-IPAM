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
