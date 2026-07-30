"""Limitación de tasa.

Algoritmo: ventana deslizante con marcas de tiempo en un sorted set de Redis.

Por qué no una ventana fija con `INCR` + `EXPIRE`, que es lo más habitual:
permite el doble del límite en el peor caso. Con 60 por minuto, un cliente puede
hacer 60 peticiones en el segundo 59 y otras 60 en el 61, es decir 120 en dos
segundos. Para un endpoint de lectura da igual; para el que invoca al modelo
—donde cada petición cuesta dinero y latencia— no.

La comprobación y el registro se hacen en un script Lua para que sean atómicos.
Sin eso, dos peticiones simultáneas podrían leer el mismo recuento y pasar
ambas, que es justo lo que ocurre bajo carga, que es cuando importa.

**Ante un fallo de Redis se permite la petición.** Es una decisión consciente:
el limitador protege de abuso y de coste, no es un control de seguridad. Si
fallara cerrado, una caída de Redis dejaría la aplicación inutilizable. El
incidente se registra con nivel crítico, porque operar sin límite es un estado
degradado que alguien debe ver.
"""

from __future__ import annotations

import time

import redis
from redis.exceptions import RedisError

from app.config import settings
from app.core.errors import RateLimitedError
from app.core.logging import get_logger

log = get_logger("ratelimit")

# KEYS[1] clave del cliente
# ARGV[1] marca de tiempo actual en milisegundos
# ARGV[2] tamaño de la ventana en milisegundos
# ARGV[3] número máximo de peticiones en la ventana
# ARGV[4] identificador único de esta petición
_SLIDING_WINDOW = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

-- Descarta lo que ya salió de la ventana.
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)

local used = redis.call('ZCARD', key)
if used >= limit then
    -- Devuelve cuánto falta para que se libere el hueco más antiguo.
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_ms = window - (now - tonumber(oldest[2]))
    return {0, used, retry_ms}
end

redis.call('ZADD', key, now, member)
-- La expiración evita acumular claves de clientes que dejaron de aparecer.
redis.call('PEXPIRE', key, window)
return {1, used + 1, 0}
"""


class RateLimiter:
    def __init__(self, url: str | None = None) -> None:
        self._client = redis.Redis.from_url(
            url or settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        self._script = self._client.register_script(_SLIDING_WINDOW)
        self._degraded = False

    def check(
        self, identity: str, *, bucket: str, limit: int, window_seconds: int = 60
    ) -> None:
        """Consume una unidad de cuota. Lanza `RateLimitedError` si se agotó."""
        now_ms = int(time.time() * 1000)
        key = f"rl:{bucket}:{identity}"
        member = f"{now_ms}-{time.monotonic_ns()}"

        try:
            allowed, _used, retry_ms = self._script(
                keys=[key], args=[now_ms, window_seconds * 1000, limit, member]
            )
            if self._degraded:
                log.info("ratelimit_recovered")
                self._degraded = False
        except RedisError:
            if not self._degraded:
                # Solo la primera vez: con Redis caído, esto se dispararía en
                # cada petición y el log crítico dejaría de ser visible.
                log.critical("ratelimit_unavailable_failing_open", bucket=bucket)
                self._degraded = True
            return

        if not allowed:
            raise RateLimitedError(
                details={
                    "bucket": bucket,
                    "limit": limit,
                    "window_seconds": window_seconds,
                    "retry_after_seconds": max(1, round(int(retry_ms) / 1000)),
                }
            )

    def reset(self, identity: str, *, bucket: str) -> None:
        """Limpia la cuota. Solo para pruebas y para el Failure Lab."""
        try:
            self._client.delete(f"rl:{bucket}:{identity}")
        except RedisError:
            pass

    def ping(self) -> bool:
        try:
            return bool(self._client.ping())
        except RedisError:
            return False


_limiter: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter()
    return _limiter
