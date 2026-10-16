import logging
import re
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import APIError
from .routes import router

app = FastAPI(title="ContribCompass API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(router, prefix="/api")
logger = logging.getLogger("contribcompass.api")


@app.middleware("http")
async def request_id_middleware(request, call_next):
    supplied = request.headers.get("X-Request-ID", "")
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", supplied) else str(uuid.uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


def _error_response(request, status_code: int, code: str, message: str, retryable: bool = False, retry_at: str | None = None):
    error = {"code": code, "message": message, "retryable": retryable, "request_id": getattr(request.state, "request_id", "unknown")}
    if retry_at:
        error["retry_at"] = retry_at
    return JSONResponse(status_code=status_code, content={"error": error}, headers={"X-Request-ID": error["request_id"]})


@app.exception_handler(APIError)
async def api_error_handler(request, exc: APIError):
    logger.warning("API request failed", extra={"request_id": getattr(request.state, "request_id", "unknown"), "endpoint": request.url.path, "repository": getattr(request.state, "repository", None), "error_code": exc.code, "status_code": exc.status_code})
    return _error_response(request, exc.status_code, exc.code, exc.message, exc.retryable, exc.retry_at)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc: RequestValidationError):
    return _error_response(request, 422, "INVALID_REQUEST", "The request is invalid. Check the submitted fields.")


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request, exc: StarletteHTTPException):
    status_map = {404: ("NOT_FOUND", "The requested API resource was not found."), 405: ("METHOD_NOT_ALLOWED", "This method is not supported for the requested endpoint.")}
    code, message = status_map.get(exc.status_code, ("REQUEST_FAILED", "The request could not be completed."))
    headers = {"X-Request-ID": getattr(request.state, "request_id", "unknown")}
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message, "retryable": exc.status_code >= 500, "request_id": headers["X-Request-ID"]}}, headers=headers)


@app.exception_handler(Exception)
async def unexpected_error_handler(request, exc: Exception):
    request_id = getattr(request.state, "request_id", "unknown")
    logger.exception("Unhandled API exception", extra={"request_id": request_id, "endpoint": request.url.path, "repository": getattr(request.state, "repository", None), "exception_type": type(exc).__name__})
    return _error_response(request, 500, "INTERNAL_SERVER_ERROR", "Unable to analyze this repository.", retryable=True)


@app.get("/")
async def root():
    return {"name": "ContribCompass", "docs": "/docs"}
