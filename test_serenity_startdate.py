"""
Pruebas de la API de Serenity: conversaciones y feedback.

Uso (variables en el .env junto al script):
  SERENITY_API_KEY=...            # API key con rol auditor
  SERENITY_AGENT_CODE=...         # código del agente orquestador
  SERENITY_BASE_URL=https://api.serenitystar.ai

  python test_serenity_startdate.py list --since 2026-11-20T10:00:00Z [--user email]
  python test_serenity_startdate.py recent [--user email]
  python test_serenity_startdate.py detail --id <conversationId>
  python test_serenity_startdate.py raw --id <conversationId>
  python test_serenity_startdate.py feedback [--since 2026-11-23T00:00:00Z]
  python test_serenity_startdate.py scan [--n 50]        # estadísticas de las últimas N conversaciones

  # Escriben en Serenity: usar solo sobre conversaciones de prueba propias
  python test_serenity_startdate.py feedback-set --conv <id> --msg <id> --value up|down [--comment "texto"]
  python test_serenity_startdate.py feedback-delete --conv <id> --msg <id>
"""
import argparse
import json
import os
import statistics
import time
from collections import Counter
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# Carga el .env que está junto al script, sin depender del directorio desde el que se ejecute
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

BASE_URL = os.environ["SERENITY_BASE_URL"].rstrip("/")
AGENT = os.environ["SERENITY_AGENT_CODE"]
HEADERS = {"X-API-KEY": os.environ["SERENITY_API_KEY"]}


def get(path, params=None):
    r = requests.get(f"{BASE_URL}{path}", headers=HEADERS, params=params, timeout=30)
    if r.status_code == 429:
        print(f"429 recibido. Retry-After: {r.headers.get('Retry-After')}")
    if not r.ok:
        print(f"HTTP {r.status_code}: {r.text}")
    r.raise_for_status()
    return r.json()


def send(method, path, body=None):
    r = requests.request(method, f"{BASE_URL}{path}", headers=HEADERS, json=body, timeout=30)
    print(f"{method} {path} -> HTTP {r.status_code} {r.text[:300]}")
    r.raise_for_status()


def show_items(data):
    print(f"total={data.get('total')}  page={data.get('page')}  pageSize={data.get('pageSize')}")
    for it in data.get("items") or []:
        print(f"  {it['id']}  start={it['startDate']}  end={it['endDate']}  user={it.get('userIdentifier')}")


def with_user(params, user):
    if user:
        params["userIdentifier"] = user
    return params


def cmd_list(since, user=None):
    # Filtro por fecha de inicio; el orden ascendente es por fecha de fin
    show_items(get(f"/api/v2/AgentInstance/{AGENT}",
                   with_user({"startDate": since, "sortDirection": "asc", "pageSize": 50}, user)))


def cmd_recent(user=None):
    # Sin filtro de fecha, ordenado por endDate descendente (última actividad primero)
    show_items(get(f"/api/v2/AgentInstance/{AGENT}",
                   with_user({"sortDirection": "desc", "pageSize": 10}, user)))


def cmd_detail(conv_id):
    d = get(f"/api/v2/agent/{AGENT}/conversation/{conv_id}")
    msgs = d.get("messages") or []
    print(f"open={d.get('open')}  start={d.get('startDate')}  end={d.get('endDate')}  mensajes={len(msgs)}")
    for m in msgs[-3:]:
        print(f"  {m['createdAt']}  {m['sender']}: {(m.get('value') or '')[:60]!r}")


def cmd_raw(conv_id):
    # Guarda la respuesta completa para revisar todos los campos. Contiene datos personales: no la compartas.
    d = get(f"/api/v2/agent/{AGENT}/conversation/{conv_id}")
    out = f"conversation_{conv_id}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"Guardado en {out}. Campos de primer nivel: {sorted(d.keys())}")
    msgs = d.get("messages") or []
    if msgs:
        print(f"Campos de cada mensaje: {sorted(msgs[0].keys())}")
        print(f"Valores de sender: {sorted({m.get('sender') for m in msgs})}")
        print(f"Valores de type: {sorted({str(m.get('type')) for m in msgs})}")


def cmd_feedback(since=None):
    # Feedback del agente, el más reciente primero. Filtra y ordena por dateUtc.
    params = {"sortDirection": "desc", "pageSize": 10}
    if since:
        params["startDate"] = since
    d = get(f"/api/v2/Agent/{AGENT}/feedback", params)
    print(f"total={d.get('total')}  page={d.get('page')}  pageSize={d.get('pageSize')}")
    for it in d.get("items") or []:
        rating = "+" if it.get("feedback") else "-"
        print(f"  {it['id']}  {it.get('dateUtc')}  {rating}  conv={it.get('conversationId')}  "
              f"msg={it.get('agentMessageId')}  comment={(it.get('comment') or '')[:40]!r}")


EMPTY_GUID = "00000000-0000-0000-0000-000000000000"


def parse_utc(value):
    # Serenity devuelve fechas con 7 decimales y a veces sin zona: se recortan a microsegundos y se asume UTC
    v = value.replace("Z", "+00:00")
    tz = ""
    if "+" in v[10:]:
        v, tz = v[:v.rindex("+")], v[v.rindex("+"):]
    if "." in v:
        base, frac = v.split(".")
        v = f"{base}.{frac[:6]}"
    dt = datetime.fromisoformat(v + (tz or "+00:00"))
    return dt


