"""Acceso a recursos con atribución de intentos.

Este módulo contiene la traducción más importante del sistema:

    RLS devuelve cero filas  →  403 ACCESS_DENIED_CROSS_TENANT

Y sostiene la propiedad que hace que esa traducción sea segura: la respuesta es
idéntica tanto si el recurso pertenece a otro cliente como si no existe. Si
fueran distintas —404 contra 403—, un atacante podría enumerar identificadores
válidos de otras organizaciones sin llegar a leer su contenido, que ya es una
fuga de información aunque no salga ni un solo campo.

Internamente sí se distingue, porque el equipo de seguridad necesita saber
contra quién iba el intento. Esa atribución se obtiene con
`audit_resource_owner()`, que solo devuelve el tenant propietario.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import CrossTenantAccessError
from app.core.logging import get_logger
from app.services import audit
from app.services.audit import AuditEvent

log = get_logger("access")

# Tablas para las que se puede pedir atribución. Coincide con la lista fijada
# dentro de `audit_resource_owner`; se repite aquí para fallar antes de llegar a
# la base de datos y con un mensaje comprensible.
ATTRIBUTABLE_TABLES = frozenset(
    {
        "documents", "interactions", "agent_outputs", "review_items",
        "healthcare_professionals", "products", "tasks", "simulations", "users",
    }
)


class Principal:
    """Identidad efectiva de la petición. Definido aquí para evitar un ciclo."""

    __slots__ = ("fingerprint", "jti", "role", "tenant_id", "trace_id", "user_id")

    def __init__(
        self,
        *,
        user_id: str,
        tenant_id: str,
        role: str,
        jti: str = "",
        trace_id: str = "",
        fingerprint: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.role = role
        self.jti = jti
        self.trace_id = trace_id
        self.fingerprint = fingerprint


def _attribute(session: Session, table: str, resource_id: str) -> str | None:
    """Devuelve el tenant propietario de un recurso, o None si no existe."""
    if table not in ATTRIBUTABLE_TABLES:
        return None
    try:
        owner = session.execute(
            text("SELECT audit_resource_owner(:table, CAST(:id AS uuid))"),
            {"table": table, "id": resource_id},
        ).scalar()
        return str(owner) if owner else None
    except Exception:
        # La atribución es información útil, no un requisito para denegar.
        # Si falla, se deniega igualmente y el log lo recoge.
        log.warning("attribution_failed", table=table, exc_info=True)
        return None


def deny_cross_tenant(
    session: Session,
    principal: Principal,
    *,
    table: str,
    resource_id: str,
    resource_type: str | None = None,
) -> None:
    """Registra el intento y lanza el error. No retorna nunca."""
    owner = _attribute(session, table, resource_id)

    audit.record(
        session,
        AuditEvent(
            action=audit.ACCESS_CROSS_TENANT_ATTEMPT,
            outcome="denied",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            decision_code="ACCESS_DENIED_CROSS_TENANT",
            resource_type=resource_type or table,
            resource_id=resource_id,
            resource_tenant_id=owner,
            policy_code="TENANT_ISOLATION",
            # El dato que convierte el 403 en una afirmación comprobable.
            exposed_field_count=0,
            client_fingerprint=principal.fingerprint,
            detail={
                # Distingue el intento real de una petición a un identificador
                # inventado. Hacia fuera la respuesta es la misma.
                "resource_exists": owner is not None,
                "attribution": "unavailable" if owner is None else "resolved",
            },
        ),
    )
    # El evento tiene que sobrevivir aunque la petición acabe en error.
    session.commit()

    raise CrossTenantAccessError()


def fetch_scoped_one(
    session: Session,
    principal: Principal,
    *,
    table: str,
    resource_id: str,
    columns: str = "*",
    extra_where: str = "",
    params: dict[str, Any] | None = None,
    resource_type: str | None = None,
) -> dict[str, Any]:
    """Recupera un recurso del tenant actual o deniega.

    La consulta **no** lleva filtro de tenant: lo aplica RLS. Es deliberado.
    Si el filtro estuviera aquí, el aislamiento dependería de que cada llamada
    se acuerde de escribirlo, y bastaría una omisión para abrir una fuga.
    Escribiéndolo así, la omisión es imposible.
    """
    if table not in ATTRIBUTABLE_TABLES:
        raise ValueError(f"tabla no permitida: {table}")

    where = f"id = CAST(:__rid AS uuid){' AND ' + extra_where if extra_where else ''}"
    query = text(f"SELECT {columns} FROM {table} WHERE {where}")  # noqa: S608

    row = session.execute(
        query, {"__rid": resource_id, **(params or {})}
    ).mappings().first()

    if row is None:
        deny_cross_tenant(
            session,
            principal,
            table=table,
            resource_id=resource_id,
            resource_type=resource_type,
        )

    return dict(row)  # type: ignore[arg-type]
