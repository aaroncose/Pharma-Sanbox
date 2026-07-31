"""Consulta del registro de auditoría y reconstrucción de decisiones.

**Este router no tiene ningún endpoint de escritura.** No es que escribir exija
un permiso que nadie tiene: es que la operación no existe en la superficie de la
API. Junto con el trigger que rechaza `UPDATE` y `DELETE` sobre `audit_log` y el
`GRANT` revocado al rol de aplicación, son tres capas independientes. Un registro
de auditoría que se puede editar no es un registro de auditoría.

El endpoint que justifica el módulo es `/audit/trace/{trace_id}`. Reúne, bajo un
único identificador, los eventos de auditoría, los pasos del agente, la salida
producida y las fuentes citadas con su estado de entonces frente al de ahora. Es
la respuesta a «¿por qué el sistema dijo esto?», que sin correlación solo se
puede contestar especulando.

La exportación se audita a sí misma. Es el hueco clásico: el registro recoge
todo menos los accesos al propio registro, y entonces «quién se llevó una copia
de la actividad comercial» es justo la pregunta que no tiene respuesta.
"""

from __future__ import annotations

import csv
import io
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, rate_limit, require
from app.core.permissions import AUDIT_EXPORT, AUDIT_READ, TRACE_READ
from app.services import audit
from app.services.audit import AuditEvent

router = APIRouter(prefix="/audit", tags=["audit"])

# Tope de filas por exportación. No es una restricción de rendimiento: una
# exportación sin límite convierte un permiso de lectura en una copia completa
# de la actividad comercial de la organización, y ese es un evento distinto que
# merece una decisión distinta.
EXPORT_LIMIT = 5000


