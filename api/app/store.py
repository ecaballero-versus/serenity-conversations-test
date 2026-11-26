"""Origen de los datos de la API.

- `MockStore`: datos ficticios de `mock_data` (contrato, tests y ejemplos para IRICOM).
- `DbStore`: base de datos de la ingesta. Antes de responder aplica la lectura síncrona contra Serenity
  (`OnDemandSync`) en los orquestadores sincronizados.

Se elige con `API_BACKEND` (ver `settings.py`).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Protocol
from uuid import UUID

from . import mock_data
from .agents import AGENTS, SYNC_ENABLED
from .models import (
    Attachment,
    ConversationDetail,
    ConversationSearchRequest,
    ConversationSummary,
    Message,
    MessageFeedback,
)
from .settings import settings

log = logging.getLogger(__name__)

WELCOME_ID = "00000000-0000-0000-0000-000000000000"
# Una conversación abierta se vuelve a descargar si la copia tiene más de esto
CONVERSATION_MAX_AGE = timedelta(seconds=60)
# Sin un ciclo correcto de la ingesta periódica en este tiempo, /v1/health responde `degraded`
INGEST_MAX_DELAY = timedelta(minutes=15)


@dataclass
class SearchResult:
    items: list[ConversationSummary]  # hasta limit + 1 (el sobrante indica que hay página siguiente)
    synced_at: datetime | None
    stale: bool


class Store(Protocol):
    def search(self, criteria: ConversationSearchRequest, after: tuple[datetime, UUID] | None) -> SearchResult: ...
    def get(self, conversation_id: UUID) -> ConversationDetail | None: ...
    def health(self) -> str: ...


def _summary(d: ConversationDetail) -> ConversationSummary:
    return ConversationSummary(**d.model_dump(include=set(ConversationSummary.model_fields)))


class MockStore:
    def search(self, criteria, after):
        rows = [d for email, d in mock_data.CONVERSATIONS.values()
                if email == criteria.email and d.agent_code == criteria.agent_code]
        if criteria.open is not None:
            rows = [d for d in rows if d.open == criteria.open]
        if criteria.last_activity_from:
            rows = [d for d in rows if d.end_date >= criteria.last_activity_from]
        if criteria.last_activity_to:
            rows = [d for d in rows if d.end_date < criteria.last_activity_to]
        rows.sort(key=lambda d: (d.end_date, d.id), reverse=True)
        if after:
            rows = [d for d in rows if (d.end_date, d.id) < after]
        return SearchResult(items=[_summary(d) for d in rows[:criteria.limit + 1]],
                            synced_at=mock_data.SYNCED_AT if rows else None, stale=False)

    def get(self, conversation_id):
        found = mock_data.CONVERSATIONS.get(conversation_id)
        return found[1] if found else None

    def health(self):
        return "ok"


class OnDemandSync:
    """Lectura síncrona contra Serenity. Nunca lanza excepciones: devuelve False si falla.

    Local: llama directamente al paquete `ingest`. En AWS será una invocación a la Lambda de sincronización.
    """

    def __init__(self, engine, data_dir, base_url: str, api_key: str):
        from ingest.storage import LocalRawStore

        self.engine, self.raw_store = engine, LocalRawStore(data_dir)
        self.base_url, self.api_key = base_url, api_key

    def _client(self):
        from ingest.client import SerenityClient

        # Hay alguien esperando la respuesta: tiempos cortos, un reintento y sin esperar ante un 429
        return SerenityClient(self.base_url, self.api_key, rps=10, timeout=5, max_retries=1, wait_on_429=False)

    def student(self, agent_code: str, email: str) -> bool:
        from ingest.ondemand import sync_student

        try:
            return sync_student(self.engine, self._client(), self.raw_store, agent_code, email)
        except Exception as exc:  # noqa: BLE001
            log.warning("Lectura síncrona de alumno fallida (%s): %s", agent_code, type(exc).__name__)
            return False

    def conversation(self, agent_code: str, conversation_id: str) -> bool:
        from ingest.ondemand import refresh_conversation

        try:
            return refresh_conversation(self.engine, self._client(), self.raw_store, agent_code, conversation_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Refresco de la conversación %s fallido: %s", conversation_id, type(exc).__name__)
            return False


class DbStore:
    def __init__(self, engine, sync: OnDemandSync | None = None, sync_agents: set[str] = SYNC_ENABLED):
        from ingest import db

        self.db, self.engine, self.sync, self.sync_agents = db, engine, sync, sync_agents

    # --- auxiliares ---------------------------------------------------------------------------------

    def _last_cycle(self, conn, agent_code: str) -> datetime | None:
        """Fin del último ciclo correcto de la ingesta periódica. Un ciclo correcto garantiza que la copia
        incluye toda la actividad anterior a ese momento (el listado incremental la habría detectado)."""
        from sqlalchemy import func, select

        r = self.db.runs.c
        return conn.execute(select(func.max(r.finished_at)).where(
            r.agent_code == agent_code, r.status.in_(["ok", "partial"]))).scalar()

    @staticmethod
    def _latest(*values: datetime | None) -> datetime | None:
        return max((v for v in values if v is not None), default=None)

    def _can_sync(self, agent_code: str) -> bool:
        return self.sync is not None and agent_code in self.sync_agents

    # --- Store --------------------------------------------------------------------------------------

    def search(self, criteria, after):
        from sqlalchemy import and_, or_, select

        stale = False
        if self._can_sync(criteria.agent_code):
            stale = not self.sync.student(criteria.agent_code, criteria.email)

        c = self.db.conversations.c
        q = select(c.id, c.agent_code, c.title, c.start_date, c.end_date, c.is_open, c.message_count).where(
            c.agent_code == criteria.agent_code, c.user_identifier == criteria.email)
        if criteria.open is not None:
            q = q.where(c.is_open == criteria.open)
        if criteria.last_activity_from:
            q = q.where(c.end_date >= criteria.last_activity_from)
        if criteria.last_activity_to:
            q = q.where(c.end_date < criteria.last_activity_to)
        if after:
            end, cid = after
            q = q.where(or_(c.end_date < end, and_(c.end_date == end, c.id < str(cid))))
        q = q.order_by(c.end_date.desc(), c.id.desc()).limit(criteria.limit + 1)

        st = self.db.student_state.c
        with self.engine.connect() as conn:
            rows = conn.execute(q).all()
            student_synced = conn.execute(select(st.last_synced_at).where(
                st.agent_code == criteria.agent_code, st.user_identifier == criteria.email)).scalar()
            synced_at = self._latest(student_synced, self._last_cycle(conn, criteria.agent_code))
        items = [ConversationSummary(id=r.id, agent_code=r.agent_code, vertical=AGENTS.get(r.agent_code, ""),
                                     title=r.title, start_date=r.start_date, end_date=r.end_date, open=r.is_open,
                                     message_count=r.message_count) for r in rows]
        return SearchResult(items=items, synced_at=synced_at, stale=stale)

    def get(self, conversation_id):
        from sqlalchemy import select

        c, m, f = self.db.conversations, self.db.messages, self.db.message_feedback
        cid = str(conversation_id)
        load = select(c).where(c.c.id == cid)
        with self.engine.connect() as conn:
            row = conn.execute(load).first()
        if row is None:
            return None

        stale = False
        if (self._can_sync(row.agent_code) and row.is_open
                and datetime.now(timezone.utc) - row.last_synced_at > CONVERSATION_MAX_AGE):
            stale = not self.sync.conversation(row.agent_code, cid)

        with self.engine.connect() as conn:
            row = conn.execute(load).first()
            msgs = conn.execute(select(m).where(m.c.conversation_id == cid).order_by(m.c.seq)).all()
            feedback = {fb.message_id: fb for fb in conn.execute(select(f).where(f.c.conversation_id == cid))}
            synced_at = self._latest(row.last_synced_at, self._last_cycle(conn, row.agent_code))

        messages = []
        for msg in msgs:
            fb = feedback.get(msg.id) if msg.sender == "bot" and msg.id != WELCOME_ID else None
            messages.append(Message(
                id=msg.id, seq=msg.seq, sender=msg.sender, type=msg.type or "text", content=msg.value or "",
                created_at=msg.created_at, is_welcome=msg.seq == 0 and msg.id == WELCOME_ID,
                canceled_by_client=msg.canceled_by_client, is_interruption=msg.is_interruption,
                attachments=[Attachment.model_validate(a) for a in msg.attachments or [] if isinstance(a, dict)],
                feedback=MessageFeedback(is_positive=fb.is_positive, comment=fb.comment,
                                         created_at=fb.created_at) if fb else None,
            ))
        return ConversationDetail(
            id=row.id, agent_code=row.agent_code, vertical=AGENTS.get(row.agent_code, ""), title=row.title,
            start_date=row.start_date, end_date=row.end_date, open=row.is_open, message_count=row.message_count,
            synced_at=synced_at, stale=stale, student_email=row.user_identifier, messages=messages)

    def health(self):
        now = datetime.now(timezone.utc)
        with self.engine.connect() as conn:
            for agent_code in self.sync_agents:
                last = self._last_cycle(conn, agent_code)
                if last is None or now - last > INGEST_MAX_DELAY:
                    return "degraded"
        return "ok"


@lru_cache
def get_store() -> Store:
    if settings.backend == "mock":
        return MockStore()
    from ingest import db

    db_path = settings.data_dir / "serenity.db"
    if not db_path.exists():
        log.warning("No existe %s: la API arranca con una base vacía. Ejecuta antes la ingesta.", db_path)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = db.make_engine(f"sqlite:///{db_path}")
    sync = None
    if settings.sync_on_demand:
        if settings.serenity_base_url and settings.serenity_api_key:
            sync = OnDemandSync(engine, settings.data_dir, settings.serenity_base_url, settings.serenity_api_key)
        else:
            log.warning("Sin SERENITY_BASE_URL o SERENITY_API_KEY: lectura síncrona desactivada")
    log.info("API con datos de %s (lectura síncrona %s)", db_path, "activada" if sync else "desactivada")
    return DbStore(engine, sync)
