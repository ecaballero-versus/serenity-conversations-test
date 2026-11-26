"""Orquestadores conocidos, desde config/agents.yaml (la misma configuración que usa la ingesta)."""
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config" / "agents.yaml"
_AGENTS = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["agents"]

# agent_code -> vertical
AGENTS: dict[str, str] = {a["agent_code"]: a["vertical"] for a in _AGENTS}
# Orquestadores que sincroniza la ingesta: solo en ellos se aplica la lectura síncrona
SYNC_ENABLED: set[str] = {a["agent_code"] for a in _AGENTS if a.get("sync_enabled")}
