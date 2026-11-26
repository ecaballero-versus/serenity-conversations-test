"""Modelos Pydantic del contrato de la API para IRICOM (v1).

Convenciones:
- Nombres de campo en snake_case e inglés.
- Todas las fechas son UTC en ISO 8601 con sufijo `Z`.
- Los identificadores de conversación y mensaje son UUID.
"""
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from .agents import AGENTS

AgentCode = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="Código del agente orquestador de Serenity (distingue mayúsculas y minúsculas). Valores "
        "actuales: " + ", ".join(f"`{code}` ({vertical})" for code, vertical in AGENTS.items()) + ".",
        examples=["OrquestaAYIIPP"],
    ),
]

VerticalCode = Annotated[
    str,
    Field(description="Vertical a la que pertenece el orquestador. Informativo.", examples=["forvide"]),
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Búsqueda de conversaciones de un alumno --------------------------------------------------


class ConversationSearchRequest(ApiModel):
    """Criterios de búsqueda. El email va en el cuerpo para que no quede en los logs de las URL."""

    email: EmailStr = Field(
        description="Email del alumno. Se compara sin distinguir mayúsculas y minúsculas.",
        examples=["alumna.ejemplo@example.com"],
    )
    agent_code: AgentCode
    last_activity_from: AwareDatetime | None = Field(
        default=None,
        description="Solo conversaciones cuya última actividad (`end_date`) es igual o posterior a "
        "esta fecha. Debe incluir zona horaria.",
    )
    last_activity_to: AwareDatetime | None = Field(
        default=None,
        description="Solo conversaciones cuya última actividad (`end_date`) es anterior a esta fecha. "
        "Debe incluir zona horaria.",
    )
    open: bool | None = Field(
        default=None,
        description="`true`: solo abiertas; `false`: solo cerradas; omitido: todas.",
    )
    limit: int = Field(default=20, ge=1, le=100, description="Número máximo de resultados por página.")
    cursor: str | None = Field(
        default=None,
        max_length=500,
        description="Cursor opaco devuelto en `next_cursor` de la página anterior. Se debe repetir la "
        "búsqueda con los mismos criterios.",
    )

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def check_range(self) -> "ConversationSearchRequest":
        if self.last_activity_from and self.last_activity_to and self.last_activity_from >= self.last_activity_to:
            raise ValueError("last_activity_from debe ser anterior a last_activity_to")
        return self


class SyncInfo(ApiModel):
    synced_at: datetime | None = Field(
        description="Momento de la última sincronización con Serenity de los datos devueltos. `null` si "
        "nunca se ha sincronizado (por ejemplo, un alumno sin conversaciones)."
    )
    stale: bool = Field(
        description="`true` si no se pudo refrescar desde Serenity antes de responder y los datos pueden "
        "no incluir la actividad más reciente."
    )


class ConversationSummary(ApiModel):
    id: UUID
    agent_code: AgentCode
    vertical: VerticalCode
    title: str | None = Field(
        description="Título generado automáticamente por Serenity (unos 50 caracteres como máximo)."
    )
    start_date: datetime = Field(description="Inicio de la conversación.")
    end_date: datetime = Field(
        description="Última actividad: fecha del último mensaje. Cambia mientras la conversación siga abierta."
    )
    open: bool = Field(description="Si la conversación sigue abierta (se cierra tras 24 h sin actividad).")
    message_count: int = Field(ge=0, description="Número de mensajes, incluido el de bienvenida.")


class _ConversationPage(ApiModel):
    items: list[ConversationSummary] = Field(
        description="Conversaciones ordenadas por última actividad, de la más reciente a la más antigua."
    )
    next_cursor: str | None = Field(description="Cursor de la página siguiente; `null` si no hay más.")


class ConversationSearchResponse(SyncInfo, _ConversationPage):
    # Orden de bases: items y next_cursor aparecen antes que synced_at y stale
    pass


# --- Detalle de conversación ---------------------------------------------------------------------


class Sender(str, Enum):
    user = "user"
    bot = "bot"


class Attachment(BaseModel):
    """Conocimiento adjunto a un mensaje (`attachedVolatileKnowledges` en Serenity).

    La forma exacta está pendiente de confirmar: pueden aparecer campos adicionales.
    """

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(default=None, description="Identificador del adjunto en Serenity.")
    name: str | None = Field(default=None, description="Nombre del archivo.", examples=["temario.pdf"])
    content_type: str | None = Field(default=None, description="Tipo MIME.", examples=["application/pdf"])


class MessageFeedback(ApiModel):
    is_positive: bool = Field(description="`true`: valoración positiva; `false`: negativa.")
    comment: str | None = Field(default=None, max_length=1000, description="Comentario opcional del alumno.")
    created_at: datetime = Field(
        description="Fecha de la valoración. No cambia si el alumno modifica la valoración después."
    )


class Message(ApiModel):
    id: UUID = Field(
        description="Identificador del mensaje en Serenity. **No es único**: la bienvenida y algunos mensajes del "
        "agente sin identificador propio (p. ej. avisos) llevan `00000000-0000-0000-0000-000000000000`. Para "
        "identificar un mensaje dentro de la conversación, úsese `seq`."
    )
    seq: int = Field(ge=0, description="Posición del mensaje en la conversación, empezando en 0.")
    sender: Sender = Field(description="`user`: el alumno; `bot`: el agente.")
    type: str = Field(description="Tipo de contenido. Hoy siempre `text`.", examples=["text"])
    content: str = Field(description="Contenido en Markdown. El aula debe renderizarlo.")
    created_at: datetime
    is_welcome: bool = Field(description="`true` en el mensaje de bienvenida del agente (siempre `seq = 0`).")
    canceled_by_client: bool = Field(description="El alumno canceló la generación de la respuesta.")
    is_interruption: bool = Field(description="El mensaje interrumpió una respuesta en curso.")
    attachments: list[Attachment] = Field(default_factory=list)
    feedback: MessageFeedback | None = Field(
        default=None, description="Valoración del alumno. Solo en mensajes `bot`; `null` si no hay."
    )


class ConversationDetail(SyncInfo, ConversationSummary):
    # Orden de bases: los campos de ConversationSummary aparecen primero en el esquema
    student_email: str = Field(
        description="Email del alumno, en minúsculas, tal como lo identifica Serenity. Las conversaciones de "
        "pruebas de desarrollo tienen un identificador `apikey-user-…@serenitystar.ai`.",
        examples=["alumna.ejemplo@example.com"],
    )
    messages: list[Message] = Field(description="Mensajes en orden cronológico.")


# --- Sistema --------------------------------------------------------------------------------------


class Health(ApiModel):
    status: Literal["ok", "degraded"] = Field(
        description="`degraded` si la API responde pero la sincronización con Serenity está retrasada."
    )
    version: str = Field(examples=["0.1.0"])
    time: datetime


# --- Errores (RFC 9457) ---------------------------------------------------------------------------


class ValidationIssue(ApiModel):
    pointer: str = Field(
        description="Ubicación del campo erróneo (JSON Pointer para el cuerpo, o `query`/`path` + nombre).",
        examples=["/email"],
    )
    message: str = Field(examples=["value is not a valid email address"])


class Problem(BaseModel):
    """Error en formato `application/problem+json` (RFC 9457)."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(
        default="about:blank",
        description="URI que identifica el tipo de error.",
        examples=["https://api.example.com/problems/not-found"],
    )
    title: str = Field(description="Resumen del tipo de error.", examples=["Not Found"])
    status: int = Field(description="Código HTTP.", examples=[404])
    detail: str | None = Field(default=None, description="Explicación de este caso concreto.")
    instance: str | None = Field(default=None, description="Ruta de la petición que falló.")
    errors: list[ValidationIssue] | None = Field(default=None, description="Solo en 422: errores de validación.")