@router.get(
    "",
    dependencies=[Depends(require(AUDIT_READ)), Depends(rate_limit())],
)
def list_events(
    session: TenantSession,
    action: Annotated[str | None, Query(max_length=100)] = None,
    outcome: Annotated[str | None, Query()] = None,
    actor_user_id: Annotated[str | None, Query()] = None,
    resource_type: Annotated[str | None, Query(max_length=60)] = None,
    trace_id: Annotated[str | None, Query(max_length=60)] = None,
    since_hours: Annotated[int | None, Query(ge=1, le=8760)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """Eventos del registro, del más reciente al más antiguo.

    Ninguna consulta filtra por `tenant_id`: lo aplica RLS. Conviene notar la
    consecuencia, porque aquí no es la de siempre. Una organización ve los
    intentos que **sus** usuarios hicieron contra recursos ajenos —son actividad
    suya y debe poder investigarlos— y no ve los intentos que otros hicieron
    contra los suyos. Lo segundo puede parecer una carencia; es lo correcto:
    ese registro pertenece a la organización que lo generó, y enseñarlo diría
    quién intentó qué desde fuera, que es información sobre un tercero.
    """
    rows = session.execute(
        text(
            "SELECT a.id, a.occurred_at, a.trace_id, a.action, a.outcome::text, "
            "       a.decision_code, a.resource_type, a.resource_id, "
            "       a.policy_code, a.model, a.prompt_name, a.prompt_version, "
            "       a.latency_ms, a.cost_eur::double precision AS cost_eur, "
            "       a.exposed_field_count, a.actor_role::text AS actor_role, "
            "       a.detail, u.full_name AS actor_name, "
            "       (a.resource_tenant_id IS NOT NULL "
            "        AND a.resource_tenant_id <> a.tenant_id) AS cross_tenant, "
            "       jsonb_array_length(a.documents_used) AS documents_used "
            "  FROM audit_log a "
            "  LEFT JOIN users u ON u.id = a.actor_user_id "
            " WHERE (CAST(:action AS text) IS NULL OR a.action = :action) "
            "   AND (CAST(:outcome AS text) IS NULL "
            "        OR a.outcome::text = :outcome) "
            "   AND (CAST(:actor AS uuid) IS NULL "
            "        OR a.actor_user_id = CAST(:actor AS uuid)) "
            "   AND (CAST(:resource_type AS text) IS NULL "
            "        OR a.resource_type = :resource_type) "
            "   AND (CAST(:trace_id AS text) IS NULL OR a.trace_id = :trace_id) "
            "   AND (CAST(:since_hours AS int) IS NULL "
            "        OR a.occurred_at > now() - make_interval(hours => :since_hours)) "
            " ORDER BY a.occurred_at DESC LIMIT :limit"
        ),
        {
            "action": action,
            "outcome": outcome,
            "actor": actor_user_id,
            "resource_type": resource_type,
            "trace_id": trace_id,
            "since_hours": since_hours,
            "limit": limit,
        },
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get(
    "/security",
    dependencies=[Depends(require(AUDIT_READ)), Depends(rate_limit())],
)
def security_events(
    session: TenantSession,
    since_hours: Annotated[int, Query(ge=1, le=8760)] = 168,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, Any]:
    """Solo lo denegado, bloqueado o fallido.

    Existe como vista propia porque en volumen real los eventos de éxito ahogan
    a los demás por dos órdenes de magnitud, y la pantalla que hay que mirar es
    la de los intentos que no prosperaron. Un acceso denegado es más informativo
    que uno concedido: dice que alguien buscó donde no debía.

    Se devuelve `exposed_field_count` en cada fila porque es la afirmación
    comprobable: en un intento denegado vale cero, y eso es un dato del registro
    y no una deducción a partir del código de estado.
    """
    rows = session.execute(
        text(
            "SELECT a.id, a.occurred_at, a.trace_id, a.action, a.outcome::text, "
            "       a.decision_code, a.policy_code, a.resource_type, "
            "       a.resource_id, a.exposed_field_count, a.client_fingerprint, "
            "       a.actor_role::text AS actor_role, a.detail, "
            "       u.full_name AS actor_name, "
            "       (a.resource_tenant_id IS NOT NULL "
            "        AND a.resource_tenant_id <> a.tenant_id) AS cross_tenant "
            "  FROM audit_log a "
            "  LEFT JOIN users u ON u.id = a.actor_user_id "
            " WHERE a.outcome <> 'success' "
            "   AND a.occurred_at > now() - make_interval(hours => :since_hours) "
            " ORDER BY a.occurred_at DESC LIMIT :limit"
        ),
        {"since_hours": since_hours, "limit": limit},
    ).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "count": len(rows),
        # Si alguna fila denegada tuviera campos expuestos, la promesa del
        # sistema estaría rota y habría que verlo en la propia pantalla, no en
        # una prueba que quizá nadie ejecuta hoy.
        "leaked_rows": sum(1 for r in rows if r["exposed_field_count"] > 0),
    }


@router.get(
    "/stats",
    dependencies=[Depends(require(AUDIT_READ)), Depends(rate_limit())],
)
def statistics(
    session: TenantSession,
    since_hours: Annotated[int, Query(ge=1, le=8760)] = 168,
) -> dict[str, Any]:
    """Resumen para la pantalla de estado.

    El coste acumulado va aquí y no en una pantalla de facturación aparte
    porque es un dato de gobierno, no de contabilidad: quién gasta cuánto en
    generación es parte de saber cómo se está usando el sistema.
    """
    totals = session.execute(
        text(
            "SELECT count(*) AS events, "
            "       count(*) FILTER (WHERE outcome <> 'success') AS non_success, "
            "       count(*) FILTER (WHERE decision_code = "
            "                        'ACCESS_DENIED_CROSS_TENANT') AS cross_tenant, "
            "       count(*) FILTER (WHERE action = 'agent.blocked_by_policy') "
            "         AS blocked, "
            "       COALESCE(sum(cost_eur), 0)::double precision AS cost_eur, "
            "       count(DISTINCT actor_user_id) AS actors "
            "  FROM audit_log "
            " WHERE occurred_at > now() - make_interval(hours => :h)"
        ),
        {"h": since_hours},
    ).mappings().one()

    by_action = session.execute(
        text(
            "SELECT action, outcome::text AS outcome, count(*) AS n "
            "  FROM audit_log "
            " WHERE occurred_at > now() - make_interval(hours => :h) "
            " GROUP BY 1, 2 ORDER BY n DESC LIMIT 25"
        ),
        {"h": since_hours},
    ).mappings().all()

    review = session.execute(
        text(
            "SELECT status::text AS status, count(*) AS n "
            "  FROM review_items GROUP BY 1"
        )
    ).mappings().all()

    return {
        "window_hours": since_hours,
        "totals": dict(totals),
        "by_action": [dict(r) for r in by_action],
        "review_queue": {r["status"]: r["n"] for r in review},
    }


@router.get(
    "/trace/{trace_id}",
    dependencies=[Depends(require(TRACE_READ)), Depends(rate_limit())],
)
def reconstruct(
    trace_id: str,
    session: TenantSession,
) -> dict[str, Any]:
    """La cadena completa de una decisión, bajo un único identificador.

    Sin esto, cuando una respuesta sale mal solo se puede mirar el resultado y
    especular: la recuperación no encontró la sección, o la encontró y el modelo
    no la citó, o la citó y el verificador la rechazó. Son tres fallos distintos
    con tres arreglos distintos y desde fuera se parecen.

    No se deniega con 403 cuando no hay nada: un identificador de traza no es un
    recurso con propietario, y RLS ya garantiza que solo se vean las filas de la
    organización. Devolver un conjunto vacío es la respuesta correcta y no
    permite enumerar nada, porque los identificadores no son adivinables.
    """
    steps = session.execute(
        text(
            "SELECT step, step_type, name, status, input_summary, "
            "       output_summary, latency_ms, created_at "
            "  FROM agent_traces WHERE trace_id = :t ORDER BY step"
        ),
        {"t": trace_id},
    ).mappings().all()

    events = session.execute(
        text(
            "SELECT a.id, a.occurred_at, a.action, a.outcome::text AS outcome, "
            "       a.decision_code, a.policy_code, a.resource_type, "
            "       a.resource_id, a.exposed_field_count, a.detail, "
            "       u.full_name AS actor_name, a.actor_role::text AS actor_role "
            "  FROM audit_log a "
            "  LEFT JOIN users u ON u.id = a.actor_user_id "
            " WHERE a.trace_id = :t ORDER BY a.occurred_at"
        ),
        {"t": trace_id},
    ).mappings().all()

    output = session.execute(
        text(
            "SELECT id, kind, payload, answer_text, confidence, risk, "
            "       requires_human_review, blocked_reason, prompt_name, "
            "       prompt_version, model, provider, latency_ms, "
            "       cost_eur::double precision AS cost_eur, input_tokens, "
            "       output_tokens, created_at "
            "  FROM agent_outputs WHERE trace_id = :t AND deleted_at IS NULL"
        ),
        {"t": trace_id},
    ).mappings().first()

    sources: list[dict[str, Any]] = []
    if output is not None:
        sources = [
            dict(r)
            for r in session.execute(
                text(
                    "SELECT s.document_id, s.quoted_excerpt, s.document_version, "
                    "       s.document_status_at_use::text AS status_at_use, "
                    "       s.relevance, d.title, d.status::text AS status_now, "
                    "       (d.status::text <> s.document_status_at_use::text) "
                    "         AS status_changed "
                    "  FROM agent_output_sources s "
                    "  JOIN documents d ON d.id = s.document_id "
                    " WHERE s.agent_output_id = :oid"
                ),
                {"oid": output["id"]},
            ).mappings().all()
        ]

    return {
        "trace_id": trace_id,
        "found": bool(steps or events or output),
        "steps": [dict(s) for s in steps],
        "events": [dict(e) for e in events],
        "output": dict(output) if output else None,
        "sources": sources,
        # La comparación que hace auditable una retirada. Si es cierto, esta
        # salida citó material que después dejó de ser válido, y eso hay que
        # poder verlo sin cruzar tablas a mano.
        "cites_changed_documents": any(s["status_changed"] for s in sources),
        "total_latency_ms": sum(s["latency_ms"] for s in steps),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Exportación
# ─────────────────────────────────────────────────────────────────────────────

# Columnas que salen en el CSV. Se enumeran, y falta `detail` a propósito: es un
# `jsonb` libre donde acaban motivos de compliance y resúmenes de documentos, y
# volcarlo en una hoja de cálculo que circula por correo es exactamente el tipo
# de fuga que el resto del sistema evita. Quien necesite el detalle lo consulta
# en la pantalla, que deja su propio rastro.
_EXPORT_COLUMNS = (
    "occurred_at", "trace_id", "action", "outcome", "decision_code",
    "actor_name", "actor_role", "resource_type", "resource_id", "policy_code",
    "model", "prompt_name", "prompt_version", "latency_ms", "cost_eur",
    "exposed_field_count",
)


@router.get(
    "/export",
    dependencies=[Depends(require(AUDIT_EXPORT)), Depends(rate_limit("export", 5))],
)
def export_csv(
    principal: CurrentPrincipal,
    session: TenantSession,
    since_hours: Annotated[int, Query(ge=1, le=8760)] = 720,
    outcome: Annotated[str | None, Query()] = None,
) -> StreamingResponse:
    """Exporta el registro a CSV y **deja constancia de la exportación**.

    Es el hueco clásico de los sistemas de auditoría: el registro recoge todo
    menos los accesos al propio registro. Cuando después hay que responder a
    «¿quién se llevó una copia de la actividad comercial y cuándo?», resulta que
    esa es justamente la pregunta sin respuesta.

    El evento se escribe **antes** de generar el fichero. Si se escribiera
    después, una exportación interrumpida a mitad —o abortada a propósito— se
    llevaría los datos sin dejar rastro.
    """
    rows = session.execute(
        text(
            "SELECT a.occurred_at, a.trace_id, a.action, a.outcome::text AS outcome, "
            "       a.decision_code, u.full_name AS actor_name, "
            "       a.actor_role::text AS actor_role, a.resource_type, "
            "       a.resource_id, a.policy_code, a.model, a.prompt_name, "
            "       a.prompt_version, a.latency_ms, a.cost_eur, "
            "       a.exposed_field_count "
            "  FROM audit_log a "
            "  LEFT JOIN users u ON u.id = a.actor_user_id "
            " WHERE a.occurred_at > now() - make_interval(hours => :h) "
            "   AND (CAST(:outcome AS text) IS NULL "
            "        OR a.outcome::text = :outcome) "
            " ORDER BY a.occurred_at DESC LIMIT :limit"
        ),
        {"h": since_hours, "outcome": outcome, "limit": EXPORT_LIMIT},
    ).mappings().all()

    audit.record(
        session,
        AuditEvent(
            action=audit.AUDIT_EXPORTED,
            outcome="success",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            resource_type="audit_log",
            resource_tenant_id=principal.tenant_id,
            decision_code="AUDIT_EXPORTED",
            # Cuántas filas salieron de verdad. Es el dato que convierte «se
            # exportó el registro» en una afirmación con magnitud.
            exposed_field_count=len(rows) * len(_EXPORT_COLUMNS),
            client_fingerprint=principal.fingerprint,
            detail={
                "rows": len(rows),
                "columns": list(_EXPORT_COLUMNS),
                "window_hours": since_hours,
                "outcome_filter": outcome,
                "truncated": len(rows) >= EXPORT_LIMIT,
            },
        ),
    )
    session.commit()

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=_EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row[k] for k in _EXPORT_COLUMNS})
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="auditoria-{since_hours}h.csv"'
            ),
            # El fichero no debe quedarse en la caché de un proxy corporativo.
            "Cache-Control": "no-store",
            "X-Export-Rows": str(len(rows)),
            "X-Export-Truncated": "true" if len(rows) >= EXPORT_LIMIT else "false",
        },
    )
