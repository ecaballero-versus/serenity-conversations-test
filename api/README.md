# API de conversaciones Serenity

Contrato de la API de solo lectura para IRICOM (fase 0) y servidor local (fase 1). El servidor funciona
con **datos ficticios** o con la **base local de la ingesta**, con datos reales.
El diseño completo está en [../docs/handoff.md](../docs/handoff.md).

## Contenido

| Ruta | Qué es |
|---|---|
| `app/models.py` | Modelos Pydantic: la fuente del contrato |
| `app/routes.py` | Endpoints v1 y ejemplos de respuesta |
| `app/mock_data.py` | Datos ficticios (alumnos `@example.com`) |
| `app/errors.py` | Errores `application/problem+json` (RFC 9457) |
| `app/security.py` | Esquema OAuth2 client credentials (simulado) |
| `openapi/openapi.json`, `openapi/openapi.yaml` | Especificación exportada para IRICOM |
| `tests/test_contract.py` | Tests del contrato |

## Endpoints

| Método y ruta | Auth | Descripción |
|---|---|---|
| `GET /v1/health` | No | Estado del servicio |
| `POST /v1/students/conversations/search` | Sí | Conversaciones de un alumno (email + `agent_code` en el cuerpo), paginadas con cursor |
| `GET /v1/conversations/{conversation_id}` | Sí | Conversación con mensajes y feedback |

## Uso (desde esta carpeta, con el venv del proyecto)

```powershell
..\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload     # http://127.0.0.1:8000/docs
..\.venv\Scripts\python.exe scripts\export_openapi.py            # regenera openapi/
..\.venv\Scripts\python.exe -m pytest
```

Tras cambiar modelos o rutas hay que regenerar `openapi/`. Un test falla si la especificación exportada
no coincide con la del código.

## Modos del servidor

Se elige con `API_BACKEND` en el `.env` de la raíz (plantilla en `../.env.example`). Los tests y los scripts
de exportación usan siempre `mock`, sea cual sea el `.env`.

### `API_BACKEND=db`: versión local con datos reales

Hacen falta dos terminales, desde la raíz del proyecto:

```powershell
# 1. Ingesta periódica: un ciclo cada 5 minutos hasta Ctrl+C (simula EventBridge)
.venv\Scripts\python.exe -m ingest loop

# 2. API
cd api
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload     # http://127.0.0.1:8000/docs
```

- **Datos**: lee la base de la ingesta (`data/serenity.db`). Solo hay datos de los orquestadores con
  `sync_enabled: true` en `config/agents.yaml`.
- **Token fijo** en lugar de Cognito: solo se acepta `API_TOKEN`.
  - En `/docs`, botón **Authorize**: `API_CLIENT_ID` y `API_CLIENT_SECRET` del `.env`.
  - Con curl o Postman: `Authorization: Bearer <API_TOKEN>`.
- **Lectura síncrona** (`API_SYNC_ON_DEMAND`, activada por defecto):
  - Antes de responder a una búsqueda, la API consulta a Serenity las novedades de ese alumno. Lo hace
    como mucho una vez por minuto y alumno.
  - El detalle de una conversación abierta se vuelve a descargar si la copia tiene más de 60 s.
  - Si Serenity falla, se responde con los datos guardados y `stale: true`.
  - Solo se aplica en los orquestadores sincronizados.
- **`synced_at`**: la sincronización más reciente que cubre los datos devueltos, ya sea el último ciclo
  correcto de la ingesta o la lectura síncrona.
- **`/v1/health`**: responde `degraded` si algún orquestador sincronizado lleva más de 15 minutos sin un
  ciclo correcto (por ejemplo, si `ingest loop` no está en marcha).
- **SQLite** en modo WAL, para que la API y la ingesta escriban a la vez. En AWS será PostgreSQL.

### `API_BACKEND=mock`: datos ficticios (por defecto)

- En `/docs`, botón **Authorize**: sirve cualquier `client_id` y `client_secret`. El servidor local
  simula el endpoint de tokens en `/mock/oauth2/token`. Después, *Try it out* en cada endpoint.
- Con curl o Postman: sirve cualquier `Authorization: Bearer <texto>`. `Bearer token-sin-permiso` devuelve
  403.
- Alumnos de ejemplo:
  - `alumna.ejemplo@example.com`: 3 conversaciones con `OrquestaAYIIPP` y 1 con `AgenteOrquestaGCIN`.
  - `alumno.demo@example.org`: 1 conversación con `OrquestaAYIIPP`.
- `synced_at` es fijo y `stale` siempre es `false`.

### Común a los dos modos

- `export_openapi.py` solo genera los archivos de `openapi/`.
- La especificación del servidor local apunta a sí mismo. La exportada en `openapi/` lleva el dominio de
  producción y el endpoint de tokens de Cognito (provisionales).
- No hay límite de peticiones: el 429 solo está documentado.

## Decisiones del contrato

- El alumno se identifica por email + `agent_code` (código del orquestador en Serenity). Los códigos
  válidos salen de `../config/agents.yaml`, la misma configuración que usa la ingesta. Las respuestas
  incluyen también `vertical`, solo informativo.
- Filtros opcionales de búsqueda: `last_activity_from` / `last_activity_to` (sobre `end_date`) y `open`.
- Orden: última actividad descendente. Cursor opaco.
- El mensaje de bienvenida se incluye con `is_welcome: true`.
- Se exponen `canceled_by_client`, `is_interruption`, `attachments` (forma genérica, pendiente de
  confirmar) y el comentario del feedback.
- El detalle de conversación incluye el email del alumno (`student_email`) para facilitar la depuración.
- Los errores de validación no devuelven el valor recibido, para no reflejar emails.

## Pendiente antes de compartir la versión definitiva

- Dominio de la API (`servers`) y del endpoint de tokens de Cognito (`tokenUrl`); ahora `example.com`.
- Forma real de `attachedVolatileKnowledges`.
