"""Dependencias de FastAPI.

El orden importa y no es casual:

    1. traza          identificador para correlacionar todo lo que sigue
    2. autenticación  ¿quién es? — firma del token, sin tocar la base de datos
    3. sesión         abre transacción y **fija el tenant** antes de nada
    4. autorización   ¿puede hacer esto? — matriz de permisos
    5. límite de tasa por usuario, no por dirección IP

El punto 3 es el que sostiene el modelo: ninguna consulta de ningún endpoint se
ejecuta sin que la transacción tenga ya `app.tenant_id` fijado. No hay ruta por
la que un endpoint pueda obtener una sesión "sin contexto" y consultar sin
querer todo el conjunto de datos.

El límite se aplica por identidad autenticada en lugar de por IP porque los
clientes de este producto salen a internet tras la NAT corporativa de una
farmacéutica: limitar por IP castigaría a toda la organización por el uso de una
sola persona.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.permissions import has_permission
from app.core.ratelimit import get_rate_limiter
from app.core.security import decode_token
from app.db.session import SessionFactory, TenantContext, apply_tenant_context
from app.services import audit
from app.services.access import Principal
from app.services.audit import AuditEvent, fingerprint_client

# `auto_error=False` para poder devolver el error de dominio propio en lugar del
# 403 genérico de Starlette, que además usa un código distinto del contrato.
bearer_scheme = HTTPBearer(auto_error=False)


def get_trace_id(request: Request) -> str:
    """Identificador que correlaciona auditoría, trazas del agente y respuesta.

    Se lee del estado de la petición, donde lo deja el middleware. Leerlo de la
    cabecera entrante —como se hacía— significaba depender de que el cliente la
    enviara: ninguno lo hace, así que todas las peticiones compartían el valor
    `tr_unknown`. El síntoma visible fue una violación de unicidad en
    `agent_traces`; el daño silencioso era que ninguna auditoría se podía
    correlacionar con nada.

    Se conserva la cabecera como origen alternativo para que un cliente que sí
    la envíe pueda enlazar su propia traza con la del servidor.
    """
    from_state = getattr(request.state, "request_id", None)
    return from_state or request.headers.get("X-Request-Id") or "tr_unknown"


def get_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> Principal:
    """Identidad de la petición a partir del token de acceso.

    No consulta la base de datos. La contrapartida es una ventana de hasta 30
    minutos —la vida del token— entre desactivar un usuario y que deje de poder
    operar; a cambio, la autenticación no depende de que la base de datos ni
    Redis estén disponibles. Documentado en `docs/adr/0006`.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Falta la cabecera de autorización")

    claims = decode_token(credentials.credentials, expected_type="access")

    return Principal(
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        role=claims.role,
        jti=claims.jti,
        trace_id=get_trace_id(request),
        fingerprint=fingerprint_client(
            request.client.host if request.client else None,
            request.headers.get("user-agent"),
        ),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_principal)]


def get_session(principal: CurrentPrincipal) -> Iterator[Session]:
    """Sesión con el tenant del token ya aplicado.

    Es la única forma de obtener una sesión dentro de un endpoint. Como el
    contexto se fija aquí y no en cada consulta, no existe el camino por el que
    alguien olvide aplicarlo.
    """
    session = SessionFactory()
    try:
        session.begin()
        apply_tenant_context(
            session,
            TenantContext(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                role=principal.role,
            ),
        )
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


TenantSession = Annotated[Session, Depends(get_session)]


def get_unscoped_session() -> Iterator[Session]:
    """Sesión sin tenant, para el inicio de sesión.

    Es la única operación que legítimamente ocurre antes de saber a qué
    organización pertenece quien llama. El endpoint de login es el único
    autorizado a usarla, y consulta exclusivamente `users` por correo.
    """
    session = SessionFactory()
    try:
        session.begin()
        apply_tenant_context(session, TenantContext(None, None, "anonymous"))
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


UnscopedSession = Annotated[Session, Depends(get_unscoped_session)]


def require(*permissions: str):
    """Exige uno o más permisos. Deniega por defecto.

    Un endpoint sin `require(...)` en sus dependencias no queda abierto: queda
    sin declarar, y la prueba `test_every_endpoint_declares_permission` lo
    detecta.
    """

    def dependency(principal: CurrentPrincipal, session: TenantSession) -> Principal:
        missing = [p for p in permissions if not has_permission(principal.role, p)]
        if missing:
            audit.record(
                session,
                AuditEvent(
                    action=audit.ACCESS_PERMISSION_DENIED,
                    outcome="denied",
                    trace_id=principal.trace_id,
                    tenant_id=principal.tenant_id,
                    actor_user_id=principal.user_id,
                    actor_role=principal.role,
                    decision_code="PERMISSION_DENIED",
                    exposed_field_count=0,
                    client_fingerprint=principal.fingerprint,
                    detail={"required": list(permissions), "missing": missing},
                ),
            )
            session.commit()
            raise PermissionDeniedError(
                details={"required": list(permissions)},
            )
        return principal

    return dependency


def rate_limit(bucket: str = "api", limit: int | None = None):
    """Limita por identidad autenticada.

    El presupuesto del agente es un cubo aparte y mucho más estrecho: una
    petición de chat cuesta dinero y segundos, una de lectura no.
    """
    effective = limit if limit is not None else settings.rate_limit_per_minute

    def dependency(principal: CurrentPrincipal) -> None:
        get_rate_limiter().check(
            principal.user_id, bucket=bucket, limit=effective
        )

    return dependency


def agent_rate_limit():
    return rate_limit("agent", settings.rate_limit_agent_per_minute)
