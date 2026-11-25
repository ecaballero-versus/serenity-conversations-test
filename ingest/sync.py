"""Ciclo de ingesta incremental de un orquestador.

1. Listado por última actividad descendente, hasta encontrar una conversación anterior a
   `marca de agua - solape` (o a `ahora - bootstrap_days` en la primera ejecución).
2. Detalle solo de las conversaciones nuevas o con actividad nueva (fecha de fin distinta de la guardada),
   más las que fallaron en ciclos anteriores (`sync_pending`).
3. Guardado del JSON original y del modelo consultable, una transacción por conversación.
4. Avance de la marca de agua. Los fallos quedan en `sync_pending` y se reintentan en el siguiente ciclo.
5. Reconciliación del feedback de los últimos 7 días: insertar, actualizar y borrar lo que ya no exista.

Los logs solo contienen recuentos e ids de conversación, nunca emails ni contenido.
"""
import hashlib
import logging
from dataclasses import asdict, dataclass
from datetime import timedelta

from sqlalchemy import bindparam, delete, insert, select, true, update
from sqlalchemy.engine import Engine

from . import db
from .client import NotFound, SerenityClient, SerenityError
from .storage import LocalRawStore
from .timeutil import parse_utc, to_param, utcnow

log = logging.getLogger(__name__)

PAGE_SIZE = 200
OVERLAP = timedelta(minutes=10)
FEEDBACK_WINDOW = timedelta(days=7)
MAX_PENDING_ATTEMPTS = 10


@dataclass
class RunStats:
    listed: int = 0
    fetched: int = 0
    unchanged: int = 0
    failed: int = 0
    not_found: int = 0
    feedback_upserted: int = 0
    feedback_deleted: int = 0


def _list_changed(client: SerenityClient, agent_code: str, lower_bound) -> dict[str, dict]:
    """Conversaciones con última actividad >= lower_bound: id -> {end_date, channel, agent_version}."""
    found: dict[str, dict] = {}
    page = 1
    while True:
        data = client.list_conversations(agent_code, page, PAGE_SIZE)
        items = data.get("items") or []
        reached_older = False
        for item in items:
            end = parse_utc(item["endDate"])
            if end < lower_bound:
                reached_older = True
                break
            # Si una conversación recibe actividad mientras se pagina, puede aparecer dos veces: se deduplica
            found.setdefault(item["id"], {"end_date": end, "channel": item.get("channel"),
                                          "agent_version": item.get("agentVersion")})
        if reached_older or len(items) < PAGE_SIZE or page * PAGE_SIZE >= (data.get("total") or 0):
            return found
        page += 1


def _save_conversation(engine: Engine, store: LocalRawStore, agent_code: str, detail: dict, raw: bytes,
                       meta: dict | None) -> None:
    """meta: campos que solo trae el listado (channel, agent_version). None si no se ha listado."""
    now = utcnow()
    conv_id = detail["id"]
    content_hash = hashlib.sha256(raw).hexdigest()
    key = store.key(agent_code, conv_id)
    msgs = detail.get("messages") or []

    with engine.begin() as conn:
        existing = conn.execute(select(db.conversations.c.content_hash, db.conversations.c.channel,
                                       db.conversations.c.agent_version, db.conversations.c.first_synced_at)
                                .where(db.conversations.c.id == conv_id)).first()
        if existing and existing.content_hash == content_hash:
            conn.execute(update(db.conversations).where(db.conversations.c.id == conv_id)
                         .values(last_synced_at=now))
            return
        store.put(key, raw)
        db.upsert(conn, db.conversations, {
            "id": conv_id,
            "agent_code": agent_code,
            "user_identifier": (detail.get("userIdentifier") or "").strip().lower(),
            "title": detail.get("name"),
            "channel": meta["channel"] if meta else (existing.channel if existing else None),
            "agent_version": meta["agent_version"] if meta else (existing.agent_version if existing else None),
            "start_date": parse_utc(detail["startDate"]),
            "end_date": parse_utc(detail["endDate"]),
            "is_open": bool(detail.get("open")),
            "message_count": len(msgs),
            "content_hash": content_hash,
            "raw_key": key,
            "first_synced_at": existing.first_synced_at if existing else now,
            "last_synced_at": now,
        }, keys=["id"])
        # Los mensajes no se editan en Serenity, pero se reescriben enteros para mantener seq coherente
        conn.execute(delete(db.messages).where(db.messages.c.conversation_id == conv_id))
        if msgs:
            conn.execute(insert(db.messages), [{
                "conversation_id": conv_id,
                "id": m["id"],
                "seq": seq,
                "sender": m.get("sender"),
                "type": str(m.get("type")) if m.get("type") is not None else None,
                "value": m.get("value"),
                "created_at": parse_utc(m["createdAt"]),
                "canceled_by_client": bool(m.get("canceledByClient")),
                "is_interruption": bool(m.get("isInterruption")),
                "attachments": m.get("attachedVolatileKnowledges") or [],
            } for seq, m in enumerate(msgs)])
        conn.execute(delete(db.pending).where(db.pending.c.agent_code == agent_code,
                                              db.pending.c.conversation_id == conv_id))


