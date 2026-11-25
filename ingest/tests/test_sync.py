"""Tests de la ingesta con un cliente falso. Solo datos ficticios."""
import json
from datetime import timedelta, timezone

import pytest
from sqlalchemy import func, select

from ingest import db
from ingest.client import SerenityError
from ingest.storage import LocalRawStore
from ingest.sync import run_ingest
from ingest.timeutil import parse_utc, utcnow

AGENT = "AgentePrueba"


def fmt(dt, tz=False):
    # Formatos de Serenity: sin zona y 7 decimales (conversaciones, feedback) o con +00:00 (mensajes)
    s = dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "0"
    return s + "+00:00" if tz else s


class FakeClient:
    def __init__(self):
        self.convs: dict[str, dict] = {}
        self.feedback: dict[str, dict] = {}
        self.fail_ids: set[str] = set()
        self.detail_calls: list[str] = []
        self.requests_made = 0
        self.rate_limit_remaining = 1000

    def add_conv(self, cid, minutes_ago, n_turns=1, email="Alumno.Ficticio@Example.com"):
        end = utcnow() - timedelta(minutes=minutes_ago)
        start = end - timedelta(minutes=5)
        msgs = [{"id": "00000000-0000-0000-0000-000000000000", "sender": "bot", "type": "text",
                 "value": "Hola", "createdAt": fmt(start, tz=True)}]
        for i in range(n_turns):
            msgs.append({"id": f"{cid[:8]}-u{i}", "sender": "user", "type": "text", "value": "Pregunta",
                         "createdAt": fmt(end - timedelta(seconds=10), tz=True)})
            msgs.append({"id": f"{cid[:8]}-b{i}", "sender": "bot", "type": "text", "value": "**Respuesta**",
                         "createdAt": fmt(end, tz=True), "attachedVolatileKnowledges": []})
        self.convs[cid] = {"version": 13, "id": cid, "name": "Título", "startDate": fmt(start), "endDate": fmt(end),
                           "open": True, "userIdentifier": email, "messages": msgs}

    def list_conversations(self, agent, page, page_size):
        self.requests_made += 1
        items = sorted(self.convs.values(), key=lambda c: c["endDate"], reverse=True)
        chunk = items[(page - 1) * page_size: page * page_size]
        return {"total": len(items), "page": page, "pageSize": page_size,
                "items": [{"id": c["id"], "endDate": c["endDate"], "startDate": c["startDate"],
                           "channel": "FV IP", "agentVersion": c.get("version", 13)} for c in chunk]}

    def get_conversation(self, agent, cid):
        self.requests_made += 1
        self.detail_calls.append(cid)
        if cid in self.fail_ids:
            raise SerenityError("HTTP 500")
        raw = json.dumps(self.convs[cid]).encode()
        return json.loads(raw), raw

    def list_feedback(self, agent, start_date, page, page_size):
        self.requests_made += 1
        since = parse_utc(start_date)
        items = [f for f in self.feedback.values() if parse_utc(f["dateUtc"]) >= since]
        return {"total": len(items), "page": page, "pageSize": page_size,
                "items": items[(page - 1) * page_size: page * page_size]}


@pytest.fixture
def env(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path / 't.db'}")
    return engine, FakeClient(), LocalRawStore(tmp_path)


def count(engine, table, *where):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table).where(*where)).scalar()


def test_parse_utc():
    assert parse_utc("2026-11-20T10:15:02.1234567").isoformat() == "2026-11-20T10:15:02.123456+00:00"
    assert parse_utc("2026-11-20T10:15:02+00:00").tzinfo is not None
    assert parse_utc("2026-11-20T10:15:02Z") == parse_utc("2026-11-20T10:15:02")


def test_primera_ejecucion_respeta_ventana(env, tmp_path):
    engine, client, store = env
    client.add_conv("aaaaaaaa-0000-0000-0000-000000000001", minutes_ago=60)
    client.add_conv("aaaaaaaa-0000-0000-0000-000000000002", minutes_ago=60 * 24 * 10)  # fuera de 7 días
    stats = run_ingest(engine, client, store, AGENT, bootstrap_days=7)
    assert (stats.listed, stats.fetched) == (1, 1)
    assert count(engine, db.messages) == 3
    with engine.connect() as conn:
        row = conn.execute(select(db.conversations)).one()
    assert row.user_identifier == "alumno.ficticio@example.com"
    assert row.end_date.tzinfo is not None and row.channel == "FV IP"
    assert (tmp_path / row.raw_key).exists()


def test_incremental_solo_descarga_cambios(env):
    engine, client, store = env
    for i in range(3):
        client.add_conv(f"bbbbbbbb-0000-0000-0000-00000000000{i}", minutes_ago=30 + i)
    run_ingest(engine, client, store, AGENT)
    client.detail_calls.clear()
    # Sin cambios: el solape vuelve a listar las tres, pero no se descarga ninguna
    stats = run_ingest(engine, client, store, AGENT)
    assert stats.fetched == 0 and stats.unchanged == 3 and client.detail_calls == []
    # Actividad nueva en una y una conversación nueva
    client.add_conv("bbbbbbbb-0000-0000-0000-000000000000", minutes_ago=0, n_turns=2)
    client.add_conv("bbbbbbbb-0000-0000-0000-000000000009", minutes_ago=0)
    stats = run_ingest(engine, client, store, AGENT)
    assert sorted(client.detail_calls) == ["bbbbbbbb-0000-0000-0000-000000000000",
                                           "bbbbbbbb-0000-0000-0000-000000000009"]
    with engine.connect() as conn:
        n = conn.execute(select(db.conversations.c.message_count)
                         .where(db.conversations.c.id == "bbbbbbbb-0000-0000-0000-000000000000")).scalar()
    assert n == 5


