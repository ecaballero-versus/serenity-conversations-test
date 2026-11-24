"""Endpoints v1. En la fase 0 responden con los datos ficticios de `mock_data`."""
import base64
import binascii
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException

from . import __version__, mock_data
from .errors import PROBLEM_BASE
from .models import (
    ConversationDetail,
    ConversationSearchRequest,
    ConversationSearchResponse,
    ConversationSummary,
    Health,
    Problem,
    VerticalList,
)
from .security import read_access

router = APIRouter(prefix="/v1")

# --- Documentación de errores ---------------------------------------------------------------------

_ERROR_DOCS = {
    401: ("Falta el token de acceso, ha caducado o no es válido.", "Falta el token de acceso o no es válido."),
    403: ("El token no tiene el scope `conversations:read`.", "El token no tiene el scope conversations:read."),
    404: ("La conversación no existe.", "No existe la conversación."),
    422: ("La petición no supera la validación.", "La petición no es válida."),
    429: ("Se ha superado el límite de peticiones. Reintentar tras los segundos indicados en `Retry-After`.",
          "Demasiadas peticiones."),
}
_SLUGS = {401: "unauthorized", 403: "forbidden", 404: "not-found", 422: "validation-error", 429: "rate-limited"}


def _errors(*codes: int, instance: str, pointer: str = "/email",
            pointer_message: str = "value is not a valid email address") -> dict:
    out = {}
    for code in codes:
        description, detail = _ERROR_DOCS[code]
        example = {"type": f"{PROBLEM_BASE}{_SLUGS[code]}", "title": {401: "Unauthorized", 403: "Forbidden",
                   404: "Not Found", 422: "Unprocessable Content", 429: "Too Many Requests"}[code],
                   "status": code, "detail": detail, "instance": instance}
        if code == 422:
            example["errors"] = [{"pointer": pointer, "message": pointer_message}]
        doc = {"model": Problem, "description": description, "content": {"application/json": {"example": example}}}
        if code == 429:
            doc["headers"] = {"Retry-After": {"description": "Segundos que hay que esperar antes de reintentar.",
                                              "schema": {"type": "integer", "example": 30}}}
        if code == 401:
            doc["headers"] = {"WWW-Authenticate": {"description": "Esquema de autenticación requerido.",
                                                   "schema": {"type": "string", "example": "Bearer"}}}
        out[code] = doc
    return out


# (ruta, método) -> respuestas documentadas. main.py las usa para restaurar los ejemplos en el OpenAPI.
RESPONSE_DOCS: dict[tuple[str, str], dict] = {}


def _documented(path: str, method: str, responses: dict) -> dict:
    RESPONSE_DOCS[(f"/v1{path}", method)] = responses
    return responses


def _dump(model) -> dict:
    return model.model_dump(mode="json")


# --- Cursor opaco (posición en el orden end_date desc, id desc) ----------------------------------


def _encode_cursor(item: ConversationSummary) -> str:
    raw = json.dumps({"e": item.end_date.isoformat(), "i": str(item.id)}).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
        return datetime.fromisoformat(data["e"]), UUID(data["i"])
    except (binascii.Error, ValueError, KeyError, TypeError):
        raise HTTPException(422, "El cursor no es válido.")


# --- Endpoints -----------------------------------------------------------------------------------


@router.get("/health", tags=["Sistema"], operation_id="getHealth", response_model=Health,
            summary="Estado del servicio", description="No requiere autenticación.")
def health() -> Health:
    return Health(status="ok", version=__version__, time=datetime.now(timezone.utc))


@router.get(
    "/verticals", tags=["Catálogo"], operation_id="listVerticals", response_model=VerticalList,
    dependencies=[read_access], summary="Verticales disponibles",
    description="Valores válidos del campo `vertical`. Se añadirán verticales nuevas sin cambiar el contrato.",
    responses=_documented("/verticals", "get", {
        200: {"content": {"application/json": {"example": _dump(VerticalList(items=mock_data.VERTICALS))}}},
        **_errors(401, 403, 429, instance="/v1/verticals")}),
)
def list_verticals() -> VerticalList:
    return VerticalList(items=mock_data.VERTICALS)


_SEARCH_EXAMPLE_ITEMS = [
    ConversationSummary(**d.model_dump(include=set(ConversationSummary.model_fields)))
    for email, d in mock_data.CONVERSATIONS.values()
    if email == "alumna.ejemplo@example.com" and d.vertical == "idiomas"
]
_SEARCH_EXAMPLE_ITEMS.sort(key=lambda c: c.end_date, reverse=True)