def _safe_error(exc: Exception) -> str:
    """Descripción del error sin datos de alumnos.

    Los errores de base de datos incluyen los parámetros SQL (emails, contenido), así que solo se guarda
    el tipo. Los errores del cliente de Serenity se construyen sin cuerpos de respuesta.
    """
    if isinstance(exc, SerenityError):
        return f"SerenityError: {exc}"
    orig = getattr(exc, "orig", None)  # errores de SQLAlchemy: tipo del error del driver
    return f"{type(exc).__name__}" + (f" ({type(orig).__name__})" if orig is not None else "")


def _mark_pending(engine: Engine, agent_code: str, conv_id: str, error: str) -> None:
    now = utcnow()
    with engine.begin() as conn:
        row = conn.execute(select(db.pending.c.attempts, db.pending.c.first_failed_at)
                           .where(db.pending.c.agent_code == agent_code,
                                  db.pending.c.conversation_id == conv_id)).first()
        db.upsert(conn, db.pending, {
            "agent_code": agent_code, "conversation_id": conv_id,
            "attempts": (row.attempts if row else 0) + 1, "last_error": error[:500],
            "first_failed_at": row.first_failed_at if row else now, "last_failed_at": now,
        }, keys=["agent_code", "conversation_id"])


def _reconcile_feedback(engine: Engine, client: SerenityClient, agent_code: str, stats: RunStats) -> None:
    now = utcnow()
    window_start = now - FEEDBACK_WINDOW
    remote: dict[str, dict] = {}
    page, total = 1, None
    while True:
        data = client.list_feedback(agent_code, to_param(window_start), page, PAGE_SIZE)
        items = data.get("items") or []
        total = data.get("total") or 0
        for it in items:
            remote[it["id"]] = it
        if len(items) < PAGE_SIZE or page * PAGE_SIZE >= total:
            break
        page += 1
    if len(remote) != total:
        # Listado incompleto o cambiante: se actualiza lo recibido, pero no se borra nada
        log.warning("Feedback: recibidos %s de %s; se omite el borrado", len(remote), total)

    with engine.begin() as conn:
        for it in remote.values():
            db.upsert(conn, db.message_feedback, {
                "id": it["id"], "agent_code": agent_code, "conversation_id": it["conversationId"],
                "message_id": it["agentMessageId"], "is_positive": bool(it.get("feedback")),
                "comment": it.get("comment"), "created_at": parse_utc(it["dateUtc"]), "last_synced_at": now,
            }, keys=["id"])
        stats.feedback_upserted = len(remote)
        if len(remote) == total:
            # Una valoración retirada desaparece de Serenity. Solo se borra dentro de la ventana releída.
            result = conn.execute(delete(db.message_feedback).where(
                db.message_feedback.c.agent_code == agent_code,
                db.message_feedback.c.created_at >= window_start,
                db.message_feedback.c.id.not_in(list(remote)) if remote else true(),
            ))
            stats.feedback_deleted = result.rowcount


