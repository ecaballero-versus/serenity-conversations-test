"""API con la base de datos de la ingesta (API_BACKEND=db). Solo datos ficticios."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import security
from app.main import app
from app.store import DbStore, get_store
from ingest import db

AUTH = {"Authorization": "Bearer token-de-prueba"}
SEARCH = "/v1/students/conversations/search"
AGENT, OTHER_AGENT = "OrquestaAYIIPP", "AgenteOrquestaGCIN"
EMAIL = "alumno.ficticio@example.com"
ZERO = "00000000-0000-0000-0000-000000000000"
NOW = datetime.now(timezone.utc).replace(microsecond=0)
T0 = NOW - timedelta(days=2)


def cid(n: int) -> str:
    return f"aaaaaaaa-0000-4000-8000-{n:012d}"


class FakeSync:
    def __init__(self):
        self.ok = True
        self.students: list[tuple[str, str]] = []
        self.conversations: list[str] = []

    def student(self, agent_code, email):
        self.students.append((agent_code, email))
        return self.ok

    def conversation(self, agent_code, conversation_id):
        self.conversations.append(conversation_id)
        return self.ok


def add_conversation(conn, n, *, email=EMAIL, agent=AGENT, end=T0, is_open=False, synced=NOW):
    conn.execute(db.conversations.insert().values(
        id=cid(n), agent_code=agent, user_identifier=email, title=f"Conversación {n}", channel="FV IP",
        start_date=end - timedelta(minutes=5), end_date=end, is_open=is_open, message_count=4,
        content_hash="x", raw_key=f"raw/{agent}/{cid(n)}.json", first_synced_at=synced, last_synced_at=synced))
    msgs = [(ZERO, "bot", "¡Hola!"), (f"bbbbbbbb-0000-4000-8000-{n:012d}", "user", "Pregunta"),
            (f"cccccccc-0000-4000-8000-{n:012d}", "bot", "**Respuesta**"), (ZERO, "bot", "Aviso del sistema")]
    conn.execute(db.messages.insert(), [
        {"conversation_id": cid(n), "seq": i, "id": mid, "sender": sender, "type": "text", "value": value,
         "created_at": end - timedelta(minutes=5 - i), "canceled_by_client": False, "is_interruption": False,
         "attachments": []} for i, (mid, sender, value) in enumerate(msgs)])


@pytest.fixture
def env(tmp_path):
    engine = db.make_engine(f"sqlite:///{tmp_path / 't.db'}")
    with engine.begin() as conn:
        add_conversation(conn, 1, end=T0)
        add_conversation(conn, 2, end=T0 + timedelta(hours=1))
        add_conversation(conn, 3, end=T0 + timedelta(hours=1))  # misma última actividad que la 2
        add_conversation(conn, 4, end=NOW - timedelta(minutes=10), is_open=True, synced=NOW - timedelta(minutes=5))
        add_conversation(conn, 5, email="otra.persona@example.com")
        add_conversation(conn, 6, agent=OTHER_AGENT)
        conn.execute(db.message_feedback.insert().values(
            id="f1", agent_code=AGENT, conversation_id=cid(1), message_id=f"cccccccc-0000-4000-8000-{1:012d}",
            is_positive=False, comment="Poco clara", created_at=T0, last_synced_at=NOW))
    sync = FakeSync()
    app.dependency_overrides[get_store] = lambda: DbStore(engine, sync, sync_agents={AGENT})
    yield engine, sync, TestClient(app)
    app.dependency_overrides.clear()


def search(client, **body):
    return client.post(SEARCH, json={"email": EMAIL, "agent_code": AGENT, **body}, headers=AUTH)


def add_run(engine, agent=AGENT, finished=NOW, status="ok"):
    with engine.begin() as conn:
        conn.execute(db.runs.insert().values(agent_code=agent, kind="ingest", started_at=finished,
                                             finished_at=finished, status=status))


def test_busqueda_solo_del_alumno_y_agente_ordenada(env):
    engine, sync, client = env
    r = client.post(SEARCH, json={"email": "Alumno.Ficticio@Example.com", "agent_code": AGENT}, headers=AUTH)
    data = r.json()
    assert [i["id"] for i in data["items"]] == [cid(4), cid(3), cid(2), cid(1)]
    assert data["items"][0]["vertical"] == "forvide" and data["next_cursor"] is None
    assert sync.students == [(AGENT, EMAIL)] and data["stale"] is False


def test_paginacion_con_empates_de_fecha(env):
    _, _, client = env
    seen, cursor = [], None
    while True:
        data = search(client, limit=1, **({"cursor": cursor} if cursor else {})).json()
        seen += [i["id"] for i in data["items"]]
        cursor = data["next_cursor"]
        if not cursor:
            break
    assert seen == [cid(4), cid(3), cid(2), cid(1)]


def test_filtros(env):
    _, _, client = env
    assert [i["id"] for i in search(client, open=True).json()["items"]] == [cid(4)]
    since = (T0 + timedelta(minutes=30)).isoformat()
    assert len(search(client, last_activity_from=since).json()["items"]) == 3


def test_synced_at_es_la_sincronizacion_mas_reciente(env):
    engine, _, client = env
    assert search(client).json()["synced_at"] is None
    add_run(engine, finished=NOW - timedelta(minutes=3))
    add_run(engine, finished=NOW - timedelta(minutes=1), status="error")  # no cuenta
    synced = datetime.fromisoformat(search(client).json()["synced_at"])
    assert synced == NOW - timedelta(minutes=3)


def test_stale_si_falla_la_lectura_sincrona(env):
    _, sync, client = env
    sync.ok = False
    data = search(client).json()
    assert data["stale"] is True and len(data["items"]) == 4  # se devuelven los datos guardados


def test_sin_lectura_sincrona_en_agentes_no_sincronizados(env):
    _, sync, client = env
    r = client.post(SEARCH, json={"email": EMAIL, "agent_code": OTHER_AGENT}, headers=AUTH)
    assert [i["id"] for i in r.json()["items"]] == [cid(6)] and sync.students == []


def test_detalle_con_mensajes_y_feedback(env):
    _, sync, client = env
    d = client.get(f"/v1/conversations/{cid(1)}", headers=AUTH).json()
    assert d["student_email"] == EMAIL and d["vertical"] == "forvide"
    assert [m["seq"] for m in d["messages"]] == [0, 1, 2, 3]
    assert [m["is_welcome"] for m in d["messages"]] == [True, False, False, False]
    assert d["messages"][2]["feedback"] == {"is_positive": False, "comment": "Poco clara", "created_at": d["messages"][2]["feedback"]["created_at"]}
    assert d["messages"][3]["id"] == ZERO and d["messages"][3]["feedback"] is None
    assert sync.conversations == []  # cerrada: no se refresca


def test_detalle_abierta_y_antigua_se_refresca(env):
    _, sync, client = env
    d = client.get(f"/v1/conversations/{cid(4)}", headers=AUTH).json()
    assert sync.conversations == [cid(4)] and d["stale"] is False
    sync.ok = False
    assert client.get(f"/v1/conversations/{cid(4)}", headers=AUTH).json()["stale"] is True


def test_detalle_inexistente(env):
    _, _, client = env
    assert client.get(f"/v1/conversations/{cid(99)}", headers=AUTH).status_code == 404


def test_health_degradado_sin_ingesta_reciente(env):
    engine, _, client = env
    assert client.get("/v1/health").json()["status"] == "degraded"
    add_run(engine, finished=NOW - timedelta(minutes=2))
    assert client.get("/v1/health").json()["status"] == "ok"


def test_token_fijo(env, monkeypatch):
    _, _, client = env
    monkeypatch.setattr(security.settings, "api_token", "token-fijo-ficticio")
    monkeypatch.setattr(security.settings, "client_id", "cliente-ficticio")
    monkeypatch.setattr(security.settings, "client_secret", "secreto-ficticio")
    assert search(client).status_code == 401  # token-de-prueba ya no vale
    bad = client.post("/mock/oauth2/token", data={"grant_type": "client_credentials"},
                      auth=("cliente-ficticio", "otro"))
    assert bad.status_code == 401
    tok = client.post("/mock/oauth2/token", data={"grant_type": "client_credentials"},
                      auth=("cliente-ficticio", "secreto-ficticio")).json()["access_token"]
    r = client.post(SEARCH, json={"email": EMAIL, "agent_code": AGENT}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
