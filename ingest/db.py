"""Modelo de datos (SQLAlchemy Core).

Prototipo sobre SQLite. SQLite no tiene esquemas, así que los esquemas del diseño (`serenity`, `sync`)
se reflejan en el prefijo del nombre de tabla. Al pasar a PostgreSQL, cada tabla irá a su esquema.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    TypeDecorator,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine


class UTCDateTime(TypeDecorator):
    """Guarda en UTC sin zona y devuelve datetime con zona UTC (SQLite no guarda zonas)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Se esperaba un datetime con zona horaria")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


metadata = MetaData()

# --- serenity: datos ------------------------------------------------------------------------------

agents = Table(
    "serenity_agents", metadata,
    Column("agent_code", String(100), primary_key=True),
    Column("vertical", String(50), nullable=False),
    Column("description", String(200)),
    Column("sync_enabled", Boolean, nullable=False, default=False),
    Column("retention_days", Integer),
)

conversations = Table(
    "serenity_conversations", metadata,
    Column("id", String(36), primary_key=True),
    Column("agent_code", String(100), nullable=False),
    Column("user_identifier", String(320), nullable=False),  # email en minúsculas
    Column("title", String(500)),
    Column("channel", String(100)),  # del listado: FV IP (aula), WebAppPreview / Unspecified (pruebas)
    Column("agent_version", Integer),  # del listado: versión del agente que atendió la conversación
    Column("start_date", UTCDateTime, nullable=False),
    Column("end_date", UTCDateTime, nullable=False),
    Column("is_open", Boolean, nullable=False),
    Column("message_count", Integer, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("raw_key", String(300), nullable=False),
    Column("first_synced_at", UTCDateTime, nullable=False),
    Column("last_synced_at", UTCDateTime, nullable=False),
    Index("ix_conversations_student", "user_identifier", "agent_code", "end_date"),
    Index("ix_conversations_agent_end", "agent_code", "end_date"),
)

messages = Table(
    "serenity_messages", metadata,
    # La clave es la posición: el id no es único dentro de una conversación. La bienvenida y algunos
    # mensajes del bot sin id propio (posibles errores o avisos) llevan el GUID vacío.
    Column("conversation_id", String(36), primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("id", String(36), nullable=False),
    Column("sender", String(20), nullable=False),
    Column("type", String(20)),
    Column("value", Text),
    Column("created_at", UTCDateTime, nullable=False),
    Column("canceled_by_client", Boolean, nullable=False, default=False),
    Column("is_interruption", Boolean, nullable=False, default=False),
    Column("attachments", JSON, nullable=False, default=list),
    Index("ix_messages_id", "conversation_id", "id"),
)

message_feedback = Table(
    "serenity_message_feedback", metadata,
    Column("id", String(36), primary_key=True),
    Column("agent_code", String(100), nullable=False),
    Column("conversation_id", String(36), nullable=False),
    Column("message_id", String(36), nullable=False),
    Column("is_positive", Boolean, nullable=False),
    Column("comment", Text),
    Column("created_at", UTCDateTime, nullable=False),  # dateUtc: no cambia al modificar la valoración
    Column("last_synced_at", UTCDateTime, nullable=False),
    Index("ix_feedback_conversation", "conversation_id"),
    Index("ix_feedback_agent_created", "agent_code", "created_at"),
)

# --- sync: estado de la sincronización ------------------------------------------------------------

agent_state = Table(
    "sync_agent_state", metadata,
    Column("agent_code", String(100), primary_key=True),
    Column("watermark", UTCDateTime),  # fecha de fin más reciente vista en el listado
    Column("updated_at", UTCDateTime, nullable=False),
)

student_state = Table(
    "sync_student_state", metadata,
    # Lectura síncrona (bajo demanda) por alumno: como mucho una consulta a Serenity por minuto
    Column("agent_code", String(100), primary_key=True),
    Column("user_identifier", String(320), primary_key=True),
    Column("last_synced_at", UTCDateTime, nullable=False),
    Column("watermark", UTCDateTime),  # última actividad más reciente vista para el alumno
)

runs = Table(
    "sync_runs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("agent_code", String(100), nullable=False),
    Column("kind", String(20), nullable=False),  # ingest
    Column("started_at", UTCDateTime, nullable=False),
    Column("finished_at", UTCDateTime),
    Column("status", String(20), nullable=False),  # running, ok, partial, error
    Column("lower_bound", UTCDateTime),
    Column("listed", Integer, default=0),
    Column("fetched", Integer, default=0),
    Column("unchanged", Integer, default=0),
    Column("failed", Integer, default=0),
    Column("not_found", Integer, default=0),
    Column("feedback_upserted", Integer, default=0),
    Column("feedback_deleted", Integer, default=0),
    Column("requests_made", Integer, default=0),
    Column("rate_limit_remaining", Integer),
    Column("error", Text),  # sin datos de alumnos
)

pending = Table(
    "sync_pending", metadata,
    Column("agent_code", String(100), primary_key=True),
    Column("conversation_id", String(36), primary_key=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("last_error", Text),
    Column("first_failed_at", UTCDateTime, nullable=False),
    Column("last_failed_at", UTCDateTime, nullable=False),
)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # La API (varios hilos) y la ingesta periódica (otro proceso) escriben a la vez en el mismo archivo
        engine = create_engine(url, connect_args={"timeout": 30, "check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()
    else:
        engine = create_engine(url)
    metadata.create_all(engine)
    _add_missing_columns(engine)
    return engine


def _add_missing_columns(engine: Engine) -> None:
    """Migración mínima del prototipo: añade columnas nuevas a tablas existentes (sin borrar datos)."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in existing:
                    conn.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col.name} "
                                      f"{col.type.compile(engine.dialect)}"))


def upsert(conn, table: Table, values: dict, keys: list[str]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE, válido en SQLite y PostgreSQL."""
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table).values(**values)
    update = {k: stmt.excluded[k] for k in values if k not in keys}
    conn.execute(stmt.on_conflict_do_update(index_elements=keys, set_=update) if update
                 else stmt.on_conflict_do_nothing(index_elements=keys))
