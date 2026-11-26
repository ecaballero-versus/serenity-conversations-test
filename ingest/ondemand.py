"""Lectura síncrona (bajo demanda): la API la invoca antes de responder para incluir la actividad más
reciente, sin esperar al siguiente ciclo de la ingesta periódica.

- `sync_student`: novedades de un alumno con un orquestador. Como mucho una consulta a Serenity por minuto y
  alumno (`sync_student_state`). Lista solo las conversaciones del alumno (filtro `userIdentifier`) hasta su
  marca de agua y descarga las nuevas o con actividad nueva.
- `refresh_conversation`: vuelve a descargar una conversación concreta.

Devuelven True si los datos guardados están al día y False si algo falló (la API responde `stale: true`).
Las conversaciones que fallan quedan en `sync_pending` y las reintenta la ingesta periódica.
En AWS esto será la Lambda de sincronización: la API no tendrá la API key de Serenity.
Los logs no contienen emails.
"""
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.engine import Engine

from . import db
from .client import SerenityClient
from .storage import LocalRawStore
from .sync import OVERLAP, _list_changed, _stored_end_dates, fetch_one
from .timeutil import utcnow

log = logging.getLogger(__name__)

STUDENT_MIN_INTERVAL = timedelta(seconds=60)


def sync_student(engine: Engine, client: SerenityClient, store: LocalRawStore, agent_code: str, email: str,
                 min_interval: timedelta = STUDENT_MIN_INTERVAL) -> bool:
    email = email.strip().lower()
    now = utcnow()
    st = db.student_state.c
    with engine.connect() as conn:
        state = conn.execute(select(st.last_synced_at, st.watermark)
                             .where(st.agent_code == agent_code, st.user_identifier == email)).first()
    if state and now - state.last_synced_at < min_interval:
        return True

    # Sin estado previo se listan todas las conversaciones del alumno (pocas: las limita la retención)
    lower_bound = state.watermark - OVERLAP if state and state.watermark else None
    changed = _list_changed(client, agent_code, lower_bound, user_identifier=email)
    stored = _stored_end_dates(engine, changed)
    to_fetch = [cid for cid, info in changed.items() if stored.get(cid) != info["end_date"]]
    results = [fetch_one(engine, client, store, agent_code, cid, changed[cid]) for cid in to_fetch]
    failed = results.count("failed")

    seen = [info["end_date"] for info in changed.values()]
    if state and state.watermark:
        seen.append(state.watermark)
    if not failed:
        # Con fallos no se registra la sincronización: el siguiente intento vuelve a consultar Serenity
        with engine.begin() as conn:
            db.upsert(conn, db.student_state, {"agent_code": agent_code, "user_identifier": email,
                                               "last_synced_at": now, "watermark": max(seen, default=None)},
                      keys=["agent_code", "user_identifier"])
    log.info("Lectura síncrona %s: %s listadas, %s descargadas, %s fallidas",
             agent_code, len(changed), results.count("ok"), failed)
    return not failed


def refresh_conversation(engine: Engine, client: SerenityClient, store: LocalRawStore, agent_code: str,
                         conv_id: str) -> bool:
    # "not_found": caducada en Serenity; la copia guardada es la última que existe
    return fetch_one(engine, client, store, agent_code, conv_id, None) != "failed"
