"""Cola de revisión humana y el ciclo de realimentación.

Dos ideas sostienen este módulo.

**Una decisión es definitiva.** Un elemento decidido no se vuelve a decidir. No
es rigidez burocrática: `decided_by` es el registro de quién se hizo responsable
de dejar pasar un contenido, y si se pudiera sobrescribir, esa responsabilidad
sería reasignable a posteriori. Reabrir un caso se hace generando contenido
nuevo, que entra en la cola como elemento nuevo y deja los dos rastros.

**Toda decisión produce un ejemplo de evaluación, también las aprobaciones.**
Esto es lo que convierte la cola en algo más que un trámite. El caso obvio es la
corrección: compliance reescribe una respuesta y con ello produce el par
`(lo que el agente dijo, lo que debía decir)`, que es literalmente la forma de un
caso de prueba.

El caso menos obvio, y el que importa, es la aprobación sin cambios. Significa
que el harness marcó para revisión algo que estaba bien: un **falso positivo**.
Si solo se recogieran las correcciones, el sistema únicamente podría aprender a
ser más cauto, los umbrales solo podrían apretarse, y la métrica «cada vez hay
menos correcciones» tendría un aspecto excelente mientras el producto bloquea
contenido legítimo y nadie lo mide. Un control que solo se puede endurecer
acaba apagado por quien lo sufre.

Ningún ejemplo entra en la suite de evaluación automáticamente
(`promoted_to_eval` nace en falso). Que una corrección sea correcta no la
convierte en un buen caso de prueba: puede ser irrepetible, específica de un
producto, o depender de un documento que ya no existe.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import ConflictError
from app.core.logging import get_logger
from app.services import audit
from app.services.access import Principal
from app.services.audit import AuditEvent

log = get_logger("review")

# Qué acción de auditoría corresponde a cada desenlace.
_AUDIT_ACTIONS: dict[str, str] = {
    "approved": audit.COMPLIANCE_REVIEW_APPROVED,
    "rejected": audit.COMPLIANCE_REVIEW_REJECTED,
    "edited": audit.COMPLIANCE_REVIEW_EDITED,
    # Pedir regeneración no es aprobar ni rechazar: es devolver el trabajo. Se
    # audita como rechazo porque su efecto sobre el contenido es el mismo —no se
    # entrega— y el matiz queda en el detalle.
    "regeneration_requested": audit.COMPLIANCE_REVIEW_REJECTED,
}

# Qué se espera del sistema la próxima vez, según el desenlace. Es el campo que
# hace que un ejemplo sirva para evaluar comportamiento y no solo para comparar
# dos cadenas de texto.
_DEFAULT_EXPECTATION: dict[str, str] = {
    "approved": (
        "El contenido era correcto tal cual. El harness lo marcó para revisión "
        "sin necesidad: es un falso positivo y debe contarse como tal al ajustar "
        "los umbrales."
    ),
    "rejected": (
        "El contenido no debía entregarse. El sistema hizo bien en marcarlo; se "
        "conserva como ejemplo de lo que hay que seguir deteniendo."
    ),
    "edited": (
        "El contenido necesitaba corrección. La versión editada es la respuesta "
        "esperada para un caso equivalente."
    ),
    "regeneration_requested": (
        "El contenido no era utilizable pero el caso sí es respondible. Se espera "
        "una respuesta correcta con las mismas fuentes."
    ),
}


def load_item(session: Session, review_item_id: str) -> dict[str, Any] | None:
    """Lee un elemento de la cola. Sin filtro de tenant: lo aplica RLS."""
    row = session.execute(
        text(
            "SELECT id, subject_type, agent_output_id, document_id, requested_by, "
            "       reason, policy_code, priority, status, original_content, "
            "       edited_content, decision_rationale, decided_by, decided_at, "
            "       created_at "
            "  FROM review_items WHERE id = CAST(:id AS uuid)"
        ),
        {"id": review_item_id},
    ).mappings().first()
    return dict(row) if row else None


def assert_decidable(item: dict[str, Any], principal: Principal) -> None:
    """Comprueba que el elemento admita decisión y que quien decide pueda hacerlo.

    La segunda comprobación —que el revisor no sea quien pidió la revisión— es
    en la práctica redundante: `compliance_officer` no tiene ningún permiso de
    generación, así que no puede ser el autor del contenido que revisa. Está
    aquí de todos modos porque esa redundancia depende de la matriz de permisos,
    y una matriz se edita. Si algún día alguien concede `chat.use` a compliance
    para una demostración, esta línea es lo que impide que la separación entre
    quien produce y quien valida desaparezca sin que nadie lo note.
    """
    if item["status"] != "pending":
        raise ConflictError(
            f"Este elemento ya fue resuelto como '{item['status']}'. "
            "Una decisión de compliance no se sobrescribe.",
            details={
                "status": item["status"],
                "decided_at": (
                    item["decided_at"].isoformat() if item["decided_at"] else None
                ),
                "rule": "REVIEW_DECISION_IS_FINAL",
            },
        )

    if str(item["requested_by"]) == principal.user_id:
        raise ConflictError(
            "Quien originó el contenido no puede validarlo",
            details={"rule": "SEPARATION_OF_DUTIES"},
        )


_DECIDE = text(
    """
    UPDATE review_items
       SET status             = CAST(:status AS review_status),
           decision_rationale = :rationale,
           edited_content     = COALESCE(:edited_content, edited_content),
           decided_by         = CAST(:decided_by AS uuid),
           decided_at         = now()
     WHERE id = CAST(:id AS uuid)
       -- Redundante con `assert_decidable`, y deliberado: cierra la ventana
       -- entre la lectura y la escritura. Dos revisores decidiendo a la vez
       -- sobre el mismo elemento producirían dos eventos de auditoría y una
       -- sola decisión ganadora, con el perdedor creyendo que la suya valió.
       AND status = 'pending'
    """
)

_INSERT_FEEDBACK = text(
    """
    INSERT INTO feedback_examples
        (id, tenant_id, review_item_id, original_answer, corrected_answer, reason,
         policy_code, expected_behaviour, promoted_to_eval)
    VALUES
        (CAST(:id AS uuid), CAST(:tenant_id AS uuid), CAST(:review_item_id AS uuid),
         :original_answer, :corrected_answer, :reason, :policy_code,
         :expected_behaviour, false)
    """
)


def decide(
    session: Session,
    principal: Principal,
    item: dict[str, Any],
    *,
    outcome: str,
    rationale: str,
    edited_content: str | None = None,
    expected_behaviour: str = "",
    extra_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resuelve un elemento de la cola y deja los tres rastros.

    Escribe la decisión, genera el ejemplo de realimentación y audita. Los tres
    en la misma transacción: una decisión registrada sin su evento de auditoría
    sería exactamente el agujero que este módulo existe para no tener.
    """
    if outcome not in _AUDIT_ACTIONS:
        raise ValueError(f"desenlace no declarado: {outcome}")

    updated = session.execute(
        _DECIDE,
        {
            "id": item["id"],
            "status": outcome,
            "rationale": rationale,
            "edited_content": edited_content,
            "decided_by": principal.user_id,
        },
    ).rowcount

    if not updated:
        # Otro revisor llegó primero entre la lectura y la escritura.
        raise ConflictError(
            "Otro revisor ha resuelto este elemento mientras tanto",
            details={"rule": "REVIEW_DECISION_IS_FINAL", "concurrent": True},
        )

    feedback_id = _record_feedback(
        session,
        principal,
        item,
        outcome=outcome,
        rationale=rationale,
        edited_content=edited_content,
        expected_behaviour=expected_behaviour,
    )

    audit.record(
        session,
        AuditEvent(
            action=_AUDIT_ACTIONS[outcome],
            outcome="success",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            resource_type="review_item",
            resource_id=str(item["id"]),
            resource_tenant_id=principal.tenant_id,
            review_item_id=str(item["id"]),
            policy_code=item["policy_code"],
            decision_code=outcome.upper(),
            exposed_field_count=0,
            client_fingerprint=principal.fingerprint,
            detail={
                "subject_type": item["subject_type"],
                "agent_output_id": (
                    str(item["agent_output_id"]) if item["agent_output_id"] else None
                ),
                "requested_by": str(item["requested_by"]),
                # El motivo entra en el log íntegro. Es el dato por el que se
                # consulta este registro seis meses después.
                "rationale": rationale,
                "content_was_edited": edited_content is not None,
                "feedback_example_created": feedback_id is not None,
                **(extra_detail or {}),
            },
        ),
    )

    log.info(
        "review_decided",
        review_item_id=str(item["id"]),
        outcome=outcome,
        subject=item["subject_type"],
    )

    return {
        "id": str(item["id"]),
        "status": outcome,
        "decided_by": principal.user_id,
        "feedback_example_id": feedback_id,
    }


