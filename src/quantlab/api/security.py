"""Bearer roles, finite ASGI ingress, fixed diagnostics and payload-free logs."""
import json
import logging
from secrets import compare_digest
from time import perf_counter
from uuid import uuid4

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .errors import APIError, error_response

logger = logging.getLogger("quantlab.api")
bearer = HTTPBearer(auto_error=False, scheme_name="LocalBearer")


class SafeServerFormatter(logging.Formatter):
    """Uvicorn may emit formatted tracebacks as messages; never render those."""
    def format(self, record):
        return json.dumps({"event": "asgi_server", "level": record.levelname}, separators=(",", ":"))


async def require_reader(request: Request, credential: HTTPAuthorizationCredentials | None = Depends(bearer)):
    settings = request.app.state.settings
    token = "" if credential is None else credential.credentials
    if not token.isascii():
        raise APIError(401, "unauthenticated")
    operator = compare_digest(token, settings.operator_token.get_secret_value())
    reader = compare_digest(token, "" if settings.reader_token is None else settings.reader_token.get_secret_value())
    if credential is None or (not operator and (settings.reader_token is None or not reader)):
        raise APIError(401, "unauthenticated")
    return "operator" if operator else "reader"


async def require_operator(role: str = Depends(require_reader)):
    if role != "operator":
        raise APIError(403, "forbidden")


class BoundaryMiddleware:
    """Read actual chunks before parsing, including requests without Content-Length."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self._active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started, status, sent = perf_counter(), 500, False

        async def respond(message):
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status, sent = message["status"], True
                message["headers"] = [*message.get("headers", []),
                    (b"x-request-id", request_id.encode("ascii")),
                    (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
            await send(message)

        admitted = self._active < self.settings.max_concurrent_requests
        if admitted:
            self._active += 1
        try:
            if not admitted:
                raise APIError(503, "service_busy")
            headers = {}
            for key, value in scope.get("headers", []):
                headers.setdefault(key.lower(), []).append(value)
            lengths = headers.get(b"content-length", [])
            if len(lengths) > 1 or (lengths and (len(lengths[0]) > 20 or not lengths[0].isdigit())):
                raise APIError(422, "invalid_request")
            if lengths and int(lengths[0]) > self.settings.max_request_bytes:
                raise APIError(413, "request_too_large")
            if b"content-encoding" in headers:
                raise APIError(415, "unsupported_media_type")
            buffer, size = bytearray(), 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    raise APIError(422, "invalid_request")
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.settings.max_request_bytes:
                    raise APIError(413, "request_too_large")
                buffer.extend(chunk)
                if not message.get("more_body", False):
                    break
            body = bytes(buffer)
            delivered = False
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            await self.app(scope, bounded_receive, respond)
        except APIError as exc:
            if not sent:
                await error_response(exc.status, exc.code, request_id, exc.issues)(scope, receive, respond)
        except Exception:
            if not sent:
                await error_response(500, "internal_error", request_id)(scope, receive, respond)
        finally:
            if admitted:
                self._active -= 1
            route = scope.get("route")
            logger.info(json.dumps({"event": "http_request", "request_id": request_id,
                "route": getattr(route, "name", "unmatched"), "status": status,
                "duration_ms": round((perf_counter()-started)*1000, 3)}, separators=(",", ":")))
