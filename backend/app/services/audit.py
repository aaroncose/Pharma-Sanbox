"""Servicio de auditoría.

Todo evento relevante acaba aquí. Tres reglas de diseño:

**Se registra el intento, no solo el resultado.** Un acceso denegado es más
informativo que uno concedido: dice que alguien buscó donde no debía. Por eso
`audit_log` acepta escrituras que apuntan a un tenant que el actor no puede
leer, y guarda `resource_tenant_id` para saber contra quién iba el intento.

**`exposed_field_count` es un dato, no una suposición.** Cuando se deniega un
acceso, el log afirma explícitamente que salieron cero campos. El Failure Lab lo
comprueba en lugar de confiar en que el 403 implique que no se filtró nada.

**Nada de datos personales innecesarios.** No se guarda la dirección IP sino un
identificador derivado con HMAC, que permite correlacionar sesiones sin
almacenar el dato. Es minimización aplicada al propio registro de auditoría, que
es donde más fácil resulta olvidarla.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging import get_logger

log = get_logger("audit")

Outcome = Literal["success", "denied", "blocked", "error"]


# ── Acciones ─────────────────────────────────────────────────────────────────
# Vocabulario cerrado, en formato `dominio.recurso.verbo`. Se usan como
# constantes para que un cambio de nombre no deje eventos huérfanos que las
# búsquedas de la pantalla de auditoría ya no encuentren.

AUTH_LOGIN_SUCCESS = "auth.login.success"
AUTH_LOGIN_FAILED = "auth.login.failed"
AUTH_LOGIN_INACTIVE = "auth.login.inactive_user"
AUTH_TOKEN_REFRESHED = "auth.token.refreshed"  # noqa: S105 — nombre de acción
AUTH_LOGOUT = "auth.logout"

ACCESS_CROSS_TENANT_ATTEMPT = "access.cross_tenant.attempt"
ACCESS_PERMISSION_DENIED = "access.permission.denied"
ACCESS_RATE_LIMITED = "access.rate_limited"

DOCUMENT_SEARCH = "document.search"
DOCUMENT_APPROVED = "document.approved"
DOCUMENT_WITHDRAWN = "document.withdrawn"

AGENT_BRIEFING_GENERATED = "agent.briefing.generated"
AGENT_CHAT_ANSWERED = "agent.chat.answered"
AGENT_BLOCKED_BY_POLICY = "agent.blocked_by_policy"
AGENT_TOOL_DENIED = "agent.tool.denied"
AGENT_PROVIDER_FAILED = "agent.provider.failed"
AGENT_PROMPT_INJECTION_DETECTED = "agent.prompt_injection.detected"

COMPLIANCE_REVIEW_REQUESTED = "compliance.review.requested"
COMPLIANCE_REVIEW_APPROVED = "compliance.review.approved"
COMPLIANCE_REVIEW_REJECTED = "compliance.review.rejected"
COMPLIANCE_REVIEW_EDITED = "compliance.review.edited"

FAILURE_LAB_EXECUTED = "failure_lab.scenario.executed"


@dataclass(slots=True)
class AuditEvent:
    action: str
    outcome: Outcome
    trace_id: str
    tenant_id: str | None = None
    actor_user_id: str | None = None
    actor_role: str | None = None
    decision_code: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    resource_tenant_id: str | None = None
    policy_code: str | None = None
    model: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    tools_called: list[dict[str, Any]] = field(default_factory=list)
    documents_used: list[dict[str, Any]] = field(default_factory=list)
    review_item_id: str | None = None
    latency_ms: int | None = None
    cost_eur: float | None = None
    exposed_field_count: int = 0
    client_fingerprint: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def fingerprint_client(ip: str | None, user_agent: str | None) -> str | None:
    """Identificador estable y no reversible de un cliente.

    Permite responder a "¿este intento viene del mismo sitio que aquel otro?"
    sin almacenar la dirección. Se usa HMAC con el secreto de la aplicación, no
    un hash a secas: sin clave, el espacio de direcciones IPv4 es lo bastante
    pequeño como para invertirlo por fuerza bruta en minutos.
    """
    if not ip and not user_agent:
        return None
    material = f"{ip or ''}|{user_agent or ''}".encode()
    digest = hmac.new(settings.jwt_secret.encode(), material, hashlib.sha256).hexdigest()
    return digest[:32]


# El identificador se genera en la aplicación y NO se usa `RETURNING`.
#
# Motivo, encontrado por la prueba `test_login_failures_are_audited_...`:
# `RETURNING` obliga a que la fila insertada pase también la política de
# LECTURA, porque devolverla es un SELECT. Durante el inicio de sesión la
# transacción todavía no tiene tenant —no se sabe quién llama hasta haber
# validado las credenciales— así que la política de lectura la rechaza y el
# INSERT falla entero.
#
# La alternativa habría sido relajar la política de lectura de `audit_log`, es
# decir, debilitar el aislamiento del registro de auditoría para poder escribir
# en él. Generar el UUID en Python cuesta una línea y deja intacta la propiedad
# que importa: cada organización solo lee su propio log.
_INSERT = text(
    """
    INSERT INTO audit_log (
        id, tenant_id, trace_id, actor_user_id, actor_role, action, outcome,
        decision_code, resource_type, resource_id, resource_tenant_id,
        policy_code, model, prompt_name, prompt_version,
        tools_called, documents_used, review_item_id,
        latency_ms, cost_eur, exposed_field_count, client_fingerprint, detail
    ) VALUES (
        CAST(:id AS uuid),
        CAST(NULLIF(:tenant_id, '') AS uuid), :trace_id,
        CAST(NULLIF(:actor_user_id, '') AS uuid), CAST(NULLIF(:actor_role,'') AS user_role),
        :action, CAST(:outcome AS audit_outcome),
        :decision_code, :resource_type, :resource_id,
        CAST(NULLIF(:resource_tenant_id, '') AS uuid),
        :policy_code, :model, :prompt_name, :prompt_version,
        CAST(:tools_called AS jsonb), CAST(:documents_used AS jsonb),
        CAST(NULLIF(:review_item_id, '') AS uuid),
        :latency_ms, :cost_eur, :exposed_field_count, :client_fingerprint,
        CAST(:detail AS jsonb)
    )
    """
)


def record(session: Session, event: AuditEvent) -> str | None:
    """Escribe el evento.

    Nunca propaga una excepción. Un fallo al auditar no puede tumbar la
    operación de negocio ni, peor, convertir un acceso denegado en un error 500
    que oculte la denegación. El fallo se registra en el log operativo con
    nivel crítico, que es donde debe verse.

    **La escritura va dentro de un `SAVEPOINT`.** Atrapar la excepción no basta:
    en PostgreSQL un statement fallido aborta la transacción entera, así que
    tragarse el error sin más no aísla nada —envenena todo lo que venga después,
    que empieza a fallar con `InFailedSqlTransaction`—. El resultado sería lo
    contrario de lo que promete el docstring: en lugar de que un fallo de
    auditoría no afecte a la operación, la operación se pierde entera y aun así
    responde 200. El punto de guardado revierte solo esta inserción y deja la
    transacción utilizable.
    """
    event_id = str(uuid.uuid4())
    params = {
        "id": event_id,
        "tenant_id": event.tenant_id or "",
        "trace_id": event.trace_id,
        "actor_user_id": event.actor_user_id or "",
        "actor_role": event.actor_role or "",
        "action": event.action,
        "outcome": event.outcome,
        "decision_code": event.decision_code,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "resource_tenant_id": event.resource_tenant_id or "",
        "policy_code": event.policy_code,
        "model": event.model,
        "prompt_name": event.prompt_name,
        "prompt_version": event.prompt_version,
        "tools_called": json.dumps(event.tools_called, ensure_ascii=False),
        "documents_used": json.dumps(event.documents_used, ensure_ascii=False),
        "review_item_id": event.review_item_id or "",
        "latency_ms": event.latency_ms,
        "cost_eur": event.cost_eur,
        "exposed_field_count": event.exposed_field_count,
        "client_fingerprint": event.client_fingerprint,
        "detail": json.dumps(event.detail, ensure_ascii=False),
    }
    try:
        with session.begin_nested():
            session.execute(_INSERT, params)
        return event_id
    except Exception:
        log.critical(
            "audit_write_failed",
            action=event.action,
            outcome=event.outcome,
            trace_id=event.trace_id,
            exc_info=True,
        )
        return None
