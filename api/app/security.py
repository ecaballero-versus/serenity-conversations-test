"""Autenticación OAuth2 client credentials (Cognito en producción).

El servidor local no usa Cognito:
- Sin cabecera `Authorization: Bearer ...` -> 401.
- Con `API_TOKEN` definido (token fijo): solo se acepta ese token; cualquier otro -> 401.
- Sin `API_TOKEN` (modo de prueba): `token-sin-permiso` -> 403 (simula un token sin el scope
  `conversations:read`) y cualquier otro token se acepta.

Para probar desde /docs, el servidor local ofrece un endpoint de tokens simulado (`/mock/oauth2/token`). Con
`API_TOKEN`, entrega ese token solo si recibe `API_CLIENT_ID` y `API_CLIENT_SECRET` (en el cuerpo o con
autenticación Basic). Sin `API_TOKEN`, acepta cualquier credencial. No forma parte del contrato.
"""
import base64
import binascii
import hmac
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, Security
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2, SecurityScopes

from .settings import settings

SCOPE_READ = "conversations:read"
COGNITO_TOKEN_URL = "https://auth.example.com/oauth2/token"  # Pendiente: dominio de Cognito
MOCK_TOKEN_PATH = "/mock/oauth2/token"

oauth2_scheme = OAuth2(
    flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(
        tokenUrl=MOCK_TOKEN_PATH, scopes={SCOPE_READ: "Lectura de conversaciones de alumnos"},
    )),
    scheme_name="oauth2",
    description="OAuth2 client credentials. Se solicita un token al endpoint de tokens con el `client_id` "
    "y `client_secret` entregados a IRICOM y se envía en la cabecera `Authorization: Bearer <token>`.",
    auto_error=False,
)

_FORBIDDEN_TOKEN = "token-sin-permiso"


async def require_read_scope(security_scopes: SecurityScopes,
                             authorization: str | None = Security(oauth2_scheme)) -> None:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Falta el token de acceso o no es válido.",
                            headers={"WWW-Authenticate": "Bearer"})
    if settings.api_token:
        if not hmac.compare_digest(token.encode(), settings.api_token.encode()):
            raise HTTPException(401, "Falta el token de acceso o no es válido.",
                                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        return
    if token == _FORBIDDEN_TOKEN:
        raise HTTPException(403, f"El token no tiene el scope {SCOPE_READ}.",
                            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{SCOPE_READ}"'})


read_access = Security(require_read_scope, scopes=[SCOPE_READ])


mock_token_router = APIRouter()


@mock_token_router.post(MOCK_TOKEN_PATH, include_in_schema=False)
async def mock_token(request: Request) -> dict:
    """Simula el endpoint de tokens de Cognito (grant_type=client_credentials)."""
    form = parse_qs((await request.body()).decode())
    if form.get("grant_type") != ["client_credentials"]:
        raise HTTPException(400, "grant_type debe ser client_credentials.")
    if not settings.api_token:
        return {"access_token": "token-de-prueba", "token_type": "Bearer", "expires_in": 3600}
    client_id, client_secret = _client_credentials(request, form)
    if not (settings.client_id and settings.client_secret
            and hmac.compare_digest(client_id.encode(), settings.client_id.encode())
            and hmac.compare_digest(client_secret.encode(), settings.client_secret.encode())):
        raise HTTPException(401, "Credenciales de cliente no válidas.")
    return {"access_token": settings.api_token, "token_type": "Bearer", "expires_in": 3600}


def _client_credentials(request: Request, form: dict) -> tuple[str, str]:
    """client_id y client_secret de la cabecera Basic o, si no hay, del cuerpo."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() == "basic":
        try:
            client_id, _, secret = base64.b64decode(value).decode().partition(":")
            return client_id, secret
        except (binascii.Error, UnicodeDecodeError):
            return "", ""
    return (form.get("client_id") or [""])[0], (form.get("client_secret") or [""])[0]
