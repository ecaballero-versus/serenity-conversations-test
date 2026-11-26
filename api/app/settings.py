"""Configuración del servidor local, desde variables de entorno o el `.env` de la raíz del proyecto.

- `API_BACKEND`: `mock` (datos ficticios; por defecto) o `db` (base local de la ingesta).
- `LOCAL_DATA_DIR`: carpeta de datos de la ingesta (por defecto `./data`), solo con `db`.
- `API_TOKEN`: token fijo que sustituye a Cognito. Si está vacío se acepta cualquier token (modo de prueba).
- `API_CLIENT_ID` / `API_CLIENT_SECRET`: credenciales que acepta el endpoint de tokens simulado para entregar
  `API_TOKEN` (para probar el flujo client credentials desde /docs).
- `API_SYNC_ON_DEMAND`: `true` (por defecto) para la lectura síncrona contra Serenity, solo con `db`.
  Necesita `SERENITY_BASE_URL` y `SERENITY_API_KEY`.

Las variables ya definidas en el entorno tienen prioridad sobre el `.env` (los tests fuerzan `mock`).
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")
# El modo `db` reutiliza el paquete `ingest` de la raíz del proyecto (modelo de datos y lectura síncrona)
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    return default if not value else value in {"1", "true", "yes", "si", "sí"}


@dataclass
class Settings:
    backend: str
    data_dir: Path
    api_token: str
    client_id: str
    client_secret: str
    sync_on_demand: bool
    serenity_base_url: str
    serenity_api_key: str


def load() -> Settings:
    backend = os.environ.get("API_BACKEND", "mock").strip().lower()
    if backend not in {"mock", "db"}:
        raise RuntimeError(f"API_BACKEND debe ser 'mock' o 'db', no '{backend}'")
    return Settings(
        backend=backend,
        data_dir=Path(os.environ.get("LOCAL_DATA_DIR") or ROOT / "data"),
        api_token=os.environ.get("API_TOKEN", "").strip(),
        client_id=os.environ.get("API_CLIENT_ID", "").strip(),
        client_secret=os.environ.get("API_CLIENT_SECRET", "").strip(),
        sync_on_demand=_flag("API_SYNC_ON_DEMAND", True),
        serenity_base_url=os.environ.get("SERENITY_BASE_URL", "").strip(),
        serenity_api_key=os.environ.get("SERENITY_API_KEY", "").strip(),
    )


settings = load()
