r"""Regenera deliverable-v1/iricom/: copia la especificación exportada y genera los ejemplos con el servidor
de prueba (datos ficticios). Uso, desde la raíz:  .venv\Scripts\python.exe api/scripts/export_deliverable.py
"""
import json, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
subprocess.run([sys.executable, str(ROOT / "api" / "scripts" / "export_openapi.py")], check=True, cwd=ROOT / "api")
DEST = ROOT / "deliverable-v1" / "iricom"
for name in ("openapi.yaml", "openapi.json"):
    shutil.copy(ROOT / "api" / "openapi" / name, DEST / name)
from fastapi.testclient import TestClient
from app.main import app
out = DEST / "ejemplos"
out.mkdir(exist_ok=True)
c = TestClient(app)
H = {"Authorization": "Bearer token-de-prueba"}
def save(name, req, resp):
    data = {"peticion": req, "respuesta": {"status": resp.status_code,
            "content_type": resp.headers.get("content-type"), "body": resp.json()}}
    (out / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(name, resp.status_code)
body = {"email": "alumna.ejemplo@example.com", "agent_code": "OrquestaAYIIPP", "limit": 2}
r = c.post("/v1/students/conversations/search", json=body, headers=H)
save("01-busqueda-primera-pagina", {"method": "POST", "path": "/v1/students/conversations/search", "body": body}, r)
body2 = {**body, "cursor": r.json()["next_cursor"]}
r2 = c.post("/v1/students/conversations/search", json=body2, headers=H)
save("02-busqueda-pagina-siguiente", {"method": "POST", "path": "/v1/students/conversations/search", "body": body2}, r2)
cid = r.json()["items"][0]["id"]
r3 = c.get(f"/v1/conversations/{cid}", headers=H)
save("03-detalle-conversacion", {"method": "GET", "path": f"/v1/conversations/{cid}"}, r3)
b4 = {"email": "nadie@example.com", "agent_code": "OrquestaAYIIPP"}
save("04-alumno-sin-conversaciones", {"method": "POST", "path": "/v1/students/conversations/search", "body": b4},
     c.post("/v1/students/conversations/search", json=b4, headers=H))
save("05-error-401-sin-token", {"method": "GET", "path": f"/v1/conversations/{cid}", "nota": "sin cabecera Authorization"},
     c.get(f"/v1/conversations/{cid}"))
missing = "00000000-0000-4000-8000-000000000000"
save("06-error-404-no-existe", {"method": "GET", "path": f"/v1/conversations/{missing}"}, c.get(f"/v1/conversations/{missing}", headers=H))
b7 = {"email": "no-es-un-email", "agent_code": "AgenteInexistente"}
save("07-error-422-validacion", {"method": "POST", "path": "/v1/students/conversations/search", "body": b7},
     c.post("/v1/students/conversations/search", json=b7, headers=H))
save("08-health", {"method": "GET", "path": "/v1/health"}, c.get("/v1/health"))
