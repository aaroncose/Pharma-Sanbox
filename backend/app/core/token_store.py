"""Revocación de sesiones.

Los tokens de acceso son de vida corta y no se consultan contra ningún almacén:
validarlos requiere solo la firma, que es lo que permite que la API no toque
Redis ni la base de datos en cada petición.

Lo que sí se revoca es la cadena de refresco. Se guarda una lista de `jti`
revocados con expiración igual a la vida restante del token, de modo que Redis
no acumula entradas muertas.

Ventana de exposición: hasta 30 minutos, la vida del token de acceso. Es el
compromiso explícito de este diseño y está documentado en `docs/adr/0006`. La
alternativa —consultar el estado del usuario en cada petición— elimina la
ventana a cambio de una lectura por petición y de que una caída del almacén
tumbe la autenticación entera.

**Ante un fallo de Redis se deniega.** Al contrario que el limitador de tasa:
aquí no poder comprobar si una sesión fue revocada significa no poder afirmar
que sigue siendo válida. Ante la duda, no se renueva.
"""

from __future__ import annotations

from datetime import UTC, datetime

import redis
from redis.exceptions import RedisError

from app.config import settings
from app.core.errors import AuthenticationError
from app.core.logging import get_logger

log = get_logger("sessions")


class TokenStore:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.Redis.from_url(
            url or settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )

    @staticmethod
    def _key(jti: str) -> str:
        return f"revoked:{jti}"

    def revoke(self, jti: str, *, expires_at: datetime) -> None:
        ttl = int((expires_at - datetime.now(UTC)).total_seconds())
        if ttl <= 0:
            # Ya expiró por sí solo: revocarlo no aporta nada.
            return
        try:
            # `set(..., ex=)` en lugar de `setex`: redis-py 8 deprecó el
            # segundo. El filtro estricto de warnings de la suite lo convirtió
            # en excepción y la renovación de sesión dejó de funcionar.
            self._client.set(self._key(jti), "1", ex=ttl)
        except RedisError:
            log.critical("session_revoke_failed", jti=jti, exc_info=True)
            raise

    def assert_active(self, jti: str) -> None:
        try:
            revoked = self._client.exists(self._key(jti))
        except RedisError as exc:
            log.critical("session_check_unavailable_failing_closed", exc_info=True)
            raise AuthenticationError(
                "No se puede verificar el estado de la sesión"
            ) from exc

        if revoked:
            raise AuthenticationError("La sesión ha sido revocada")

    def revoke_all_for_user(self, user_id: str) -> None:
        """Invalida toda la cadena de refresco de un usuario.

        Se usa al desactivar una cuenta o al cambiar un rol: un token emitido
        con el rol anterior no debe poder renovarse con los permisos antiguos.
        Se marca el usuario con una fecha de corte en lugar de recorrer sus
        `jti`, que no conocemos.
        """
        try:
            self._client.set(
                f"user_revoked_after:{user_id}",
                datetime.now(UTC).isoformat(),
                ex=settings.refresh_token_ttl_days * 86400,
            )
        except RedisError:
            log.critical("user_revoke_failed", user_id=user_id, exc_info=True)
            raise

    def user_revoked_after(self, user_id: str) -> datetime | None:
        try:
            value = self._client.get(f"user_revoked_after:{user_id}")
        except RedisError as exc:
            raise AuthenticationError(
                "No se puede verificar el estado de la sesión"
            ) from exc
        return datetime.fromisoformat(value) if value else None

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except RedisError:
            return False


_store: TokenStore | None = None


def get_token_store() -> TokenStore:
    global _store
    if _store is None:
        _store = TokenStore()
    return _store