def _record_feedback(
    session: Session,
    principal: Principal,
    item: dict[str, Any],
    *,
    outcome: str,
    rationale: str,
    edited_content: str | None,
    expected_behaviour: str,
) -> str | None:
    """Convierte la decisión en un ejemplo de evaluación.

    Se genera para **todos** los desenlaces. Ver la nota del módulo: recoger
    solo las correcciones produce un sistema que únicamente puede volverse más
    restrictivo, porque los falsos positivos no dejan rastro y por tanto no
    entran en ninguna métrica.
    """
    feedback_id = str(uuid.uuid4())
    try:
        # Dentro de un SAVEPOINT, por lo mismo que en auditoría y trazas: un
        # `except Exception` a secas sobre una sentencia fallida deja la
        # transacción abortada, y entonces lo que se pierde no es el ejemplo de
        # realimentación sino la decisión de compliance que ya se había escrito
        # dos líneas antes. El punto de guardado hace que «no imprescindible»
        # signifique de verdad no imprescindible.
        with session.begin_nested():
            session.execute(
                _INSERT_FEEDBACK,
                {
                    "id": feedback_id,
                    "tenant_id": principal.tenant_id,
                    "review_item_id": item["id"],
                    "original_answer": item["original_content"],
                    # Solo una edición aporta la respuesta correcta. En los demás
                    # desenlaces se deja nulo en lugar de repetir el original: un
                    # `corrected_answer` igual al original haría creer a la suite
                    # de evaluación que la respuesta esperada es la que se
                    # rechazó.
                    "corrected_answer": edited_content,
                    "reason": rationale,
                    "policy_code": item["policy_code"],
                    "expected_behaviour": (
                        expected_behaviour.strip() or _DEFAULT_EXPECTATION[outcome]
                    ),
                },
            )
        return feedback_id
    except Exception:
        # El ejemplo de realimentación es valioso, no imprescindible: la
        # decisión de compliance ya está tomada y registrada. Si esto falla, lo
        # que se pierde es un caso de prueba futuro, no la validez de la
        # decisión.
        log.warning(
            "feedback_example_failed", review_item_id=str(item["id"]), exc_info=True
        )
        return None
