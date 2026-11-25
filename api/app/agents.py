"""Orquestadores conocidos, leídos de config/agents.yaml (misma configuración que la ingesta)."""
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config" / "agents.yaml"

# agent_code -> vertical
AGENTS: dict[str, str] = {
    a["agent_code"]: a["vertical"]
    for a in yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["agents"]
}
