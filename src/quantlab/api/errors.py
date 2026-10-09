"""Fixed HTTP diagnostics. Exception messages and input values never cross HTTP."""
from .schemas import ErrorResponse


MESSAGES = {
    "unauthenticated": "A valid bearer credential is required.",
    "forbidden": "Trusted operator authority is required.",
    "invalid_request": "Input violates the API contract.",
    "unsupported_media_type": "Use uncompressed application/json.",
    "request_too_large": "Request exceeds the byte limit.",
    "service_busy": "The bounded application service is busy; retry later.",
    "not_ready": "The application is not ready.",
    "service_unavailable": "The reporting service is not configured.",
    "not_found": "The requested resource does not exist.",
    "idempotency_conflict": "The idempotency identity conflicts with retained content.",
    "operation_capacity": "The operation namespace is full.",
    "operation_not_cancellable": "Only queued operations can be cancelled.",
    "operation_not_executable": "The operation cannot be executed in its current state.",
    "result_unavailable": "A completed operation is required for result access.",
    "recovery_required": "The owner requires trusted local recovery.",
    "query_rejected": "The reporting owner rejected the query.",
    "response_too_large": "The report exceeds the HTTP serialization bound; reduce the query page where possible.",
    "internal_error": "The application could not complete the request.",
    "http_error": "The HTTP request was rejected.",
}


class APIError(Exception):
    def __init__(self, status: int, code: str, issues=()):
        self.status, self.code, self.issues = status, code, issues
        super().__init__(MESSAGES[code])


def error_response(status, code, request_id, issues=()):
    from starlette.responses import Response
    value = ErrorResponse(code=code, message=MESSAGES[code], request_id=request_id, issues=issues)
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else {}
    return Response(value.model_dump_json(), status_code=status, media_type="application/json", headers=headers)
