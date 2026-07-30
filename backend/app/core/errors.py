"""Errores de dominio con código estable.

El `code` es parte del contrato de la API: el frontend, el Failure Lab y las
evaluaciones dependen de él. Los mensajes pueden cambiar de idioma o redacción;
los códigos no.

Regla de diseño: un error nunca revela la existencia ni el contenido de un
recurso de otro tenant. `ACCESS_DENIED_CROSS_TENANT` se devuelve tanto si el
recurso existe en otro tenant como si no existe en absoluto.
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base de todos los errores controlados de la aplicación."""

    status_code: int = 400
    code: str = "DOMAIN_ERROR"
    message: str = "Solicitud no válida"
    # Si es True, el manejador global registra un evento de seguridad
    # además del log ordinario.
    security_event: bool = False

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# ── Autenticación y autorización ─────────────────────────────────────────────


class AuthenticationError(DomainError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    message = "Credenciales no válidas o sesión expirada"


class PermissionDeniedError(DomainError):
    status_code = 403
    code = "PERMISSION_DENIED"
    message = "El rol actual no permite esta acción"
    security_event = True


class CrossTenantAccessError(DomainError):
    """Intento de alcanzar un recurso fuera del tenant de la sesión.

    Este es el error central del modelo multi-tenant. Se emite tanto desde la
    capa de aplicación como al traducir un resultado vacío causado por RLS.
    """

    status_code = 403
    code = "ACCESS_DENIED_CROSS_TENANT"
    message = "Acceso denegado: el recurso pertenece a otra organización"
    security_event = True


class InactiveUserError(DomainError):
    status_code = 403
    code = "USER_INACTIVE"
    message = "La cuenta está desactivada"


# ── Recursos ─────────────────────────────────────────────────────────────────


class NotFoundError(DomainError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Recurso no encontrado"


class ConflictError(DomainError):
    status_code = 409
    code = "CONFLICT"
    message = "El recurso ya existe o está en un estado incompatible"


class ValidationFailedError(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"
    message = "Los datos enviados no son válidos"


# ── Límites ──────────────────────────────────────────────────────────────────


class RateLimitedError(DomainError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Demasiadas peticiones. Inténtalo de nuevo en unos segundos"


# ── Agente y políticas ───────────────────────────────────────────────────────


class PolicyBlockedError(DomainError):
    """El policy engine ha bloqueado la solicitud o la respuesta."""

    status_code = 403
    code = "BLOCKED_BY_POLICY"
    message = "La solicitud infringe una política del sistema"
    security_event = True


class ToolNotAllowedError(DomainError):
    """El agente ha intentado usar una herramienta fuera de la allowlist."""

    status_code = 403
    code = "TOOL_NOT_ALLOWED"
    message = "La herramienta solicitada no está autorizada para este contexto"
    security_event = True


class InsufficientSourcesError(DomainError):
    """No hay documentación aprobada y vigente que respalde la respuesta.

    No es un fallo del sistema: es el comportamiento correcto. La API responde
    200 con `blocked_reason` en estos casos; esta excepción existe para los
    flujos internos que necesitan cortar la ejecución.
    """

    status_code = 200
    code = "INSUFFICIENT_SOURCES"
    message = "No hay documentación aprobada y vigente que respalde una respuesta"


class ProviderUnavailableError(DomainError):
    status_code = 503
    code = "LLM_PROVIDER_UNAVAILABLE"
    message = "El proveedor de IA no responde. La operación no se ha perdido"
