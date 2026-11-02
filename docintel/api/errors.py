"""One error shape for the whole API: {"error": {"code": ..., "message": ..., "details": ...}}.

`code` is stable and meant for programs, `message` is written for the person using the screen.
Nothing internal (paths, stack traces, SQL) is ever put in a response; those go to the log with a request id.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("docintel.api")

_STATUS_CODES = {
    400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED", 409: "CONFLICT",
    410: "GONE", 413: "TOO_LARGE", 415: "UNSUPPORTED_MEDIA_TYPE", 422: "VALIDATION_ERROR", 429: "RATE_LIMITED", 503: "UNAVAILABLE",
}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.headers = status, code, message, details, headers


def error_body(code: str, message: str, details: dict | None = None) -> dict:
    err: dict = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"error": err}


def _field_name(loc: tuple) -> str:
    return ".".join(str(p) for p in loc if p not in ("body", "query", "path", "header"))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(error_body(exc.code, exc.message, exc.details), status_code=exc.status, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            body = error_body(str(detail["code"]), str(detail.get("message", "")))
        else:
            default = {404: "Not found.", 405: "That method is not allowed here."}.get(exc.status_code)
            body = error_body(_STATUS_CODES.get(exc.status_code, "ERROR"), default or (str(detail) if isinstance(detail, str) else "Request failed."))
        return JSONResponse(body, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        fields = [{"field": _field_name(tuple(e.get("loc", ()))), "message": str(e.get("msg", "Invalid value"))} for e in exc.errors()][:20]
        first = fields[0] if fields else {"field": "", "message": "Invalid request."}
        msg = f"{first['field']}: {first['message']}" if first["field"] else first["message"]
        return JSONResponse(error_body("VALIDATION_ERROR", msg, {"fields": fields}), status_code=422)

    @app.exception_handler(OperationalError)
    async def _db_busy(request: Request, exc: OperationalError):
        text = str(getattr(exc, "orig", exc)).lower()
        if "locked" in text or "busy" in text:
            log.warning("database busy on %s %s", request.method, request.url.path)
            return JSONResponse(error_body("BUSY", "The database is busy. Please try again in a moment."), status_code=503, headers={"Retry-After": "1"})
        return _internal(request, exc)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        return _internal(request, exc)


def _internal(request: Request, exc: Exception) -> JSONResponse:
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    log.error("unhandled error [%s] on %s %s", rid, request.method, request.url.path, exc_info=exc)
    return JSONResponse(error_body("INTERNAL_ERROR", f"Something went wrong on the server. Reference: {rid}."), status_code=500)
