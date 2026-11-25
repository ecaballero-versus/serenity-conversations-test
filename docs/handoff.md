# Handoff: histórico de conversaciones de Serenity en AWS

Contexto acumulado en la fase de diseño. El documento de arquitectura completo está en Google Docs
("Arquitectura AWS para ingesta y consulta de conversaciones Serenity"); este archivo resume lo
necesario para implementar.

## Objetivo

Copiar las conversaciones de los alumnos con los agentes de Serenity Star a infraestructura propia
en AWS y exponerlas mediante una API autenticada de solo lectura para IRICOM (proveedor del aula
virtual). IRICOM no almacena los datos. El mismo almacén servirá para la futura migración a la
plataforma Delfín. Motivo: Serenity solo conserva los datos un tiempo limitado (retención por agente).

## Arquitectura objetivo (AWS, región eu-south-2)

- **Lambda de ingesta**: lanzada por EventBridge cada 5 minutos. Concurrencia reservada = 1.
- **Lambda de sincronización**: bajo demanda, invocada de forma síncrona por la Lambda de la API.
  Misma base de código que la ingesta, función separada (timeout y concurrencia propios; la API no
  tiene la API key de Serenity ni permisos de escritura).
- **Lambda de API**: FastAPI + Mangum, detrás de API Gateway + WAF; autenticación OAuth2
  client credentials con Cognito (scope `conversations:read`).
- **Almacenamiento**: S3 (JSON original de cada conversación) + PostgreSQL (modelo consultable).
- **Red y seguridad**: VPC con subredes privadas, NAT Gateway (salida a Serenity), VPC endpoints,
  Secrets Manager, KMS opcional, IAM con mínimo privilegio, CloudWatch, CloudTrail, GuardDuty.
- Volumen pequeño: todo cabe en Lambda. Fargate, Step Functions o SQS solo si el volumen lo exige.

## Plan por fases

- **Fase 0 (en curso)**: contrato de la API (OpenAPI generado por FastAPI, respuestas de ejemplo)
  para que IRICOM empiece su integración.
- **Fase 1**: prototipo local en Python: ingesta, base de datos, carpeta local en lugar de S3,
  API con token fijo en lugar de Cognito, lectura síncrona, reconciliación de feedback.
- **Fase 2**: beta en AWS con DevOps, con infraestructura como código (Terraform o CDK, por decidir).

## API de Serenity (verificado con pruebas)

- Base URL: `https://api.serenitystar.ai`. Autenticación: cabecera `X-API-KEY`.
- API key de un usuario virtual (System User), alcance Agent, permiso Auditor, asignada a cada
  orquestador. Las asignaciones solo se aplican tras guardar la configuración del agente.
- Se sincroniza solo el **orquestador de cada vertical**: contiene lo que ve el alumno, incluidas las
  respuestas elaboradas por subagentes. Hay al menos cuatro verticales y habrá más.

### Endpoints

| Uso | Endpoint |
|---|---|
| Listado de conversaciones | `GET /api/v2/AgentInstance/{agentCode}` |
| Detalle de conversación | `GET /api/v2/agent/{agentCode}/conversation/{conversationId}` |
| Listado de feedback | `GET /api/v2/Agent/{agentCode}/feedback` |
| Enviar / borrar feedback (solo pruebas) | `POST` / `DELETE /api/v2/Agent/{agentCode}/conversation/{conversationId}/message/{messageId}/feedback` |

Parámetros del listado: `page`, `pageSize` (200 aceptado), `sortDirection` (`asc`/`desc`),
`startDate`, `endDate`, `userIdentifier`.

### Comportamiento comprobado

- `startDate` y `endDate` filtran por **fecha de inicio** de la conversación.
- `sortDirection` ordena por **fecha de fin**, que se actualiza con cada mensaje y coincide exactamente
  con el `createdAt` del último mensaje. Por eso el incremental ordena por fecha de fin descendente y
  pagina hasta encontrar una conversación anterior a la marca de agua (con margen de solape).
- Fechas en UTC. Las de conversaciones y feedback llegan **sin zona horaria** y con 7 decimales; las de
  los mensajes llegan con `+00:00`. Tratar siempre como UTC de forma explícita.
- Filtro `userIdentifier`: coincidencia exacta e insensible a mayúsculas.
- `userIdentifier` contiene el **email del alumno**; algunos emails tienen mayúsculas (normalizar a
  minúsculas). Las conversaciones de desarrolladores tienen `apikey-user-…@serenitystar.ai`; se guardan
  igual y nunca coinciden con un alumno.
- Las conversaciones se cierran tras 24 h sin actividad. Pueden cerrarse antes por un motivo no
  confirmado. No afecta al diseño.
- Retención configurable por agente: 30 días por defecto, algunos orquestadores tienen 120.
- Límites: cabeceras `X-Rate-Limit-Limit: 12h`, `X-Rate-Limit-Remaining` (~10 millones por ventana,
  compartido por todo el tenant), `X-Rate-Limit-Reset`. Respetar 429 y `Retry-After`. Publicar el
  remanente como métrica.
- Latencia del detalle: mediana 0,18 s, máximo 0,42 s. Conversaciones pequeñas (máx. 13 mensajes y
  20 KB en una muestra de 50).
- No hay webhooks salientes.

### Detalle de conversación (campos relevantes)

`id`, `name` (título automático, truncado a ~50 caracteres), `startDate`, `endDate`, `open`,
`userIdentifier`, `messages[]`. Ignorar `messagesJson` (copia en texto), `conversationStarters`,
`usage`, `costs`, `useVision` (quedan en el JSON de S3).

