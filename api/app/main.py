"""Aplicación FastAPI. Fase 0: contrato y servidor de prueba con datos ficticios.

Arranque local:  uvicorn app.main:app --reload   (desde la carpeta api/)
"""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

from . import __version__, errors
from .errors import PROBLEM_MEDIA_TYPE
from .routes import RESPONSE_DOCS, router

DESCRIPTION = """
API de solo lectura con el histórico de conversaciones de los alumnos con los agentes de Serenity Star.

**Fase 0:** contrato provisional. El servidor de prueba responde con datos ficticios.

## Autenticación
OAuth2 *client credentials*. Todas las rutas, salvo `/v1/health`, requieren un token con el scope
`conversations:read` en la cabecera `Authorization: Bearer <token>`.

## Identificación del alumno
El alumno se identifica por **email + vertical**. El email se envía siempre en el cuerpo de la petición,
nunca en la URL. Se compara sin distinguir mayúsculas y minúsculas.

## Convenciones
- Fechas en UTC, formato ISO 8601 con sufijo `Z` (p. ej. `2026-11-24T09:10:49Z`).
- Los mensajes del agente están en **Markdown**: el aula debe renderizarlo.
- Paginación con cursor opaco: se envía el `next_cursor` recibido en la petición siguiente, con los
  mismos criterios. `next_cursor: null` indica la última página.
- Frescura: cada respuesta incluye `synced_at` (última sincronización con Serenity) y `stale`
  (`true` si no se pudo refrescar antes de responder).
- Errores en formato `application/problem+json` (RFC 9457). Los errores de validación (`422`) incluyen
  la lista `errors` con la ubicación de cada campo erróneo.
- Se pueden añadir campos nuevos a las respuestas sin cambiar de versión: el cliente debe ignorar los
  campos que no conozca.
"""

TAGS = [
    {"name": "Conversaciones", "description": "Consulta de conversaciones de los alumnos."},
    {"name": "Catálogo", "description": "Valores de referencia."},
    {"name": "Sistema", "description": "Estado del servicio."},
]

app = FastAPI(
    title="API de conversaciones Serenity",
    version=__version__,
    description=DESCRIPTION,
    openapi_tags=TAGS,
    servers=[{"url": "https://api.example.com", "description": "Pendiente: dominio definitivo"}],
)
errors.install(app)
app.include_router(router)


def custom_openapi() -> dict:
    """Ajustes sobre el esquema generado:
    - las respuestas de error se declaran como application/problem+json;
    - se eliminan los esquemas de validación propios de FastAPI, que esta API no usa.
    """
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes,
                         tags=app.openapi_tags, servers=app.servers)
    # get_openapi elimina los valores null de todo el esquema, también de los ejemplos. En esta API los
    # campos con null siempre se envían, así que los ejemplos se restauran tal como se definieron.
    for (path, method), responses in RESPONSE_DOCS.items():
        operation = schema["paths"][path][method]
        for status, response in responses.items():
            for media_type, content in response.get("content", {}).items():
                target = operation["responses"][str(status)]["content"][media_type]
                for key in ("example", "examples"):
                    if key in content:
                        target[key] = deepcopy(content[key])
    for path in schema["paths"].values():
        for operation in path.values():
            for status, response in operation.get("responses", {}).items():
                content = response.get("content", {})
                if int(status) >= 400 and "application/json" in content:
                    content[PROBLEM_MEDIA_TYPE] = content.pop("application/json")
    components = schema.get("components", {}).get("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        components.pop(name, None)
    app.openapi_schema = schema
    return schema


app.openapi = custom_openapi
