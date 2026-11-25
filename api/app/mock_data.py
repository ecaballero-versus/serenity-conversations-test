"""Datos ficticios para la fase 0. Ningún dato procede de Serenity ni corresponde a alumnos reales.

Los códigos de agente son los reales (config/agents.yaml); conversaciones y alumnos son inventados.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID

from .agents import AGENTS
from .models import Attachment, ConversationDetail, Message, MessageFeedback, Sender

WELCOME_ID = UUID("00000000-0000-0000-0000-000000000000")

# Momento fijo de la última sincronización en los datos de ejemplo
SYNCED_AT = datetime(2026, 11, 24, 10, 0, 0, tzinfo=timezone.utc)


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def _conversation(conv_id, email, agent_code, title, open_, turns, start, welcome) -> tuple[str, ConversationDetail]:
    """Construye una conversación a partir de turnos (pregunta, respuesta, feedback, adjuntos)."""
    t = _dt(start)
    messages = [Message(
        id=WELCOME_ID, seq=0, sender=Sender.bot, type="text", content=welcome, created_at=t,
        is_welcome=True, canceled_by_client=False, is_interruption=False,
    )]
    for i, turn in enumerate(turns):
        question, answer = turn["q"], turn["a"]
        t += timedelta(seconds=40)
        messages.append(Message(
            id=UUID(f"{conv_id[:24]}{2 * i + 1:012x}"), seq=len(messages), sender=Sender.user, type="text",
            content=question, created_at=t, is_welcome=False, canceled_by_client=False,
            is_interruption=turn.get("interruption", False), attachments=turn.get("attachments", []),
        ))
        t += timedelta(seconds=9)
        messages.append(Message(
            id=UUID(f"{conv_id[:24]}{2 * i + 2:012x}"), seq=len(messages), sender=Sender.bot, type="text",
            content=answer, created_at=t, is_welcome=False, canceled_by_client=turn.get("canceled", False),
            is_interruption=False, feedback=turn.get("feedback"),
        ))
    detail = ConversationDetail(
        id=UUID(conv_id), agent_code=agent_code, vertical=AGENTS[agent_code], title=title, start_date=messages[0].created_at,
        end_date=messages[-1].created_at, open=open_, student_email=email, message_count=len(messages), messages=messages,
        synced_at=SYNCED_AT, stale=False,
    )
    return email, detail


_STUDENT_A = "alumna.ejemplo@example.com"
_STUDENT_B = "alumno.demo@example.org"

_WELCOME_IDIOMAS = "¡Hola! Soy tu tutor. ¿En qué puedo ayudarte hoy?"
_WELCOME_MARKETING = "¡Hola! Soy tu asistente. Cuéntame qué necesitas."

_RAW = [
    _conversation(
        "3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c01", _STUDENT_A, "OrquestaAYIIPP",
        "Diferencia entre present perfect y past simple", True,
        [
            {
                "q": "¿Cuándo uso el present perfect en lugar del past simple?",
                "a": "Usa el **present perfect** cuando la acción tiene relación con el presente:\n\n"
                     "- *I have lost my keys* (aún no las tengo).\n"
                     "- *I lost my keys yesterday* (momento terminado: **past simple**).\n\n"
                     "Pista: si aparece un momento concreto del pasado (*yesterday*, *in 2020*), usa past simple.",
                "feedback": MessageFeedback(is_positive=True, comment="Muy claro, gracias",
                                            created_at=_dt("2026-11-24T09:12:30")),
            },
            {
                "q": "¿Y con *for* y *since*?",
                "a": "Con *for* y *since* se usa el present perfect para algo que empezó en el pasado y "
                     "continúa:\n\n| Expresión | Ejemplo |\n|---|---|\n| for + duración | *I have lived "
                     "here for three years* |\n| since + punto de inicio | *I have lived here since 2023* |",
            },
        ],
        "2026-11-24T09:10:00", _WELCOME_IDIOMAS,
    ),
    _conversation(
        "3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c02", _STUDENT_A, "OrquestaAYIIPP",
        "Corrección de un email formal en inglés", False,
        [
            {
                "q": "¿Puedes revisar este email? Lo adjunto.",
                "a": "He revisado el texto. Tres cambios recomendados:\n\n"
                     "1. Sustituye *Dear Sir* por *Dear Hiring Manager*.\n"
                     "2. Evita contracciones: *I'm* → *I am*.\n"
                     "3. Cierra con *Kind regards* en lugar de *Bye*.",
                "attachments": [Attachment(id="vk-0001", name="borrador_email.docx",
                                           content_type="application/vnd.openxmlformats-officedocument."
                                                        "wordprocessingml.document")],
                "feedback": MessageFeedback(is_positive=False, comment="Esperaba que reescribiera el email completo",
                                            created_at=_dt("2026-11-18T16:05:10")),
            },
        ],
        "2026-11-18T16:02:00", _WELCOME_IDIOMAS,
    ),
    _conversation(
        "3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c03", _STUDENT_A, "OrquestaAYIIPP",
        "Vocabulario para una entrevista de trabajo", False,
        [
            {
                "q": "Dame vocabulario útil para una entrevista de trabajo en inglés",
                "a": "Aquí tienes una lista larga de expresiones agrupadas por tema: presentación, "
                     "experiencia, fortalezas, debilidades…",
                "canceled": True,
            },
            {
                "q": "Solo las 5 más importantes, por favor",
                "interruption": True,
                "a": "Las cinco más útiles:\n\n1. *I'm a fast learner*\n2. *I work well under pressure*\n"
                     "3. *I'm looking for new challenges*\n4. *My main strength is…*\n5. *Could you tell me "
                     "more about the team?*",
            },
        ],
        "2026-11-10T11:30:00", _WELCOME_IDIOMAS,
    ),
    _conversation(
        "3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c04", _STUDENT_A, "AgenteOrquestaGCIN",
        "Qué es el CTR y cómo mejorarlo", False,
        [
            {
                "q": "¿Qué es el CTR?",
                "a": "El **CTR** (*click-through rate*) es el porcentaje de impresiones que acaban en clic:\n\n"
                     "`CTR = clics / impresiones × 100`",
            },
        ],
        "2026-11-20T08:45:00", _WELCOME_MARKETING,
    ),
    _conversation(
        "7a1d9e44-2c3b-4f88-a5d6-0b9c8d7e6f01", _STUDENT_B, "OrquestaAYIIPP",
        "Phrasal verbs con get", False,
        [
            {
                "q": "Explícame los phrasal verbs con *get*",
                "a": "Algunos de los más frecuentes:\n\n- **get up**: levantarse\n- **get over**: superar\n"
                     "- **get along with**: llevarse bien con",
                "feedback": MessageFeedback(is_positive=True, created_at=_dt("2026-11-22T19:01:00")),
            },
        ],
        "2026-11-22T18:58:00", _WELCOME_IDIOMAS,
    ),
]

# id de conversación -> (email del alumno, detalle)
CONVERSATIONS: dict[UUID, tuple[str, ConversationDetail]] = {d.id: (email, d) for email, d in _RAW}


def example_conversation() -> ConversationDetail:
    return CONVERSATIONS[UUID("3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c01")][1]
