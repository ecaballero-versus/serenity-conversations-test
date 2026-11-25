"""Tests del contrato (fase 0). Solo datos ficticios."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app, export_openapi
from app.models import ConversationDetail, ConversationSearchResponse, Problem

client = TestClient(app)
AUTH = {"Authorization": "Bearer token-de-prueba"}
STUDENT = "alumna.ejemplo@example.com"
CONV_ID = "3f6c2a10-8b4e-4d21-9c7a-1e2f3a4b5c01"
SEARCH = "/v1/students/conversations/search"


def search(**body):
    return client.post(SEARCH, json={"email": STUDENT, "agent_code": "OrquestaAYIIPP", **body}, headers=AUTH)


def assert_problem(response, status):
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/problem+json")
    problem = Problem.model_validate(response.json())
    assert problem.status == status
    return problem


def test_health_sin_token():
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.parametrize("method,url", [("post", SEARCH),
                                        ("get", f"/v1/conversations/{CONV_ID}")])
def test_401_sin_token(method, url):
    r = getattr(client, method)(url)
    assert_problem(r, 401)
    assert r.headers["www-authenticate"].startswith("Bearer")


def test_403_sin_scope():
    assert_problem(client.get(f"/v1/conversations/{CONV_ID}", headers={"Authorization": "Bearer token-sin-permiso"}), 403)


def test_resultados_incluyen_agente_y_vertical():
    item = search().json()["items"][0]
    assert item["agent_code"] == "OrquestaAYIIPP" and item["vertical"] == "ayudantes"


def test_otro_agente_del_mismo_alumno_no_se_mezcla():
    r = client.post(SEARCH, json={"email": STUDENT, "agent_code": "AgenteOrquestaGCIN"}, headers=AUTH)
    assert [c["agent_code"] for c in r.json()["items"]] == ["AgenteOrquestaGCIN"]


def test_busqueda_normaliza_email_y_ordena():
    r = client.post(SEARCH, json={"email": "Alumna.Ejemplo@Example.com", "agent_code": "OrquestaAYIIPP"}, headers=AUTH)
    data = ConversationSearchResponse.model_validate(r.json())
    assert len(data.items) == 3
    assert [c.end_date for c in data.items] == sorted((c.end_date for c in data.items), reverse=True)
    assert data.next_cursor is None and data.stale is False
    assert "messages" not in r.json()["items"][0]


def test_paginacion_con_cursor():
    first = ConversationSearchResponse.model_validate(search(limit=2).json())
    assert len(first.items) == 2 and first.next_cursor
    second = ConversationSearchResponse.model_validate(search(limit=2, cursor=first.next_cursor).json())
    assert len(second.items) == 1 and second.next_cursor is None
    assert not {c.id for c in first.items} & {c.id for c in second.items}


def test_filtros():
    assert all(c["open"] for c in search(open=True).json()["items"])
    items = search(last_activity_from="2026-11-15T00:00:00Z").json()["items"]
    assert len(items) == 2


def test_alumno_sin_conversaciones():
    r = client.post(SEARCH, json={"email": "nadie@example.com", "agent_code": "OrquestaAYIIPP"}, headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"items": [], "next_cursor": None, "synced_at": None, "stale": False}


@pytest.mark.parametrize("body,pointer", [
    ({"email": "no-es-un-email", "agent_code": "OrquestaAYIIPP"}, "/email"),
    ({"email": STUDENT, "agent_code": "Orquesta AYIIPP"}, "/agent_code"),
    ({"email": STUDENT}, "/agent_code"),
    ({"email": STUDENT, "agent_code": "OrquestaAYIIPP", "limit": 500}, "/limit"),
    ({"email": STUDENT, "agent_code": "OrquestaAYIIPP", "last_activity_from": "2026-11-20T00:00:00"},
     "/last_activity_from"),
    ({"email": STUDENT, "agent_code": "OrquestaAYIIPP", "campo_desconocido": 1}, "/campo_desconocido"),
])
def test_422_validacion(body, pointer):
    problem = assert_problem(client.post(SEARCH, json=body, headers=AUTH), 422)
    assert pointer in {e.pointer for e in problem.errors}
    # El valor recibido nunca se devuelve en el error
    assert "no-es-un-email" not in json.dumps(problem.model_dump())


def test_422_vertical_desconocida_y_cursor_invalido():
    assert_problem(client.post(SEARCH, json={"email": STUDENT, "agent_code": "AgenteInexistente"}, headers=AUTH), 422)
    assert_problem(search(cursor="basura"), 422)


def test_detalle():
    data = ConversationDetail.model_validate(client.get(f"/v1/conversations/{CONV_ID}", headers=AUTH).json())
    assert data.message_count == len(data.messages)
    assert data.messages[0].is_welcome and str(data.messages[0].id) == "00000000-0000-0000-0000-000000000000"
    assert [m.seq for m in data.messages] == list(range(len(data.messages)))
    assert all(m.feedback is None for m in data.messages if m.sender == "user")
    assert any(m.feedback and m.feedback.comment for m in data.messages)
    assert data.student_email == STUDENT


def test_fechas_utc_con_z():
    body = client.get(f"/v1/conversations/{CONV_ID}", headers=AUTH).json()
    assert body["start_date"].endswith("Z") and body["messages"][0]["created_at"].endswith("Z")


def test_404_y_422_en_detalle():
    assert_problem(client.get("/v1/conversations/00000000-0000-4000-8000-000000000000", headers=AUTH), 404)
    assert_problem(client.get("/v1/conversations/no-es-uuid", headers=AUTH), 422)


def test_especificacion():
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    assert "HTTPValidationError" not in schemas
    assert spec["components"]["securitySchemes"]["oauth2"]["flows"]["clientCredentials"]["scopes"]
    for path, ops in spec["paths"].items():
        for op in ops.values():
            for status, resp in op["responses"].items():
                if int(status) >= 400:
                    assert list(resp["content"]) == ["application/problem+json"], (path, status)
    # El email nunca aparece como parámetro de URL
    for ops in spec["paths"].values():
        for op in ops.values():
            assert all(p["name"] != "email" for p in op.get("parameters", []))


def test_ejemplos_validan_contra_los_modelos():
    spec = app.openapi()
    search_op = spec["paths"][SEARCH]["post"]
    for ex in search_op["responses"]["200"]["content"]["application/json"]["examples"].values():
        ConversationSearchResponse.model_validate(ex["value"])
    detail = spec["paths"]["/v1/conversations/{conversation_id}"]["get"]
    ConversationDetail.model_validate(detail["responses"]["200"]["content"]["application/json"]["example"])


def test_especificacion_exportada_al_dia():
    exported = Path(__file__).resolve().parent.parent / "openapi" / "openapi.json"
    if not exported.exists():
        pytest.skip("Ejecutar scripts/export_openapi.py")
    assert json.loads(exported.read_text(encoding="utf-8")) == json.loads(json.dumps(export_openapi()))


def test_docs_locales_apuntan_al_propio_servidor():
    spec = app.openapi()
    assert not spec.get("servers")
    flow = spec["components"]["securitySchemes"]["oauth2"]["flows"]["clientCredentials"]
    assert flow["tokenUrl"] == "/mock/oauth2/token"
    assert "/mock/oauth2/token" not in spec["paths"]


def test_exportada_con_valores_de_produccion():
    spec = export_openapi()
    assert spec["servers"][0]["url"].startswith("https://")
    assert spec["components"]["securitySchemes"]["oauth2"]["flows"]["clientCredentials"]["tokenUrl"].startswith("https://")
    assert not app.openapi().get("servers")  # exportar no modifica el esquema del servidor local


def test_token_simulado_permite_llamar_a_la_api():
    r = client.post("/mock/oauth2/token", data={"grant_type": "client_credentials", "client_id": "x",
                                                "client_secret": "y"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert client.get(f"/v1/conversations/{CONV_ID}", headers={"Authorization": f"Bearer {token}"}).status_code == 200
