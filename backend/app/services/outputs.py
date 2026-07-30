"""Persistencia de las salidas del agente.

Es la columna vertebral compartida por los cinco endpoints que invocan al
harness. Existe como módulo propio, y no como una función dentro de cada
router, porque lo que hace **no es opcional**: si cada endpoint decidiera por su
cuenta qué guardar, la trazabilidad dependería de que cinco sitios distintos se
acordaran de lo mismo.

Cuatro escrituras, en este orden y en la misma transacción:

  1. `agent_outputs`         la salida y su procedencia
  2. `agent_output_sources`  qué documentos se citaron, **congelados**
  3. `agent_traces`          los pasos que la produjeron
  4. `review_items`          si el harness lo exigió

Dos decisiones que conviene explicar.

**La cola de revisión la alimenta el sistema, no el usuario.** No hay un botón
de «mandar a compliance». Cuando el harness marca `requires_human_review`, la
entrada en la cola se crea aquí, dentro de la misma transacción que la salida.
Si fuera una acción del usuario, el camino de menor resistencia sería no
pulsarla, y la supervisión humana pasaría a ser opcional justo en los casos en
los que importa.

**Las fuentes se guardan congeladas.** `document_version` y
`document_status_at_use` son una foto del documento en el instante de la cita,
no una referencia a su estado actual. Cuando un documento se retira —el
escenario 1 del Failure Lab— la pregunta que hay que poder responder es «¿qué
material tenía delante el agente cuando dijo aquello?». Con una referencia viva
esa pregunta no tiene respuesta: al consultarla, el documento ya aparece
retirado y parece que el agente citó material retirado, que es una acusación
distinta y falsa.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.runner import AgentResult
from app.core.logging import get_logger
from app.services import audit
from app.services.access import Principal
from app.services.audit import AuditEvent

log = get_logger("outputs")

# Qué acción de auditoría corresponde a cada tipo de salida. El vocabulario es
# cerrado a propósito: un `kind` no declarado aquí no se puede persistir, en
# lugar de acabar en el log con una acción inventada que ninguna búsqueda de la
# pantalla de auditoría encontrará.
_AUDIT_ACTIONS: dict[str, str] = {
    "briefing": audit.AGENT_BRIEFING_GENERATED,
    "chat_answer": audit.AGENT_CHAT_ANSWERED,
    "meeting_summary": audit.AGENT_BRIEFING_GENERATED,
    "simulation_feedback": audit.AGENT_CHAT_ANSWERED,
}


def _payload_of(result: AgentResult) -> dict[str, Any]:
    """Cuerpo estructurado listo para `jsonb`.

    Una salida bloqueada tiene `output = None`. Se guarda igualmente, con el
    motivo: **un bloqueo es un resultado, no la ausencia de uno**. Si no se
    guardara, la única evidencia de que el sistema frenó algo sería el log, y la
    pantalla de auditoría no podría enseñar qué se frenó ni por qué.
    """
    if result.output is None:
        return {"blocked": True, "reason": result.blocked_reason}
    return result.output.model_dump(mode="json")


def _confidence_of(result: AgentResult) -> int:
    # La restricción `agent_outputs_blocked_has_no_confidence` exige que una
    # salida bloqueada tenga confianza cero. Se respeta aquí en lugar de dejar
    # que la base de datos rechace la fila: la comprobación de la base de datos
    # es la red, no el mecanismo.
    if result.output is None or result.blocked_reason is not None:
        return 0
    return int(getattr(result.output, "confidence", 0) or 0)


_INSERT_OUTPUT = text(
    """
    INSERT INTO agent_outputs
        (id, tenant_id, kind, user_id, hcp_id, product_id, payload, answer_text,
         confidence, risk, requires_human_review, blocked_reason, trace_id,
         prompt_name, prompt_version, model, provider, latency_ms, cost_eur,
         input_tokens, output_tokens)
    VALUES
        (CAST(:id AS uuid), CAST(:tenant_id AS uuid), CAST(:kind AS review_subject),
         CAST(:user_id AS uuid), CAST(NULLIF(:hcp_id,'') AS uuid),
         CAST(NULLIF(:product_id,'') AS uuid), CAST(:payload AS jsonb), :answer_text,
         :confidence, CAST(:risk AS risk_level), :requires_human_review,
         :blocked_reason, :trace_id, :prompt_name, :prompt_version, :model,
         :provider, :latency_ms, :cost_eur, :input_tokens, :output_tokens)
    """
)

_INSERT_SOURCE = text(
    """
    INSERT INTO agent_output_sources
        (tenant_id, agent_output_id, document_id, chunk_id, quoted_excerpt,
         document_version, document_status_at_use, relevance)
    VALUES
        (CAST(:tenant_id AS uuid), CAST(:output_id AS uuid),
         CAST(:document_id AS uuid), CAST(:chunk_id AS uuid), :excerpt,
         :version, CAST(:status AS document_status), :relevance)
    """
)

_INSERT_REVIEW = text(
    """
    INSERT INTO review_items
        (id, tenant_id, subject_type, agent_output_id, requested_by, reason,
         policy_code, priority, status, original_content)
    VALUES
        (CAST(:id AS uuid), CAST(:tenant_id AS uuid),
         CAST(:subject_type AS review_subject), CAST(:output_id AS uuid),
         CAST(:requested_by AS uuid), :reason, :policy_code,
         CAST(:priority AS task_priority), 'pending', :original_content)
    """
)


def _review_priority(result: AgentResult) -> str:
    """Prioridad en la cola, derivada del motivo, no elegida por el usuario.

    Un bloqueo por política y una confianza baja llegan los dos a la cola, pero
    no son lo mismo: el primero significa que el sistema paró algo, el segundo
    que el sistema no está seguro. Ordenar la cola por ese criterio es lo que
    evita que compliance atienda por orden de llegada.
    """
    risk = str(getattr(result.output, "risk_level", "low") or "low")
    if result.blocked_reason is not None or risk in ("high", "critical"):
        return "high"
    if result.verifier is not None and result.verifier.verdict == "unsupported":
        return "high"
    return "medium"


def _review_reason(result: AgentResult) -> tuple[str, str | None]:
    """Motivo legible y código de política asociado.

    El motivo lo lee una persona que tiene que decidir en segundos si esto le
    corresponde. «Requiere revisión» no le sirve de nada; «el verificador
    encontró 2 afirmaciones sin respaldo documental» sí.
    """
    if result.blocked_reason is not None:
        code = result.policy_codes[0] if result.policy_codes else None
        return f"Salida bloqueada por política: {result.blocked_reason}", code

    if result.verifier is not None and result.verifier.verdict != "supported":
        unsupported = len(result.verifier.unsupported_claims)
        return (
            f"El verificador calificó la respuesta como "
            f"'{result.verifier.verdict}' con {unsupported} "
            f"afirmación(es) sin respaldo documental.",
            result.policy_codes[0] if result.policy_codes else None,
        )

    if result.degraded:
        return (
            "La salida se generó en modo degradado: el proveedor de IA no "
            "respondió y no se pudo verificar el contenido.",
            None,
        )

    return (
        "El harness marcó la salida para revisión humana.",
        result.policy_codes[0] if result.policy_codes else None,
    )


def persist_result(
    session: Session,
    principal: Principal,
    result: AgentResult,
    *,
    kind: str,
    answer_text: str,
    hcp_id: str | None = None,
    product_id: str | None = None,
) -> dict[str, Any]:
    """Guarda una ejecución completa y devuelve sus identificadores.

    Devuelve `output_id` y, si procede, `review_item_id`. El endpoint que llama
    los incluye en la respuesta para que la interfaz pueda enlazar directamente
    con la traza y con la entrada de la cola: sin ese enlace, «esto está en
    revisión» es una afirmación que el usuario no puede comprobar.
    """
    if kind not in _AUDIT_ACTIONS:
        raise ValueError(f"kind de salida no declarado: {kind}")

    # El identificador se genera aquí y no con `RETURNING`, por el mismo motivo
    # que en auditoría: `RETURNING` obliga a que la fila pase también la
    # política de lectura, y eso acopla la escritura a una condición que no
    # tiene por qué cumplirse.
    output_id = str(uuid.uuid4())

    session.execute(
        _INSERT_OUTPUT,
        {
            "id": output_id,
            "tenant_id": principal.tenant_id,
            "kind": kind,
            "user_id": principal.user_id,
            "hcp_id": hcp_id or "",
            "product_id": product_id or "",
            "payload": json.dumps(_payload_of(result), ensure_ascii=False),
            "answer_text": answer_text or None,
            "confidence": _confidence_of(result),
            "risk": str(getattr(result.output, "risk_level", "low") or "low"),
            "requires_human_review": result.requires_human_review,
            "blocked_reason": result.blocked_reason,
            "trace_id": result.trace.trace_id,
            "prompt_name": result.prompt_name,
            "prompt_version": result.prompt_version,
            "model": result.model,
            "provider": result.provider,
            "latency_ms": result.latency_ms,
            "cost_eur": result.cost_eur,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    )

    # ── Fuentes citadas, congeladas en el momento del uso ────────────────────
    # Solo se guardan los fragmentos que la salida **citó**, no todos los
    # recuperados. Guardar los ocho recuperados haría que la pantalla de
    # trazabilidad mostrara documentos que el agente vio y descartó como si los
    # hubiera usado, que es precisamente el tipo de cita falsa que este
    # proyecto existe para evitar.
    cited = set(getattr(result.output, "sources", None) or [])
    for chunk in result.chunks:
        if chunk.source_id not in cited:
            continue
        session.execute(
            _INSERT_SOURCE,
            {
                "tenant_id": principal.tenant_id,
                "output_id": output_id,
                "document_id": chunk.document_id,
                "chunk_id": chunk.chunk_id,
                "excerpt": chunk.excerpt(),
                "version": chunk.document_version,
                "status": chunk.document_status,
                "relevance": chunk.similarity,
            },
        )

    # ── Traza ────────────────────────────────────────────────────────────────
    result.trace.persist(session)

    # ── Cola de revisión ─────────────────────────────────────────────────────
    review_item_id: str | None = None
    if result.requires_human_review:
        reason, policy_code = _review_reason(result)
        review_item_id = str(uuid.uuid4())
        session.execute(
            _INSERT_REVIEW,
            {
                "id": review_item_id,
                "tenant_id": principal.tenant_id,
                "subject_type": kind,
                "output_id": output_id,
                "requested_by": principal.user_id,
                "reason": reason,
                "policy_code": policy_code,
                "priority": _review_priority(result),
                # Se guarda el texto tal cual se generó. Si compliance lo edita,
                # el original sigue estando: la comparación entre lo que el
                # agente dijo y lo que una persona dejó pasar es el dato que
                # convierte la cola en una fuente de casos de evaluación.
                "original_content": answer_text or "(salida bloqueada, sin texto)",
            },
        )

    # ── Auditoría ────────────────────────────────────────────────────────────
    audit.record(
        session,
        AuditEvent(
            action=(
                audit.AGENT_BLOCKED_BY_POLICY
                if result.blocked_reason
                else _AUDIT_ACTIONS[kind]
            ),
            outcome="blocked" if result.blocked_reason else "success",
            trace_id=result.trace.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            resource_type="agent_output",
            resource_id=output_id,
            resource_tenant_id=principal.tenant_id,
            policy_code=result.policy_codes[0] if result.policy_codes else None,
            model=result.model,
            prompt_name=result.prompt_name,
            prompt_version=result.prompt_version,
            documents_used=[
                {
                    "document_id": c.document_id,
                    "version": c.document_version,
                    "section": c.section,
                    "cited": c.source_id in cited,
                }
                for c in result.chunks
            ],
            review_item_id=review_item_id,
            latency_ms=result.latency_ms,
            cost_eur=result.cost_eur,
            # Cuántos campos se entregaron de verdad. En una salida bloqueada es
            # cero, y esa afirmación es comprobable en lugar de deducida del
            # código de estado.
            exposed_field_count=0 if result.blocked_reason else 1,
            client_fingerprint=principal.fingerprint,
            detail={
                "confidence": _confidence_of(result),
                "verifier_verdict": (
                    result.verifier.verdict if result.verifier else None
                ),
                "degraded": result.degraded,
                "policy_codes": result.policy_codes,
                "chunks_retrieved": len(result.chunks),
                "chunks_cited": len(cited),
            },
        ),
    )

    log.info(
        "agent_output_persisted",
        kind=kind,
        output_id=output_id,
        trace_id=result.trace.trace_id,
        blocked=result.blocked_reason,
        review=review_item_id is not None,
        cost_eur=result.cost_eur,
    )

    return {
        "output_id": output_id,
        "review_item_id": review_item_id,
        "trace_id": result.trace.trace_id,
    }


def envelope(result: AgentResult, ids: dict[str, Any]) -> dict[str, Any]:
    """Respuesta HTTP común a todos los endpoints de agente.

    La forma es la misma llegue lo que llegue —entregado, bloqueado o
    degradado— porque el cliente no debería tener tres caminos de código para
    tres estados que son el mismo evento con distinto desenlace. `delivered`
    dice cuál de los tres es.
    """
    return {
        "delivered": result.delivered,
        "blocked_reason": result.blocked_reason,
        "requires_human_review": result.requires_human_review,
        "degraded": result.degraded,
        "policy_codes": result.policy_codes,
        "output": result.output.model_dump(mode="json") if result.output else None,
        "verifier": (
            result.verifier.model_dump(mode="json") if result.verifier else None
        ),
        "sources": [
            {
                "source_id": c.source_id,
                "document_id": c.document_id,
                "title": c.document_title,
                "version": c.document_version,
                "section": c.section,
                "excerpt": c.excerpt(240),
                "similarity": c.similarity,
                "semantic_rank": c.semantic_rank,
                "lexical_rank": c.lexical_rank,
            }
            for c in result.chunks
        ],
        "meta": {
            **ids,
            "model": result.model,
            "provider": result.provider,
            "prompt": f"{result.prompt_name}@{result.prompt_version}",
            "latency_ms": result.latency_ms,
            "cost_eur": result.cost_eur,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    }
