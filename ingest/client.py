"""Cliente de solo lectura de la API de Serenity.

- Limita el ritmo de peticiones (peticiones por segundo configurable).
- Respeta 429 y `Retry-After`; reintenta errores 5xx y de red con espera exponencial.
- Nunca registra cuerpos de respuesta ni parámetros con datos de alumnos.
"""
import logging
import time

import requests

log = logging.getLogger(__name__)


class NotFound(Exception):
    pass


class SerenityError(Exception):
    pass


class SerenityClient:
    def __init__(self, base_url: str, api_key: str, rps: float = 3.0, timeout: float = 30, max_retries: int = 5,
                 wait_on_429: bool = True):
        """wait_on_429=False: ante un 429 se lanza SerenityError en lugar de esperar (lecturas bajo demanda,
        donde hay alguien esperando la respuesta)."""
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["X-API-KEY"] = api_key
        self.min_interval = 1.0 / rps
        self.timeout = timeout
        self.max_retries = max_retries
        self.wait_on_429 = wait_on_429
        self._last_request = 0.0
        self.requests_made = 0
        self.rate_limit_remaining: int | None = None

    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                r = self.session.get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt == self.max_retries:
                    raise SerenityError(f"Error de red en {path}: {type(exc).__name__}") from None
                time.sleep(2 ** attempt)
                continue
            self.requests_made += 1
            remaining = r.headers.get("X-Rate-Limit-Remaining")
            if remaining and remaining.isdigit():
                self.rate_limit_remaining = int(remaining)
            if r.status_code == 429 and not self.wait_on_429:
                raise SerenityError(f"HTTP 429 en {path}")
            if r.status_code == 429:
                retry_after = r.headers.get("Retry-After", "")
                delay = int(retry_after) if retry_after.isdigit() else 30
                log.warning("429 de Serenity: esperando %s s (intento %s)", delay, attempt + 1)
                time.sleep(delay)
                continue
            if r.status_code >= 500:
                if attempt == self.max_retries:
                    break
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                raise NotFound(path)
            if not r.ok:
                raise SerenityError(f"HTTP {r.status_code} en {path}")
            return r
        raise SerenityError(f"HTTP {r.status_code} en {path} tras {self.max_retries} reintentos")

    def list_conversations(self, agent_code: str, page: int, page_size: int = 200,
                           user_identifier: str | None = None) -> dict:
        """Listado ordenado por fecha de fin (última actividad) descendente. `user_identifier` filtra por
        alumno (coincidencia exacta, sin distinguir mayúsculas)."""
        params = {"page": page, "pageSize": page_size, "sortDirection": "desc"}
        if user_identifier:
            params["userIdentifier"] = user_identifier
        return self._get(f"/api/v2/AgentInstance/{agent_code}", params).json()

    def get_conversation(self, agent_code: str, conversation_id: str) -> tuple[dict, bytes]:
        """Devuelve el detalle interpretado y los bytes originales de la respuesta."""
        r = self._get(f"/api/v2/agent/{agent_code}/conversation/{conversation_id}")
        return r.json(), r.content

    def list_feedback(self, agent_code: str, start_date: str, page: int, page_size: int = 200) -> dict:
        """Feedback con dateUtc >= start_date."""
        return self._get(f"/api/v2/Agent/{agent_code}/feedback",
                         {"startDate": start_date, "page": page, "pageSize": page_size,
                          "sortDirection": "desc"}).json()
