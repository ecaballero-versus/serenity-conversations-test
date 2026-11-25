"""Fechas de Serenity: siempre UTC.

Las fechas de conversaciones y feedback llegan sin zona y con 7 decimales
(`2026-11-20T10:15:02.1234567`); las de mensajes, con `+00:00`. Se recortan a microsegundos y se
devuelven como datetime con zona UTC.
"""
import re
from datetime import datetime, timezone

_PATTERN = re.compile(r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?P<frac>\.\d+)?(?P<tz>Z|[+-]\d{2}:?\d{2})?$")


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    m = _PATTERN.match(value.strip())
    if not m:
        raise ValueError(f"Fecha con formato inesperado: {value!r}")
    frac = (m["frac"] or ".0")[1:7].ljust(6, "0")
    tz = m["tz"] or "+00:00"
    if tz == "Z":
        tz = "+00:00"
    return datetime.fromisoformat(f"{m['base']}.{frac}{tz}").astimezone(timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_param(dt: datetime) -> str:
    """Formato para los parámetros startDate/endDate de Serenity."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
