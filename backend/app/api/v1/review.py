"""Cola de revisión humana.

Es la pantalla donde el producto deja de ser una demostración de IA y pasa a ser
un sistema con responsabilidad asignada. Todo lo que el harness marca acaba
aquí, y de aquí sale una decisión con nombre, fecha y motivo escrito.

La cola **no se ordena por antigüedad**. Se ordena por prioridad y después por
antigüedad, y la prioridad la derivó el sistema del motivo: un bloqueo por
política y una confianza baja no son lo mismo. Ordenar solo por llegada
convierte la cola en una bandeja de entrada, donde lo urgente espera detrás de
lo trivial que llegó antes.

Ninguna consulta filtra por `tenant_id`. Lo aplica RLS.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, rate_limit, require
from app.core.errors import CrossTenantAccessError
from app.core.permissions import REVIEW_DECIDE, REVIEW_READ
from app.schemas.review import RegenerationRequest, ReviewDecision, ReviewEdit
from app.services import review
from app.services.access import deny_cross_tenant

router = APIRouter(prefix="/review", tags=["review"])


def _load_or_deny(
    session: TenantSession, principal: CurrentPrincipal, review_item_id: str
) -> dict[str, Any]:
    """Recupera el elemento o deniega igual que cualquier otro recurso.

    Un elemento de otra organización y uno inexistente producen el mismo 403.
    Aquí importa especialmente: los identificadores de la cola de revisión de un
    competidor dirían cuánto contenido problemático genera, que es información
    comercial aunque no se lea ni un campo.
    """
    item = review.load_item(session, review_item_id)
    if item is None:
        deny_cross_tenant(
            session,
            principal,
            table="review_items",
            resource_id=review_item_id,
            resource_type="review_item",
        )
        raise CrossTenantAccessError()  # inalcanzable; ayuda al verificador de tipos
    return item


@router.get(
    "",
    dependencies=[Depends(require(REVIEW_READ)), Depends(rate_limit())],
)
def list_queue(
    session: TenantSession,
    status: Annotated[str, Query()] = "pending",
    priority: Annotated[str | None, Query()] = None,
    subject_type: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """La cola, ordenada por prioridad y luego por antigüedad.

    Se devuelve `waiting_hours` calculado en la base de datos en lugar de dejar
    que el cliente reste fechas. Es el número que responde a la única pregunta
    de gestión que importa aquí —«¿cuánto lleva esperando lo más urgente?»— y
    calcularlo en el cliente lo haría depender del reloj del navegador.
    """
    rows = session.execute(
        text(
            "SELECT r.id, r.subject_type, r.agent_output_id, r.document_id, "
            "       r.reason, r.policy_code, r.priority, r.status, "
            "       r.original_content, r.created_at, r.decided_at, "
            "       u.full_name AS requested_by_name, "
            "       d.full_name AS decided_by_name, "
            "       o.confidence, o.risk, o.model, o.trace_id, "
            # El cast a `double precision` no es cosmético: `round(numeric, n)`
            # devuelve `numeric`, que viaja a JSON como cadena. Un cliente que
            # ordenara la cola por este campo ordenaría alfabéticamente, y
            # "9.5" iría detrás de "11.0" justo cuando lo que se busca es lo
            # que más lleva esperando.
            "       round(EXTRACT(EPOCH FROM (now() - r.created_at)) / 3600.0, 1)"
            "         ::double precision AS waiting_hours "
            "  FROM review_items r "
            "  JOIN users u ON u.id = r.requested_by "
            "  LEFT JOIN users d ON d.id = r.decided_by "
            "  LEFT JOIN agent_outputs o ON o.id = r.agent_output_id "
            " WHERE (CAST(:status AS text) IS NULL OR r.status::text = :status) "
            "   AND (CAST(:priority AS text) IS NULL "
            "        OR r.priority::text = :priority) "
            "   AND (CAST(:subject_type AS text) IS NULL "
            "        OR r.subject_type::text = :subject_type) "
            " ORDER BY CASE r.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
            "                          ELSE 2 END, "
            "          r.created_at ASC "
            " LIMIT :limit"
        ),
        {
            # `status=all` desactiva el filtro. El valor por defecto es
            # 'pending' porque una cola que abre mostrando lo ya resuelto invita
            # a no mirar lo que falta.
            "status": None if status == "all" else status,
            "priority": priority,
            "subject_type": subject_type,
            "limit": limit,
        },
    ).mappings().all()

    counts = session.execute(
        text(
            "SELECT status::text AS status, count(*) AS n "
            "  FROM review_items GROUP BY 1"
        )
    ).mappings().all()

    return {
        "items": [dict(r) for r in rows],
        "count": len(rows),
        "totals": {c["status"]: c["n"] for c in counts},
    }


@router.get(
    "/{review_item_id}",
    dependencies=[Depends(require(REVIEW_READ)), Depends(rate_limit())],
)
def get_item(
    review_item_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """El elemento con todo lo necesario para decidir sin salir de la pantalla.

    Se adjunta la salida completa del agente, sus fuentes y el veredicto del
    verificador. Una revisión que obliga a abrir tres pestañas para reunir el
    contexto se hace en diagonal, y una revisión en diagonal es peor que
    ninguna: produce una firma sin criterio detrás.
    """
    item = _load_or_deny(session, principal, review_item_id)

    output: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = []

    if item["agent_output_id"]:
        row = session.execute(
            text(
                "SELECT id, kind, payload, answer_text, confidence, risk, "
                "       blocked_reason, trace_id, prompt_name, prompt_version, "
                "       model, provider, cost_eur, created_at "
                "  FROM agent_outputs WHERE id = CAST(:id AS uuid)"
            ),
            {"id": item["agent_output_id"]},
        ).mappings().first()
        output = dict(row) if row else None

        sources = [
            dict(s)
            for s in session.execute(
                text(
                    "SELECT s.document_id, s.quoted_excerpt, s.document_version, "
                    "       s.document_status_at_use, s.relevance, d.title, "
                    "       d.status AS status_now, "
                    "       (d.status::text <> s.document_status_at_use::text) "
                    "         AS status_changed "
                    "  FROM agent_output_sources s "
                    "  JOIN documents d ON d.id = s.document_id "
                    " WHERE s.agent_output_id = CAST(:id AS uuid)"
                ),
                {"id": item["agent_output_id"]},
            ).mappings().all()
        ]

    return {**item, "agent_output": output, "sources": sources}


# ─────────────────────────────────────────────────────────────────────────────
# Decisiones
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/{review_item_id}/approve",
    dependencies=[Depends(require(REVIEW_DECIDE)), Depends(rate_limit())],
)
def approve(
    review_item_id: str,
    payload: ReviewDecision,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Aprueba el contenido tal como está.

    Genera un ejemplo de realimentación igual que un rechazo, y por un motivo
    que conviene tener presente: si el harness marcó esto y una persona lo
    aprueba sin tocarlo, el harness se equivocó. Es un falso positivo, y es el
    único dato con el que se pueden **relajar** los umbrales. Sin registrarlo,
    el sistema solo puede volverse más restrictivo con el tiempo.
    """
    item = _load_or_deny(session, principal, review_item_id)
    review.assert_decidable(item, principal)
    return review.decide(
        session, principal, item, outcome="approved", rationale=payload.rationale
    )


