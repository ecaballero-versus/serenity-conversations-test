"""Exporta la especificación OpenAPI a api/openapi/openapi.json y openapi.yaml.

Uso (desde la carpeta api/):  python scripts/export_openapi.py
"""
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

out_dir = ROOT / "openapi"
out_dir.mkdir(exist_ok=True)
spec = app.openapi()
(out_dir / "openapi.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
(out_dir / "openapi.yaml").write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"OpenAPI {spec['openapi']} exportado a {out_dir}")