Cada mensaje: `id`, `sender` (`bot`/`user`), `type` (`text`), `value` (Markdown), `createdAt`,
`canceledByClient`, `isInterruption`, `attachedVolatileKnowledges`. El mensaje de bienvenida tiene
siempre `id = 00000000-0000-0000-0000-000000000000`. **Corregido (24/11/2026):** no es el único; algún
mensaje del bot sin id propio (posible aviso o error) también lo lleva, así que el id del mensaje no es
único dentro de la conversación: la clave es (conversación, posición).

### Feedback

Campos: `id`, `conversationId`, `agentMessageId`, `feedback` (booleano), `comment` (opcional, hasta
1000 caracteres), `dateUtc`, `userIdentifier`, `channel`, `userMessage`, `agentMessage`.
Comprobado: cambiar una valoración modifica el mismo registro **sin actualizar `dateUtc`**; retirarla
**elimina** el registro. Sincronización: en cada ciclo releer el feedback de los últimos 7 días y
reconciliar (insertar, actualizar, borrar lo que falte); fuera de esa ventana no borrar nada.

## Modelo de datos (PostgreSQL)

Tres esquemas: `serenity` (datos), `sync` (estado), `api` (vistas para IRICOM).

- **serenity.agents**: `agent_code` (PK), `vertical`, `sync_enabled`, `retention_days`.
- **serenity.conversations**: `id` (PK), `agent_code`, `user_identifier` (email en minúsculas),
  `title`, `start_date`, `end_date`, `is_open`, `message_count`, `content_hash`, `raw_s3_key`,
  `last_synced_at`.
- **serenity.messages**: PK compuesta (`conversation_id`, `id`), `seq`, `sender`, `type`, `value`,
  `created_at`, `canceled_by_client`, `is_interruption`, `attachments` (JSONB).
- **serenity.message_feedback**: `id` (PK), `conversation_id`, `message_id`, `is_positive`, `comment`,
  `created_at` (`dateUtc`), `last_synced_at`.
- **sync.agent_state**: marca de agua (fecha de fin más reciente sincronizada) y checkpoint del
  volcado inicial.
- **sync.student_state**: última sincronización bajo demanda por alumno (umbral de 60 s).
- **sync.runs**, **sync.pending**: historial de ejecuciones y conversaciones que fallaron.
- **api**: vistas de conversaciones y mensajes (con feedback) y tabla de auditoría de accesos.
- Roles: ingesta/sincronización escriben en `serenity` y `sync`; la API solo lee las vistas de `api`
  y `sync.student_state`, y escribe en la auditoría.

## Contrato de la API para IRICOM (fase 0)

- El alumno se identifica por **email + vertical** (Serenity no expone ningún ID interno de alumno).
- El email **no va en la URL** (quedaría en logs de API Gateway, WAF y CloudWatch): va en el cuerpo.
- Endpoints previstos:
  - `POST /v1/students/conversations/search` con `{ "email": "...", "vertical": "..." }`:
    conversaciones del alumno sin mensajes (id, vertical, título, fechas, abierta, nº de mensajes),
    paginadas con `cursor` y `limit`. Aplica la lectura síncrona antes de responder.
  - `GET /v1/conversations/{conversationId}`: conversación con mensajes y feedback. Refresca desde
    Serenity si está abierta y la copia no es reciente.
  - `GET /v1/health`.
- Cada respuesta incluye `synced_at` y `stale: true` si no se pudo refrescar desde Serenity.
- Errores: 401, 403, 404, 422 (validación), 429.
- Los mensajes del agente van en Markdown; el aula debe renderizarlo.

## Volcado inicial

~20.000 conversaciones entre los orquestadores consultados. Páginas de 200 (~100 llamadas de
listado). Detalle a 3–5 peticiones por segundo, reduciendo ante 429. Invocaciones de Lambda de
~3.000–4.000 conversaciones con checkpoint. Urgente por la retención de Serenity.

## Entorno local

- Windows **sin permisos de administrador**. Python con `venv` funciona; las variables van en un
  `.env` (cargado con `python-dotenv`).
- Docker y el instalador de PostgreSQL requieren administrador. Plan para PostgreSQL: probar
  `pgserver` (pip); si falla, binarios portables en ZIP; SQLite solo como último recurso
  (usar SQLAlchemy para poder cambiar).
- S3 se simula con una carpeta local; Cognito con un token fijo.
- Script de pruebas existente: `test_serenity_startdate.py` (comandos `list`, `recent`, `detail`,
  `raw`, `feedback`, `feedback-set`, `feedback-delete`, `scan`).

## Puntos abiertos

- Lista de verticales y códigos de sus orquestadores; valores de `vertical` acordados con IRICOM.
- ~~Comprobar `groupIdentifier`~~: el listado lo trae, pero vacío en las 200 conversaciones más recientes de
  `OrquestaAYIIPP` (24/11/2026). El listado sí trae `channel` (`FV IP` en el aula; `WebAppPreview` y
  `Unspecified` en pruebas).
- ~~Identificador del alumno~~: **decidido email + `agent_code`** (Jurispol tiene dos orquestadores,
  entrada básica y ejecutiva). Pendiente de confirmar con IRICOM (entregable v1).
- Plazo de conservación de las conversaciones (RGPD).
- Ampliar la retención de los orquestadores en Serenity mientras no exista el volcado.
- Herramienta de infraestructura como código para la fase 2 (Terraform o CDK).
