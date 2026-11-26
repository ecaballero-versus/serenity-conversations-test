"""Prototipo local de ingesta (fase 1).

Uso, desde la raíz del proyecto:
  .venv\\Scripts\\python.exe -m ingest run [--agent OrquestaAYIIPP] [--days 7] [--rps 3] [--dry-run] [--rescan-days N]
  .venv\\Scripts\\python.exe -m ingest loop [--interval 300] [mismas opciones que run]
  .venv\\Scripts\\python.exe -m ingest status [--agent OrquestaAYIIPP]

`loop` simula la ingesta periódica de AWS (EventBridge cada 5 minutos): ejecuta un ciclo, espera y repite
hasta Ctrl+C.

Sin --agent se sincronizan los orquestadores con sync_enabled: true en config/agents.yaml.
Datos en LOCAL_DATA_DIR (por defecto ./data): base de datos SQLite y JSON originales.
"""
import argparse
import logging
import os
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from sqlalchemy import func, select

from . import db
from .client import SerenityClient
from .storage import LocalRawStore
from .sync import run_ingest

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.environ.get("LOCAL_DATA_DIR", ROOT / "data"))
log = logging.getLogger("ingest")


def load_agents(engine) -> list[dict]:
    """Carga config/agents.yaml en la tabla de agentes y lo devuelve."""
    agents = yaml.safe_load((ROOT / "config" / "agents.yaml").read_text(encoding="utf-8"))["agents"]
    with engine.begin() as conn:
        for a in agents:
            db.upsert(conn, db.agents, a, keys=["agent_code"])
    return agents


def cmd_run(engine, args) -> int:
    agents = load_agents(engine)
    known = {a["agent_code"] for a in agents}
    codes = [args.agent] if args.agent else [a["agent_code"] for a in agents if a["sync_enabled"]]
    unknown = [c for c in codes if c not in known]
    if unknown:
        log.error("Agentes que no están en config/agents.yaml: %s", unknown)
        return 2
    client = SerenityClient(os.environ["SERENITY_BASE_URL"], os.environ["SERENITY_API_KEY"], rps=args.rps)
    store = LocalRawStore(DATA_DIR)
    exit_code = 0
    for code in codes:
        try:
            stats = run_ingest(engine, client, store, code, bootstrap_days=args.days, dry_run=args.dry_run,
                               rescan_days=args.rescan_days)
            exit_code = exit_code or (1 if stats.failed else 0)
        except Exception as exc:  # noqa: BLE001
            log.error("%s: ciclo fallido: %s", code, type(exc).__name__)
            exit_code = 1
    return exit_code


def cmd_loop(engine, args) -> int:
    log.info("Ingesta periódica cada %s s. Ctrl+C para parar.", args.interval)
    try:
        while True:
            started = time.monotonic()
            cmd_run(engine, args)  # un ciclo fallido no detiene el bucle: se reintenta en el siguiente
            wait = max(0.0, args.interval - (time.monotonic() - started))
            log.info("Siguiente ciclo en %.0f s", wait)
            time.sleep(wait)
    except KeyboardInterrupt:
        log.info("Ingesta periódica detenida")
        return 0


def cmd_status(engine, args) -> int:
    load_agents(engine)
    c, m, f, p = db.conversations, db.messages, db.message_feedback, db.pending
    with engine.connect() as conn:
        agents = [args.agent] if args.agent else list(conn.execute(select(db.agents.c.agent_code)).scalars())
        for code in agents:
            convs = conn.execute(select(func.count(), func.sum(c.c.is_open.cast(db.Integer)),
                                        func.count(func.distinct(c.c.user_identifier)),
                                        func.min(c.c.start_date), func.max(c.c.end_date))
                                 .where(c.c.agent_code == code)).one()
            if not convs[0]:
                continue
            msgs = conn.execute(select(func.count()).select_from(m.join(c, m.c.conversation_id == c.c.id))
                                .where(c.c.agent_code == code)).scalar()
            fb = conn.execute(select(func.count(), func.sum(f.c.is_positive.cast(db.Integer)))
                              .where(f.c.agent_code == code)).one()
            pend = conn.execute(select(func.count()).where(p.c.agent_code == code)).scalar()
            wm = conn.execute(select(db.agent_state.c.watermark).where(db.agent_state.c.agent_code == code)).scalar()
            print(f"\n{code}")
            print(f"  conversaciones {convs[0]} (abiertas {convs[1] or 0}, alumnos distintos {convs[2]})")
            print(f"  actividad entre {convs[3]:%Y-%m-%d %H:%M} y {convs[4]:%Y-%m-%d %H:%M} UTC")
            print(f"  mensajes {msgs} | feedback {fb[0]} (positivo {fb[1] or 0}) | pendientes {pend}")
            print(f"  marca de agua {wm:%Y-%m-%d %H:%M:%S} UTC" if wm else "  sin marca de agua")
        print("\nÚltimas ejecuciones:")
        r = db.runs.c
        for row in conn.execute(select(r.id, r.agent_code, r.started_at, r.finished_at, r.status, r.listed, r.fetched,
                                       r.unchanged, r.failed, r.feedback_upserted, r.feedback_deleted,
                                       r.requests_made).order_by(r.id.desc()).limit(10)):
            secs = (row.finished_at - row.started_at).total_seconds() if row.finished_at else None
            print(f"  #{row.id} {row.agent_code} {row.started_at:%Y-%m-%d %H:%M} {row.status:<8} "
                  f"listadas {row.listed} descargadas {row.fetched} sin cambios {row.unchanged} fallidas {row.failed} "
                  f"feedback +{row.feedback_upserted}/-{row.feedback_deleted} peticiones {row.requests_made} "
                  f"({secs:.0f} s)" if secs is not None else f"  #{row.id} {row.status}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="ingest")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_opts = argparse.ArgumentParser(add_help=False)
    run_opts.add_argument("--agent", help="Código del orquestador (por defecto, los habilitados)")
    run_opts.add_argument("--days", type=int, default=7, help="Ventana de la primera ejecución (días)")
    run_opts.add_argument("--rps", type=float, default=3.0, help="Peticiones por segundo a Serenity")
    run_opts.add_argument("--dry-run", action="store_true", help="Solo lista y cuenta; no descarga ni guarda")
    run_opts.add_argument("--rescan-days", type=int,
                          help="Relee el listado de los últimos N días aunque haya marca de agua")
    sub.add_parser("run", parents=[run_opts], help="Ejecuta un ciclo de ingesta")
    loop = sub.add_parser("loop", parents=[run_opts], help="Ejecuta ciclos de ingesta periódicamente")
    loop.add_argument("--interval", type=int, default=300, help="Segundos entre ciclos (por defecto 300)")
    status = sub.add_parser("status", help="Resumen de lo sincronizado")
    status.add_argument("--agent")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    engine = db.make_engine(f"sqlite:///{DATA_DIR / 'serenity.db'}")
    if args.cmd == "loop" and args.rescan_days:
        parser.error("--rescan-days no tiene sentido en loop: úsalo con run")
    return {"run": cmd_run, "loop": cmd_loop, "status": cmd_status}[args.cmd](engine, args)


if __name__ == "__main__":
    sys.exit(main())