@router.post(
    "/{review_item_id}/reject",
    dependencies=[Depends(require(REVIEW_DECIDE)), Depends(rate_limit())],
)
def reject(
    review_item_id: str,
    payload: ReviewDecision,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Rechaza el contenido. No se entrega y queda el motivo."""
    item = _load_or_deny(session, principal, review_item_id)
    review.assert_decidable(item, principal)
    return review.decide(
        session, principal, item, outcome="rejected", rationale=payload.rationale
    )


@router.post(
    "/{review_item_id}/edit",
    dependencies=[Depends(require(REVIEW_DECIDE)), Depends(rate_limit())],
)
def edit(
    review_item_id: str,
    payload: ReviewEdit,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Corrige el contenido y lo aprueba en su versión corregida.

    El original **no se toca**. `original_content` sigue conteniendo lo que el
    agente produjo, y `edited_content` lo que la persona dejó. La comparación
    entre los dos es el par `(lo que dijo, lo que debía decir)`: la forma exacta
    de un caso de evaluación. Si la edición sobrescribiera el original, cada
    corrección destruiría el dato que la hace útil.
    """
    item = _load_or_deny(session, principal, review_item_id)
    review.assert_decidable(item, principal)
    return review.decide(
        session,
        principal,
        item,
        outcome="edited",
        rationale=payload.rationale,
        edited_content=payload.edited_content,
        expected_behaviour=payload.expected_behaviour,
    )


@router.post(
    "/{review_item_id}/request-regeneration",
    dependencies=[Depends(require(REVIEW_DECIDE)), Depends(rate_limit())],
)
def request_regeneration(
    review_item_id: str,
    payload: RegenerationRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Devuelve el trabajo al comercial con indicaciones.

    No regenera aquí. Regenerar desde la cola produciría contenido nuevo cuyo
    autor sería el revisor, y el revisor dejaría de poder revisarlo: la
    separación entre quien produce y quien valida se perdería en el mismo gesto
    que pretende reforzarla.
    """
    item = _load_or_deny(session, principal, review_item_id)
    review.assert_decidable(item, principal)
    return review.decide(
        session,
        principal,
        item,
        outcome="regeneration_requested",
        rationale=payload.rationale,
        extra_detail={"guidance": payload.guidance},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Realimentación
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/feedback/examples",
    dependencies=[Depends(require(REVIEW_READ)), Depends(rate_limit())],
)
def list_feedback(
    session: TenantSession,
    promoted: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """Los casos que ha producido la supervisión humana.

    Es el ciclo cerrado: cada vez que una persona decide sobre una salida, el
    sistema gana un caso de prueba que antes no tenía. Ninguno entra en la suite
    de evaluación automáticamente —`promoted_to_eval` nace en falso—, porque que
    una corrección sea correcta no la convierte en buen caso de prueba: puede
    ser irrepetible, específica de un producto, o depender de un documento que
    ya no existe.
    """
    rows = session.execute(
        text(
            "SELECT f.id, f.review_item_id, f.original_answer, f.corrected_answer, "
            "       f.reason, f.policy_code, f.expected_behaviour, "
            "       f.promoted_to_eval, f.created_at, "
            "       r.subject_type, r.status AS decision "
            "  FROM feedback_examples f "
            "  JOIN review_items r ON r.id = f.review_item_id "
            " WHERE (CAST(:promoted AS boolean) IS NULL "
            "        OR f.promoted_to_eval = CAST(:promoted AS boolean)) "
            " ORDER BY f.created_at DESC LIMIT :limit"
        ),
        {"promoted": promoted, "limit": limit},
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}
