# API de conversaciones Serenity: fase 0

Contrato de la API de solo lectura para IRICOM y servidor de prueba con **datos ficticios**.
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
| `GET /v1/verticals` | Sí | Valores válidos de `vertical` |
| `POST /v1/students/conversations/search` | Sí | Conversaciones de un alumno (email + vertical en el cuerpo), paginadas con cursor |
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

## Servidor de prueba

- Token: cualquier `Authorization: Bearer <texto>` sirve. `Bearer token-sin-permiso` devuelve 403.
- Alumnos de ejemplo: `alumna.ejemplo@example.com` (3 conversaciones en `idiomas`, 1 en
  `marketing-digital`) y `alumno.demo@example.org` (1 en `idiomas`).
- `synced_at` es fijo y `stale` siempre es `false`. No hay límite de peticiones (429 solo documentado).

## Decisiones del contrato

- `vertical` es un string (slug) validado contra `GET /v1/verticals`. Las verticales son de ejemplo
  hasta acordarlas con IRICOM.
- Filtros opcionales de búsqueda: `last_activity_from` / `last_activity_to` (sobre `end_date`) y `open`.
- Orden: última actividad descendente. Cursor opaco.
- El mensaje de bienvenida se incluye con `is_welcome: true`.
- Se exponen `canceled_by_client`, `is_interruption`, `attachments` (forma genérica, pendiente de
  confirmar) y el comentario del feedback.
- El detalle de conversación no devuelve el email del alumno (IRICOM ya lo conoce).
- Los errores de validación no devuelven el valor recibido, para no reflejar emails.

## Pendiente antes de compartir la versión definitiva

- Dominio de la API (`servers`) y del endpoint de tokens de Cognito (`tokenUrl`); ahora `example.com`.
- Lista real de verticales.
- Forma real de `attachedVolatileKnowledges`.