@router.post(
    "/students/conversations/search", tags=["Conversaciones"], operation_id="searchStudentConversations",
    response_model=ConversationSearchResponse, dependencies=[read_access],
    summary="Conversaciones de un alumno en una vertical",
    description=(
        "Devuelve las conversaciones del alumno, sin mensajes, ordenadas por última actividad descendente.\n\n"
        "Antes de responder se consulta Serenity para incluir la actividad más reciente del alumno (como "
        "mucho una vez por minuto y alumno). Si Serenity no responde, se devuelven los datos guardados con "
        "`stale: true`.\n\n"
        "Un alumno sin conversaciones devuelve `200` con `items` vacío, no `404`.\n\n"
        "El email va en el cuerpo, y no en la URL, para que no quede registrado en los logs."
    ),
    responses=_documented("/students/conversations/search", "post", {
        200: {"content": {"application/json": {"examples": {
            "primera_pagina": {
                "summary": "Primera página con más resultados",
                "value": _dump(ConversationSearchResponse(
                    items=_SEARCH_EXAMPLE_ITEMS[:2], next_cursor=_encode_cursor(_SEARCH_EXAMPLE_ITEMS[1]),
                    synced_at=mock_data.SYNCED_AT, stale=False)),
            },
            "ultima_pagina": {
                "summary": "Última página",
                "value": _dump(ConversationSearchResponse(
                    items=_SEARCH_EXAMPLE_ITEMS[2:], next_cursor=None, synced_at=mock_data.SYNCED_AT, stale=False)),
            },
            "sin_refrescar": {
                "summary": "Serenity no respondió: datos guardados",
                "value": _dump(ConversationSearchResponse(
                    items=_SEARCH_EXAMPLE_ITEMS[:1], next_cursor=None,
                    synced_at=datetime(2026, 11, 24, 9, 40, tzinfo=timezone.utc), stale=True)),
            },
            "sin_conversaciones": {
                "summary": "Alumno sin conversaciones",
                "value": {"items": [], "next_cursor": None, "synced_at": None, "stale": False},
            },
        }}}},
        **_errors(401, 403, 422, 429, instance="/v1/students/conversations/search"),
    }),
)
def search_student_conversations(
    criteria: Annotated[ConversationSearchRequest, Body(openapi_examples={
        "basica": {"summary": "Búsqueda básica",
                   "value": {"email": "alumna.ejemplo@example.com", "vertical": "idiomas"}},
        "filtrada": {"summary": "Abiertas con actividad desde una fecha",
                     "value": {"email": "alumna.ejemplo@example.com", "vertical": "idiomas",
                               "last_activity_from": "2026-11-20T00:00:00Z", "open": True, "limit": 10}},
        "siguiente_pagina": {"summary": "Página siguiente",
                             "value": {"email": "alumna.ejemplo@example.com", "vertical": "idiomas", "limit": 2,
                                       "cursor": _encode_cursor(_SEARCH_EXAMPLE_ITEMS[1])}},
    })],
) -> ConversationSearchResponse:
    if criteria.vertical not in {v.code for v in mock_data.VERTICALS}:
        raise HTTPException(422, f"La vertical '{criteria.vertical}' no existe. Consultar GET /v1/verticals.")

    rows = [d for email, d in mock_data.CONVERSATIONS.values()
            if email == criteria.email and d.vertical == criteria.vertical]
    if criteria.open is not None:
        rows = [d for d in rows if d.open == criteria.open]
    if criteria.last_activity_from:
        rows = [d for d in rows if d.end_date >= criteria.last_activity_from]
    if criteria.last_activity_to:
        rows = [d for d in rows if d.end_date < criteria.last_activity_to]
    rows.sort(key=lambda d: (d.end_date, d.id), reverse=True)
    if criteria.cursor:
        after = _decode_cursor(criteria.cursor)
        rows = [d for d in rows if (d.end_date, d.id) < after]

    page = [ConversationSummary(**d.model_dump(include=set(ConversationSummary.model_fields)))
            for d in rows[:criteria.limit]]
    next_cursor = _encode_cursor(page[-1]) if len(rows) > criteria.limit else None
    return ConversationSearchResponse(items=page, next_cursor=next_cursor,
                                      synced_at=mock_data.SYNCED_AT if rows else None, stale=False)


@router.get(
    "/conversations/{conversation_id}", tags=["Conversaciones"], operation_id="getConversation",
    response_model=ConversationDetail, dependencies=[read_access],
    summary="Conversación con mensajes y feedback",
    description=(
        "Si la conversación está abierta y la copia guardada no es reciente, se refresca desde Serenity "
        "antes de responder. Si Serenity no responde, se devuelve la copia guardada con `stale: true`.\n\n"
        "Los mensajes del agente (`sender: bot`) están en Markdown."
    ),
    responses=_documented("/conversations/{conversation_id}", "get", {
        200: {"content": {"application/json": {"example": _dump(mock_data.example_conversation())}}},
        **_errors(401, 403, 404, 422, 429, instance="/v1/conversations/3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c01",
                  pointer="path/conversation_id", pointer_message="Input should be a valid UUID"),
    }),
)
def get_conversation(conversation_id: UUID) -> ConversationDetail:
    found = mock_data.CONVERSATIONS.get(conversation_id)
    if not found:
        raise HTTPException(404, "No existe la conversación.")
    return found[1]