def run_ingest(engine: Engine, client: SerenityClient, store: LocalRawStore, agent_code: str,
               bootstrap_days: int = 7, dry_run: bool = False, rescan_days: int | None = None) -> RunStats:
    """rescan_days: relee el listado de esa ventana aunque haya marca de agua (p. ej. para rellenar
    metadatos del listado). Solo descarga el detalle de lo que haya cambiado."""
    stats = RunStats()
    started = utcnow()
    with engine.begin() as conn:
        watermark = conn.execute(select(db.agent_state.c.watermark)
                                 .where(db.agent_state.c.agent_code == agent_code)).scalar()
        pending_ids = [] if dry_run else list(conn.execute(
            select(db.pending.c.conversation_id).where(db.pending.c.agent_code == agent_code,
                                                       db.pending.c.attempts < MAX_PENDING_ATTEMPTS)).scalars())
    if rescan_days:
        lower_bound, mode = started - timedelta(days=rescan_days), f"relectura ({rescan_days} días)"
    elif watermark:
        lower_bound, mode = watermark - OVERLAP, "incremental"
    else:
        lower_bound, mode = started - timedelta(days=bootstrap_days), f"inicial ({bootstrap_days} días)"
    log.info("%s: ciclo %s, actividad desde %s", agent_code, mode, lower_bound.isoformat(timespec="seconds"))

    run_id = None
    if not dry_run:
        with engine.begin() as conn:
            run_id = conn.execute(insert(db.runs).values(agent_code=agent_code, kind="ingest", started_at=started,
                                                         status="running", lower_bound=lower_bound)
                                  ).inserted_primary_key[0]
    status, error = "ok", None
    try:
        changed = _list_changed(client, agent_code, lower_bound)
        stats.listed = len(changed)

        with engine.connect() as conn:
            stored = dict(conn.execute(select(db.conversations.c.id, db.conversations.c.end_date)
                                       .where(db.conversations.c.id.in_(list(changed)))).all()) if changed else {}
        to_fetch = [cid for cid, info in changed.items() if stored.get(cid) != info["end_date"]]
        stats.unchanged = len(changed) - len(to_fetch)
        to_fetch += [cid for cid in pending_ids if cid not in to_fetch]
        log.info("Listado: %s con actividad en la ventana, %s sin cambios, %s a descargar (%s pendientes)",
                 stats.listed, stats.unchanged, len(to_fetch), len(pending_ids))

        if dry_run:
            return stats

        # Metadatos del listado de las conversaciones sin cambios (el detalle no los trae)
        unchanged_meta = [{"cid": cid, "channel": info["channel"], "agent_version": info["agent_version"]}
                          for cid, info in changed.items() if cid in stored and cid not in to_fetch]
        if unchanged_meta:
            with engine.begin() as conn:
                conn.execute(update(db.conversations).where(db.conversations.c.id == bindparam("cid"))
                             .values(channel=bindparam("channel"), agent_version=bindparam("agent_version")),
                             unchanged_meta)

        for i, conv_id in enumerate(to_fetch, 1):
            try:
                detail, raw = client.get_conversation(agent_code, conv_id)
                _save_conversation(engine, store, agent_code, detail, raw, changed.get(conv_id))
                stats.fetched += 1
            except NotFound:
                # Puede haber caducado por la retención de Serenity entre el listado y el detalle
                stats.not_found += 1
                with engine.begin() as conn:
                    conn.execute(delete(db.pending).where(db.pending.c.agent_code == agent_code,
                                                          db.pending.c.conversation_id == conv_id))
            except Exception as exc:  # noqa: BLE001 - un fallo no detiene el ciclo
                stats.failed += 1
                _mark_pending(engine, agent_code, conv_id, _safe_error(exc))
                log.warning("Conversación %s fallida: %s", conv_id, type(exc).__name__)
            if i % 50 == 0:
                log.info("Progreso: %s/%s", i, len(to_fetch))

        if changed:
            new_watermark = max(info["end_date"] for info in changed.values())
            if watermark:
                new_watermark = max(new_watermark, watermark)
            with engine.begin() as conn:
                db.upsert(conn, db.agent_state, {"agent_code": agent_code, "watermark": new_watermark,
                                                 "updated_at": utcnow()}, keys=["agent_code"])

        _reconcile_feedback(engine, client, agent_code, stats)
        if stats.failed:
            status = "partial"
    except Exception as exc:
        status, error = "error", _safe_error(exc)
        raise
    finally:
        if run_id is not None:
            with engine.begin() as conn:
                conn.execute(update(db.runs).where(db.runs.c.id == run_id).values(
                    finished_at=utcnow(), status=status, error=error, requests_made=client.requests_made,
                    rate_limit_remaining=client.rate_limit_remaining, **asdict(stats)))
    log.info("Fin (%s): %s | peticiones %s | remanente del límite %s", status, asdict(stats),
             client.requests_made, client.rate_limit_remaining)
    return stats
