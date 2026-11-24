"""Autenticación OAuth2 client credentials (Cognito en producción).

En la fase 0 el servidor de prueba no valida tokens contra Cognito:
- sin cabecera `Authorization: Bearer ...` -> 401
- token `token-sin-permiso` -> 403 (simula un token sin el scope `conversations:read`)
- cualquier otro token -> aceptado
"""
from fastapi import HTTPException, Security
from fastapi.openapi.models import OAuthFlowClientCredentials, OAuthFlows
from fastapi.security import OAuth2, SecurityScopes

SCOPE_READ = "conversations:read"
TOKEN_URL = "https://auth.example.com/oauth2/token"  # Pendiente: dominio de Cognito

oauth2_scheme = OAuth2(
    flows=OAuthFlows(clientCredentials=OAuthFlowClientCredentials(
        tokenUrl=TOKEN_URL, scopes={SCOPE_READ: "Lectura de conversaciones de alumnos"},
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
    if token == _FORBIDDEN_TOKEN:
        raise HTTPException(403, f"El token no tiene el scope {SCOPE_READ}.",
                            headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{SCOPE_READ}"'})


read_access = Security(require_read_scope, scopes=[SCOPE_READ])