def cmd_scan(n):
    # 1) Tamaño máximo de página que acepta el listado
    r = requests.get(f"{BASE_URL}/api/v2/AgentInstance/{AGENT}", headers=HEADERS,
                     params={"sortDirection": "desc", "pageSize": 200}, timeout=30)
    r.raise_for_status()
    print(f"pageSize=200 pedido -> {len(r.json().get('items') or [])} elementos devueltos "
          f"(pageSize en respuesta: {r.json().get('pageSize')})")
    rate_headers = {k: v for k, v in r.headers.items() if "rate" in k.lower() or "retry" in k.lower()}
    print(f"Cabeceras de límites en la respuesta: {rate_headers or 'ninguna'}")

    # 2) Detalle de las últimas N conversaciones, a 1 petición por segundo
    items = (r.json().get("items") or [])[:n]
    senders, types, latencies, sizes, msg_counts = Counter(), Counter(), [], [], []
    with_attach = canceled = interruptions = multi_empty = 0
    seen_ids, dup_ids, max_gap = set(), 0, 0.0
    for i, it in enumerate(items, 1):
        t0 = time.perf_counter()
        resp = requests.get(f"{BASE_URL}/api/v2/agent/{AGENT}/conversation/{it['id']}",
                            headers=HEADERS, timeout=30)
        latencies.append(time.perf_counter() - t0)
        if resp.status_code == 429:
            print(f"429 en la conversación {i}. Retry-After: {resp.headers.get('Retry-After')}")
            break
        resp.raise_for_status()
        sizes.append(len(resp.content))
        d = resp.json()
        msgs = d.get("messages") or []
        msg_counts.append(len(msgs))
        if sum(1 for m in msgs if m["id"] == EMPTY_GUID) > 1:
            multi_empty += 1
        for m in msgs:
            senders[m.get("sender")] += 1
            types[str(m.get("type"))] += 1
            if m.get("attachedVolatileKnowledges"):
                with_attach += 1
            canceled += bool(m.get("canceledByClient"))
            interruptions += bool(m.get("isInterruption"))
            if m["id"] != EMPTY_GUID:
                dup_ids += m["id"] in seen_ids
                seen_ids.add(m["id"])
        if msgs and d.get("endDate"):
            gap = abs((parse_utc(d["endDate"]) - parse_utc(msgs[-1]["createdAt"])).total_seconds())
            max_gap = max(max_gap, gap)
        time.sleep(1)

    print(f"\nConversaciones analizadas: {len(msg_counts)}")
    print(f"Mensajes por conversación: media {statistics.mean(msg_counts):.1f}, máximo {max(msg_counts)}")
    print(f"Tamaño de respuesta: media {statistics.mean(sizes)/1024:.0f} KB, máximo {max(sizes)/1024:.0f} KB")
    lat = sorted(latencies)
    print(f"Latencia del detalle: mediana {statistics.median(lat):.2f} s, "
          f"p95 {lat[int(len(lat)*0.95)-1]:.2f} s, máximo {lat[-1]:.2f} s")
    print(f"Valores de sender: {dict(senders)}")
    print(f"Valores de type: {dict(types)}")
    print(f"Mensajes con adjuntos: {with_attach}  cancelados: {canceled}  interrupciones: {interruptions}")
    print(f"Conversaciones con más de un GUID vacío: {multi_empty}")
    print(f"IDs de mensaje repetidos entre conversaciones (sin contar el GUID vacío): {dup_ids}")
    print(f"Diferencia máxima entre endDate y el último mensaje: {max_gap:.1f} s")


def cmd_feedback_set(conv, msg, value, comment=None):
    body = {"feedback": value == "up"}
    if comment:
        body["comment"] = comment
    send("POST", f"/api/v2/Agent/{AGENT}/conversation/{conv}/message/{msg}/feedback", body)


def cmd_feedback_delete(conv, msg):
    # Ruta deducida de la de envío: si devuelve 404 o 405, revisar la página "Delete feedback" de la documentación
    send("DELETE", f"/api/v2/Agent/{AGENT}/conversation/{conv}/message/{msg}/feedback")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--since", required=True); s.add_argument("--user")
    s = sub.add_parser("recent"); s.add_argument("--user")
    s = sub.add_parser("detail"); s.add_argument("--id", required=True)
    s = sub.add_parser("raw"); s.add_argument("--id", required=True)
    s = sub.add_parser("feedback"); s.add_argument("--since")
    s = sub.add_parser("scan"); s.add_argument("--n", type=int, default=50)
    s = sub.add_parser("feedback-set"); s.add_argument("--conv", required=True); s.add_argument("--msg", required=True)
    s.add_argument("--value", required=True, choices=["up", "down"]); s.add_argument("--comment")
    s = sub.add_parser("feedback-delete"); s.add_argument("--conv", required=True); s.add_argument("--msg", required=True)
    a = p.parse_args()
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {a.cmd}")
    {"list": lambda: cmd_list(a.since, a.user), "recent": lambda: cmd_recent(a.user),
     "detail": lambda: cmd_detail(a.id), "raw": lambda: cmd_raw(a.id),
     "feedback": lambda: cmd_feedback(a.since),
     "scan": lambda: cmd_scan(a.n),
     "feedback-set": lambda: cmd_feedback_set(a.conv, a.msg, a.value, a.comment),
     "feedback-delete": lambda: cmd_feedback_delete(a.conv, a.msg)}[a.cmd]()