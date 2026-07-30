"""Contraseñas, tokens y revocación de sesión.

Decisiones y por qué
────────────────────
**Argon2id para contraseñas.** Es el ganador de la Password Hashing Competition
y el recomendado por OWASP frente a bcrypt, que además tiene el problema de
truncar silenciosamente a 72 bytes. Los parámetros se declaran explícitamente en
vez de aceptar los del paquete: si mañana la librería cambia sus valores por
defecto, los hashes existentes seguirían verificándose pero los nuevos tendrían
un coste distinto sin que nadie lo hubiera decidido.

**Verificación en tiempo constante también cuando el usuario no existe.** Si
`login` respondiera de inmediato ante un correo desconocido y tardase 50 ms ante
uno real, el tiempo de respuesta sería un oráculo de enumeración de cuentas. Por
eso se verifica siempre contra un hash señuelo.

**Tokens de acceso cortos + refresco revocable.** El token de acceso vive 30
minutos y no se puede revocar: es el precio de no consultar la base de datos en
cada petición. Lo que sí se revoca es el de refresco, mediante una lista en
Redis. Cerrar sesión, desactivar un usuario o cambiar su rol invalida la cadena
en la siguiente renovación, no dentro de siete días.

**`jti` en todos los tokens.** Sin un identificador único no hay forma de
revocar uno concreto ni de correlacionarlo con la auditoría.
"""

from __future__ import annotations

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from argon2.low_level import Type

from app.config import settings
from app.core.errors import AuthenticationError

# Parámetros explícitos, alineados con la recomendación de OWASP para Argon2id.
# 64 MiB de memoria y 3 iteraciones: coste asumible en servidor y caro de
# paralelizar en GPU, que es de lo que se trata.
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=64 * 1024,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Hash señuelo sobre un valor aleatorio de arranque. Se verifica contra él
# cuando el usuario no existe, para que el coste temporal de la respuesta sea
# indistinguible del caso real.
_DECOY_HASH = _hasher.hash(secrets.token_urlsafe(32))

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Verifica la contraseña. Siempre consume tiempo, exista o no el usuario."""
    target = password_hash or _DECOY_HASH
    try:
        _hasher.verify(target, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    # Si el hash real se verificó pero venía de un usuario inexistente, el
    # resultado sigue siendo negativo.
    return password_hash is not None


def needs_rehash(password_hash: str) -> bool:
    """True si el hash usa parámetros antiguos y conviene recalcularlo al entrar."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Tokens
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: str
    tenant_id: str
    role: str
    token_type: TokenType
    jti: str
    expires_at: datetime
    # Necesario para la revocación masiva: al desactivar un usuario o cambiar
    # su rol se guarda una fecha de corte, y todo token emitido antes deja de
    # poder renovarse. Sin `issued_at` no hay forma de compararlo.
    issued_at: datetime


def _create_token(
    *, user_id: str, tenant_id: str, role: str, token_type: TokenType, ttl: timedelta
) -> tuple[str, TokenClaims]:
    now = datetime.now(UTC)
    expires_at = now + ttl
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": user_id,
        "tenant": tenant_id,
        "role": role,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "pharma-sandbox",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    claims = TokenClaims(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        token_type=token_type,
        jti=jti,
        expires_at=expires_at,
        issued_at=now,
    )
    return token, claims


def create_access_token(
    *, user_id: str, tenant_id: str, role: str
) -> tuple[str, TokenClaims]:
    return _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        token_type="access",  # noqa: S106 — es un discriminador, no un secreto
        ttl=timedelta(minutes=settings.access_token_ttl_minutes),
    )


def create_refresh_token(
    *, user_id: str, tenant_id: str, role: str
) -> tuple[str, TokenClaims]:
    return _create_token(
        user_id=user_id,
        tenant_id=tenant_id,
        role=role,
        token_type="refresh",  # noqa: S106 — es un discriminador, no un secreto
        ttl=timedelta(days=settings.refresh_token_ttl_days),
    )


def decode_token(token: str, *, expected_type: TokenType) -> TokenClaims:
    """Decodifica y valida un token.

    Fija `algorithms` explícitamente. Aceptar el algoritmo que declara la
    cabecera del propio token es la vulnerabilidad clásica de JWT: permite
    presentar un token firmado con `none`, o con HMAC usando la clave pública
    como secreto.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer="pharma-sandbox",
            options={"require": ["exp", "iat", "sub", "jti", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("La sesión ha expirado") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Token no válido") from exc

    token_type = payload.get("type")
    # Sin esta comprobación, un token de refresco —de vida larga— serviría como
    # token de acceso, anulando el sentido de que el de acceso sea corto.
    if not hmac.compare_digest(str(token_type), expected_type):
        raise AuthenticationError("Tipo de token incorrecto")

    return TokenClaims(
        user_id=payload["sub"],
        tenant_id=payload["tenant"],
        role=payload["role"],
        token_type=expected_type,
        jti=payload["jti"],
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
    )
