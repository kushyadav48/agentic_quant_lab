"""Factory and explicit lifespan. No MCP transport, worker loop or import-time I/O."""
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from quantlab.journal import JournalError, JournalRecoveryRequired
from quantlab.mcp.operations import OperationInputError
from quantlab.persistence.contracts import RecoveryRequired

from .config import APISettings
from .dependencies import INPUT_SCHEMAS
from .errors import APIError, error_response
from .routes import router
from .schemas import ErrorResponse, Health
from .security import BoundaryMiddleware, require_reader, logger
from .services import ServiceLane, default_services


def create_app(settings: APISettings | None = None, *, services_factory=None) -> FastAPI:
    settings = APISettings.from_env() if settings is None else APISettings.model_validate(settings)

    @asynccontextmanager
    async def lifespan(app):
        lane = ServiceLane()
        app.state.lane, app.state.ready = lane, False
        try:
            try:
                await lane.start(lambda: (default_services(settings) if services_factory is None else services_factory()))
            except Exception:
                logger.error('{"event":"startup_failed"}')
                raise RuntimeError("API startup failed; inspect trusted local configuration and recovery.") from None
            app.state.ready = True
            logger.info('{"event":"startup_ready"}')
            yield
        finally:
            app.state.ready = False
            try:
                await lane.close()
            except Exception:
                logger.error('{"event":"shutdown_failed"}')
                raise RuntimeError("API shutdown failed; trusted local recovery may be required.") from None
            finally:
                app.state.lane = None
            logger.info('{"event":"shutdown_complete"}')

    app = FastAPI(title="Agentic Quant Lab local API", version="1.0.0", lifespan=lifespan,
        debug=False, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.ready = settings, False
    responses = {code: {"model": ErrorResponse} for code in (400, 401, 403, 404, 409, 413, 415, 422, 500, 503)}
    app.include_router(router, responses=responses)

    @app.get("/api/v1/health", response_model=Health, responses=responses)
    async def health():
        return Health()

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            schema.setdefault("components", {}).setdefault("schemas", {}).update(INPUT_SCHEMAS)
            # Core patterns use Rust/Python's end anchor; OpenAPI clients use ECMA regex.
            def portable_patterns(value):
                if isinstance(value, dict):
                    if "pattern" in value:
                        value["pattern"] = value["pattern"].replace(r"\z", "$")
                    for child in value.values():
                        portable_patterns(child)
                elif isinstance(value, list):
                    for child in value:
                        portable_patterns(child)
            portable_patterns(schema)
            app.openapi_schema = schema
        return app.openapi_schema
    app.openapi = openapi

    @app.get("/openapi.json", dependencies=[Depends(require_reader)], include_in_schema=False)
    async def openapi_document():
        return JSONResponse(app.openapi())

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError):
        return error_response(exc.status, exc.code, request.state.request_id, exc.issues)

    @app.exception_handler(OperationInputError)
    async def operation_error(request: Request, exc: OperationInputError):
        code = "not_found" if exc.code == "operation_not_found" else exc.code
        status = 404 if code == "not_found" else 503 if code == "operation_capacity" else 409
        return error_response(status, code, request.state.request_id)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "invalid_request", request.state.request_id)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(exc.status_code, "not_found" if exc.status_code == 404 else "http_error",
            request.state.request_id)

    async def recovery_error(request: Request, exc):
        return error_response(503, "recovery_required", request.state.request_id)
    app.add_exception_handler(RecoveryRequired, recovery_error)
    app.add_exception_handler(JournalRecoveryRequired, recovery_error)

    @app.exception_handler(JournalError)
    async def journal_error(request: Request, exc: JournalError):
        return error_response(422, "query_rejected", request.state.request_id)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
            expose_headers=["X-Request-ID"], allow_credentials=False, max_age=600)
    app.add_middleware(BoundaryMiddleware, settings=settings)
    return app
