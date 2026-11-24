"""Errores en formato application/problem+json (RFC 9457)."""
from http import HTTPStatus

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .models import Problem, ValidationIssue

PROBLEM_MEDIA_TYPE = "application/problem+json"
PROBLEM_BASE = "https://api.example.com/problems/"  # Pendiente: dominio definitivo

PROBLEM_TYPES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not-found",
    422: "validation-error",
    429: "rate-limited",
}


def problem_response(status: int, detail: str | None, instance: str, headers: dict | None = None,
                     errors: list[ValidationIssue] | None = None) -> JSONResponse:
    slug = PROBLEM_TYPES.get(status)
    body = Problem(
        type=f"{PROBLEM_BASE}{slug}" if slug else "about:blank",
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=instance,
        errors=errors,
    )
    return JSONResponse(body.model_dump(exclude_none=True), status_code=status, headers=headers,
                        media_type=PROBLEM_MEDIA_TYPE)


def _pointer(loc: tuple) -> str:
    # ("body", "email") -> "/email"; ("path", "conversation_id") -> "path/conversation_id"
    source, *rest = loc
    path = "/".join(str(p) for p in rest)
    return f"/{path}" if source == "body" else f"{source}/{path}"


def _message(err: dict) -> str:
    # Nunca se devuelve el valor recibido (`input`): podría contener el email del alumno
    return err["msg"]


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else None
        return problem_response(exc.status_code, detail, request.url.path, getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        issues = [ValidationIssue(pointer=_pointer(tuple(e["loc"])), message=_message(e)) for e in exc.errors()]
        return problem_response(422, "La petición no es válida.", request.url.path, errors=issues)