def test_fallo_queda_pendiente_y_se_reintenta(env):
    engine, client, store = env
    cid = "cccccccc-0000-0000-0000-000000000001"
    client.add_conv(cid, minutes_ago=5)
    client.fail_ids.add(cid)
    stats = run_ingest(engine, client, store, AGENT)
    assert stats.failed == 1 and count(engine, db.pending) == 1
    with engine.connect() as conn:
        assert conn.execute(select(db.runs.c.status)).scalar() == "partial"
    client.fail_ids.clear()
    # Aunque la conversación ya no tenga actividad nueva, se reintenta por estar pendiente
    stats = run_ingest(engine, client, store, AGENT)
    assert stats.fetched == 1 and count(engine, db.pending) == 0


def test_reconciliacion_de_feedback(env):
    engine, client, store = env
    cid = "dddddddd-0000-0000-0000-000000000001"
    client.add_conv(cid, minutes_ago=5)
    recent, old = fmt(utcnow() - timedelta(days=1)), fmt(utcnow() - timedelta(days=20))
    client.feedback = {
        "f1": {"id": "f1", "conversationId": cid, "agentMessageId": "m1", "feedback": True, "dateUtc": recent},
        "f2": {"id": "f2", "conversationId": cid, "agentMessageId": "m2", "feedback": False, "comment": "Mal",
               "dateUtc": recent},
    }
    run_ingest(engine, client, store, AGENT)
    assert count(engine, db.message_feedback) == 2
    # Fuera de la ventana: se inserta a mano una valoración antigua que Serenity ya no devuelve
    with engine.begin() as conn:
        conn.execute(db.message_feedback.insert().values(
            id="f-old", agent_code=AGENT, conversation_id=cid, message_id="m0", is_positive=True,
            created_at=parse_utc(old), last_synced_at=utcnow()))
    # Cambio de valoración (mismo registro, misma fecha) y retirada de otra
    client.feedback["f1"]["feedback"] = False
    del client.feedback["f2"]
    stats = run_ingest(engine, client, store, AGENT)
    assert stats.feedback_deleted == 1
    with engine.connect() as conn:
        rows = dict(conn.execute(select(db.message_feedback.c.id, db.message_feedback.c.is_positive)).all())
    assert rows == {"f1": False, "f-old": True}  # la antigua no se borra


def test_dry_run_no_escribe(env):
    engine, client, store = env
    client.add_conv("eeeeeeee-0000-0000-0000-000000000001", minutes_ago=5)
    stats = run_ingest(engine, client, store, AGENT, dry_run=True)
    assert stats.listed == 1
    assert count(engine, db.conversations) == 0 and count(engine, db.runs) == 0 and client.detail_calls == []


def test_varios_mensajes_con_guid_vacio(env):
    engine, client, store = env
    cid = "ffffffff-0000-0000-0000-000000000001"
    client.add_conv(cid, minutes_ago=5)
    last = client.convs[cid]["messages"][-1]
    client.convs[cid]["messages"].append({**last, "id": "00000000-0000-0000-0000-000000000000",
                                          "value": "Aviso del sistema"})
    stats = run_ingest(engine, client, store, AGENT)
    assert stats.fetched == 1 and count(engine, db.messages) == 4


def test_error_de_base_de_datos_no_guarda_contenido(env, monkeypatch):
    engine, client, store = env
    cid = "ffffffff-0000-0000-0000-000000000002"
    client.add_conv(cid, minutes_ago=5, email="secreto@example.com")
    from sqlalchemy.exc import IntegrityError
    import ingest.sync as sync

    def boom(*a, **k):
        raise IntegrityError("INSERT ... secreto@example.com", {"v": "secreto@example.com"}, ValueError("x"))
    monkeypatch.setattr(sync, "_save_conversation", boom)
    run_ingest(engine, client, store, AGENT)
    with engine.connect() as conn:
        error = conn.execute(select(db.pending.c.last_error)).scalar()
    assert "secreto" not in error and error.startswith("IntegrityError")


def test_relectura_actualiza_metadatos_sin_descargar(env):
    engine, client, store = env
    cid = "abababab-0000-0000-0000-000000000001"
    client.add_conv(cid, minutes_ago=60 * 24 * 2)
    run_ingest(engine, client, store, AGENT)
    client.add_conv("abababab-0000-0000-0000-000000000002", minutes_ago=1)
    run_ingest(engine, client, store, AGENT)  # la marca de agua ya deja fuera la primera
    client.convs[cid]["version"] = 14
    client.detail_calls.clear()
    stats = run_ingest(engine, client, store, AGENT, rescan_days=7)
    assert stats.listed == 2 and client.detail_calls == []
    with engine.connect() as conn:
        v = conn.execute(select(db.conversations.c.agent_version).where(db.conversations.c.id == cid)).scalar()
    assert v == 14


def test_migracion_anade_columnas(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    url = f"sqlite:///{tmp_path / 'old.db'}"
    with create_engine(url).begin() as conn:
        conn.execute(text("CREATE TABLE serenity_conversations (id VARCHAR(36) PRIMARY KEY)"))
        conn.execute(text("INSERT INTO serenity_conversations (id) VALUES ('x')"))
    engine = db.make_engine(url)
    cols = {c["name"] for c in inspect(engine).get_columns("serenity_conversations")}
    assert "agent_version" in cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM serenity_conversations")).scalar() == 1
