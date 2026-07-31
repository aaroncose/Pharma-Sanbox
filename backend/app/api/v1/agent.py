"""Endpoints que invocan al agente: briefing, asistente documental y resumen.

Los tres tienen la misma forma y no es casualidad. Reúnen el contexto, llaman a
`AgentRunner.run` y persisten el resultado con `outputs.persist_result`. Ninguno
llama al modelo por su cuenta ni decide si algo requiere revisión: eso lo
resuelve el harness, y aquí no hay forma de saltárselo porque no existe un
camino alternativo hacia el proveedor.

La consecuencia práctica es que un endpoint nuevo hereda gratis las políticas,
el verificador, la traza, la auditoría y la cola de revisión. Y, más importante,
que no puede *no* heredarlos por descuido.

**El límite de tasa del agente es un cubo aparte.** Una lectura cuesta
milisegundos; una generación cuesta segundos y dinero. Compartir presupuesto
haría que navegar por la interfaz consumiera el derecho a generar.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.agent.runner import get_runner
from app.agent.schemas import BriefingOutput, ChatOutput, MeetingSummaryOutput
from app.api.deps import (
    CurrentPrincipal,
    TenantSession,
    agent_rate_limit,
    rate_limit,
    require,
)
from app.core.permissions import (
    BRIEFING_CREATE,
    BRIEFING_READ,
    CHAT_USE,
    SUMMARY_CREATE,
    TRACE_READ,
)
from app.schemas.agent import BriefingRequest, ChatRequest, MeetingSummaryRequest
from app.services import outputs
from app.services.access import fetch_scoped_one

router = APIRouter(prefix="/agent", tags=["agent"])


# ─────────────────────────────────────────────────────────────────────────────
# Construcción de contexto
# ─────────────────────────────────────────────────────────────────────────────


def _tenant_name(session: TenantSession) -> str:
    return session.execute(text("SELECT name FROM tenants LIMIT 1")).scalar() or ""


def _product(session: TenantSession, principal: Any, product_id: str) -> dict[str, Any]:
    return fetch_scoped_one(
        session,
        principal,
        table="products",
        resource_id=product_id,
        columns="id, code, name, therapeutic_area, description",
        extra_where="is_active = true",
        resource_type="product",
    )


def _hcp_context(
    session: TenantSession, principal: Any, hcp_id: str
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Perfil del profesional y su historial, **si el consentimiento lo permite**.

    Este es el punto donde la minimización de datos deja de ser una declaración.
    `consent_data_analysis` decide si el historial de interacciones entra en el
    prompt. Sin él, el agente trabaja solo con documentación de producto.

    Se comprueba aquí, al construir el contexto, y no dentro del prompt. Pedirle
    al modelo que «no use el historial si no hay consentimiento» requeriría
    haberle pasado el historial primero, y a partir de ese momento el dato ya
    salió de la base de datos, viajó al proveedor y quedó en el prompt. El único
    sitio donde la comprobación significa algo es antes de leerlo.

    Devuelve también la razón, para que la interfaz pueda decir por qué el
    briefing es más pobre en lugar de parecer que el sistema no encontró nada.
    """
    hcp = fetch_scoped_one(
        session,
        principal,
        table="healthcare_professionals",
        resource_id=hcp_id,
        columns=(
            "id, full_name, specialty, institution, city, notes, "
            "consent_contact, consent_data_analysis"
        ),
        extra_where="deleted_at IS NULL",
        resource_type="healthcare_professional",
    )

    consent = {
        "contact": bool(hcp["consent_contact"]),
        "data_analysis": bool(hcp["consent_data_analysis"]),
        "history_included": False,
        "reason": None,
    }

    if not hcp["consent_data_analysis"]:
        consent["reason"] = (
            "El profesional no ha consentido el análisis de datos. El briefing "
            "se ha generado únicamente con documentación de producto."
        )
        blocked = "(historial no disponible: sin consentimiento de análisis de datos)"
        return hcp, blocked, consent

    rows = session.execute(
        text(
            "SELECT occurred_at, channel, topics, summary, open_questions "
            "  FROM interactions "
            " WHERE hcp_id = CAST(:hcp_id AS uuid) AND deleted_at IS NULL "
            " ORDER BY occurred_at DESC LIMIT 5"
        ),
        {"hcp_id": hcp_id},
    ).mappings().all()

    if not rows:
        return hcp, "(sin interacciones previas registradas)", consent

    consent["history_included"] = True
    lines = [
        f"- {r['occurred_at']:%Y-%m-%d} · {r['channel']} · temas: "
        f"{', '.join(r['topics']) or 'sin registrar'}\n"
        f"  Resumen: {r['summary']}\n"
        f"  Preguntas abiertas: {'; '.join(r['open_questions']) or 'ninguna'}"
        for r in rows
    ]
    return hcp, "\n".join(lines), consent


# ─────────────────────────────────────────────────────────────────────────────
# Briefing
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/briefing",
    dependencies=[Depends(require(BRIEFING_CREATE)), Depends(agent_rate_limit())],
)
def generate_briefing(
    payload: BriefingRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Prepara una visita a partir de documentación aprobada e historial."""
    product = _product(session, principal, payload.product_id)
    hcp, history, consent = _hcp_context(session, principal, payload.hcp_id)

    actor_name = session.execute(
        text("SELECT full_name FROM users WHERE id = CAST(:id AS uuid)"),
        {"id": principal.user_id},
    ).scalar() or ""

    result = get_runner().run(
        session,
        task="briefing",
        model_cls=BriefingOutput,
        # La consulta de recuperación se construye con el objetivo y el
        # producto, no con el nombre del profesional. Buscar por nombre en la
        # biblioteca documental no tiene sentido —los documentos hablan de
        # productos— y además metería un dato personal en la consulta.
        question=(
            f"{payload.objective} · {product['name']} · {product['therapeutic_area']}"
        ),
        prompt_variables={
            "tenant_name": _tenant_name(session),
            "user_name": actor_name,
            "product_name": product["name"],
            "hcp_summary": (
                f"{hcp['full_name']} — {hcp['specialty']}, {hcp['institution']} "
                f"({hcp['city']})"
            ),
            "history": history,
            "objective": payload.objective,
            "duration_minutes": payload.duration_minutes,
        },
        tenant_id=principal.tenant_id,
        product_id=payload.product_id,
        prompt_version=payload.prompt_version,
        trace_id=principal.trace_id,
    )

    ids = outputs.persist_result(
        session,
        principal,
        result,
        kind="briefing",
        answer_text=_briefing_text(result),
        hcp_id=payload.hcp_id,
        product_id=payload.product_id,
    )

    return {**outputs.envelope(result, ids), "consent": consent}


def _briefing_text(result: Any) -> str:
    """Texto plano del briefing, para la cola de revisión.

    Compliance revisa texto, no JSON. Si la cola guardara el objeto
    estructurado, revisar exigiría leer un volcado con llaves, y una revisión
    incómoda es una revisión que se hace mal.
    """
    if result.output is None:
        return ""
    parts = [result.output.hcp_summary]
    parts += [f"- {t.topic}: {t.rationale}" for t in result.output.recommended_topics]
    parts += [
        f"P: {q.question}\nR: {q.suggested_answer}"
        for q in result.output.likely_questions
    ]
    return "\n\n".join(p for p in parts if p)


# ─────────────────────────────────────────────────────────────────────────────
# Asistente documental
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/chat",
    dependencies=[Depends(require(CHAT_USE)), Depends(agent_rate_limit())],
)
def ask_documents(
    payload: ChatRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Pregunta sobre la documentación aprobada.

    No hay historial de conversación. Cada pregunta se responde contra la
    documentación recuperada para ella y nada más. Encadenar turnos permitiría
    que una respuesta anterior —que ya pasó por el verificador y pudo salir con
    matices— se convirtiera en contexto de la siguiente sin volver a
    verificarse: el mecanismo por el que una afirmación endeble se consolida en
    firme a lo largo de una conversación.
    """
    product = (
        _product(session, principal, payload.product_id) if payload.product_id else None
    )

    result = get_runner().run(
        session,
        task="chat",
        model_cls=ChatOutput,
        question=payload.question,
        prompt_variables={
            "tenant_name": _tenant_name(session),
            "product_name": product["name"] if product else "(sin producto concreto)",
            "question": payload.question,
        },
        tenant_id=principal.tenant_id,
        product_id=payload.product_id,
        prompt_version=payload.prompt_version,
        trace_id=principal.trace_id,
    )

    ids = outputs.persist_result(
        session,
        principal,
        result,
        kind="chat_answer",
        answer_text=result.output.answer if result.output else "",
        product_id=payload.product_id,
    )

    return outputs.envelope(result, ids)


# ─────────────────────────────────────────────────────────────────────────────
# Resumen posterior a la visita
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "/summary",
    dependencies=[Depends(require(SUMMARY_CREATE)), Depends(agent_rate_limit())],
)
def summarize_meeting(
    payload: MeetingSummaryRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Convierte notas de visita en resumen verificado, tareas e interacción.

    Lo que distingue este endpoint de un resumidor es que cada compromiso que el
    comercial adquirió se contrasta con la documentación aprobada. Un resumen
    que solo condensa pierde justamente el dato que importa: la frase dicha en
    una consulta, sin testigos, que promete algo que la ficha técnica no
    sostiene.
    """
    product = _product(session, principal, payload.product_id)
    hcp = fetch_scoped_one(
        session,
        principal,
        table="healthcare_professionals",
        resource_id=payload.hcp_id,
        columns="id, full_name, specialty, institution",
        extra_where="deleted_at IS NULL",
        resource_type="healthcare_professional",
    )

    result = get_runner().run(
        session,
        task="meeting_summary",
        model_cls=MeetingSummaryOutput,
        # Se recupera documentación con las notas como consulta: son ellas las
        # que contienen las afirmaciones que hay que contrastar.
        question=f"{payload.notes[:500]} · {product['name']}",
        prompt_variables={
            "tenant_name": _tenant_name(session),
            "hcp_name": hcp["full_name"],
            "specialty": hcp["specialty"],
            "institution": hcp["institution"],
            "product_name": product["name"],
            "occurred_at": payload.occurred_at or "sin fecha registrada",
            "channel": payload.channel,
            "notes": payload.notes,
        },
        tenant_id=principal.tenant_id,
        product_id=payload.product_id,
        prompt_version=payload.prompt_version,
        trace_id=principal.trace_id,
    )

    ids = outputs.persist_result(
        session,
        principal,
        result,
        kind="meeting_summary",
        answer_text=result.output.summary if result.output else "",
        hcp_id=payload.hcp_id,
        product_id=payload.product_id,
    )

    created: dict[str, Any] = {"interaction_id": None, "task_ids": []}

    # La interacción y las tareas solo se crean si la salida se entregó. Un
    # resumen bloqueado por política no debe dejar registro de visita: si lo
    # dejara, el historial del profesional acabaría conteniendo el texto que el
    # sistema decidió no entregar, y el briefing siguiente lo leería como
    # contexto válido. El bloqueo se convertiría en un retraso de un ciclo.
    if result.delivered and payload.record_interaction:
        created["interaction_id"] = _record_interaction(
            session, principal, payload, result
        )
        created["task_ids"] = _create_tasks(
            session, principal, payload, result, source_output_id=ids["output_id"]
        )

    return {**outputs.envelope(result, ids), "created": created}


def _record_interaction(
    session: TenantSession,
    principal: Any,
    payload: MeetingSummaryRequest,
    result: Any,
) -> str:
    interaction_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO interactions "
            "  (id, tenant_id, hcp_id, user_id, product_id, occurred_at, channel, "
            "   topics, summary, open_questions) "
            "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), "
            "        CAST(:hcp_id AS uuid), CAST(:user_id AS uuid), "
            "        CAST(:product_id AS uuid), "
            "        COALESCE(CAST(NULLIF(:occurred_at,'') AS timestamptz), now()), "
            "        CAST(:channel AS interaction_channel), :topics, :summary, "
            "        :open_questions)"
        ),
        {
            "id": interaction_id,
            "tenant_id": principal.tenant_id,
            "hcp_id": payload.hcp_id,
            "user_id": principal.user_id,
            "product_id": payload.product_id,
            "occurred_at": payload.occurred_at or "",
            "channel": payload.channel,
            "topics": [t.title for t in result.output.follow_up_tasks][:5],
            "summary": result.output.summary,
            "open_questions": result.output.open_questions,
        },
    )
    return interaction_id


def _create_tasks(
    session: TenantSession,
    principal: Any,
    payload: MeetingSummaryRequest,
    result: Any,
    *,
    source_output_id: str,
) -> list[str]:
    """Crea las tareas de seguimiento propuestas.

    Dos decisiones.

    **Se asignan al propio comercial que pidió el resumen**, nunca a otra
    persona. Que un agente cree trabajo en la lista de un tercero a partir de
    unas notas que ese tercero no ha visto es un problema organizativo, no una
    funcionalidad.

    **Cada tarea guarda de qué salida vino** (`source_type`, `source_id`). Sin
    ese enlace, una tarea generada es una orden sin procedencia: aparece en la
    lista de alguien sin forma de saber quién la propuso, con qué notas y con
    qué documentación delante. Con él, desde la tarea se llega a la traza.
    """
    task_ids: list[str] = []
    # Se acotan a diez. Un modelo que proponga cuarenta tareas de seguimiento a
    # partir de unas notas está generando ruido, y el límite lo contiene aquí en
    # lugar de dejar que llene la lista de trabajo de una persona.
    for task in result.output.follow_up_tasks[:10]:
        task_id = str(uuid.uuid4())
        session.execute(
            text(
                "INSERT INTO tasks "
                "  (id, tenant_id, user_id, hcp_id, product_id, title, detail, "
                "   priority, status, due_date, source_type, source_id) "
                "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), "
                "        CAST(:user_id AS uuid), CAST(:hcp_id AS uuid), "
                "        CAST(:product_id AS uuid), :title, :detail, "
                "        CAST(:priority AS task_priority), 'open', "
                "        (now() + make_interval(days => :due_in_days))::date, "
                "        'meeting_summary', CAST(:source_id AS uuid))"
            ),
            {
                "id": task_id,
                "tenant_id": principal.tenant_id,
                "user_id": principal.user_id,
                "hcp_id": payload.hcp_id,
                "product_id": payload.product_id,
                "title": task.title[:300],
                "detail": task.detail,
                "priority": task.priority,
                "due_in_days": max(1, min(90, task.due_in_days)),
                "source_id": source_output_id,
            },
        )
        task_ids.append(task_id)
    return task_ids


# ─────────────────────────────────────────────────────────────────────────────
# Consulta de salidas y trazas
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/outputs",
    dependencies=[Depends(require(BRIEFING_READ)), Depends(rate_limit())],
)
def list_outputs(
    session: TenantSession,
    kind: Annotated[str | None, Query()] = None,
    only_review: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT o.id, o.kind, o.confidence, o.risk, o.requires_human_review, "
            "       o.blocked_reason, o.trace_id, o.prompt_name, o.prompt_version, "
            "       o.model, o.provider, o.latency_ms, "
            "       o.cost_eur::double precision AS cost_eur, o.created_at, "
            "       u.full_name AS author, h.full_name AS hcp_name, "
            "       p.name AS product_name, "
            "       (SELECT count(*) FROM agent_output_sources s "
            "         WHERE s.agent_output_id = o.id) AS source_count "
            "  FROM agent_outputs o "
            "  JOIN users u ON u.id = o.user_id "
            "  LEFT JOIN healthcare_professionals h ON h.id = o.hcp_id "
            "  LEFT JOIN products p ON p.id = o.product_id "
            " WHERE o.deleted_at IS NULL "
            "   AND (CAST(:kind AS text) IS NULL OR o.kind::text = :kind) "
            "   AND (:only_review = false OR o.requires_human_review = true) "
            " ORDER BY o.created_at DESC LIMIT :limit"
        ),
        {"kind": kind, "only_review": only_review, "limit": limit},
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get(
    "/outputs/{output_id}",
    dependencies=[Depends(require(BRIEFING_READ)), Depends(rate_limit())],
)
def get_output(
    output_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Detalle de una salida con las fuentes tal como estaban al citarse.

    `status_now` frente a `document_status_at_use` es la comparación que hace
    auditable una retirada: si difieren, esta salida citó material que después
    dejó de ser válido. Sin la copia congelada esa diferencia no se podría ni
    formular.
    """
    output = fetch_scoped_one(
        session,
        principal,
        table="agent_outputs",
        resource_id=output_id,
        columns=(
            "id, kind, user_id, hcp_id, product_id, payload, answer_text, "
            "confidence, risk, requires_human_review, blocked_reason, trace_id, "
            "prompt_name, prompt_version, model, provider, latency_ms, "
            "cost_eur::double precision AS cost_eur, "
            "input_tokens, output_tokens, created_at"
        ),
        extra_where="deleted_at IS NULL",
        resource_type="agent_output",
    )

    sources = session.execute(
        text(
            "SELECT s.document_id, s.quoted_excerpt, s.document_version, "
            "       s.document_status_at_use, s.relevance, d.title, "
            "       d.status AS status_now, "
            "       (d.status::text <> s.document_status_at_use::text) AS status_changed "
            "  FROM agent_output_sources s "
            "  JOIN documents d ON d.id = s.document_id "
            " WHERE s.agent_output_id = CAST(:id AS uuid)"
        ),
        {"id": output_id},
    ).mappings().all()

    return {**output, "sources": [dict(s) for s in sources]}


@router.get(
    "/outputs/{output_id}/trace",
    dependencies=[Depends(require(TRACE_READ)), Depends(rate_limit())],
)
def get_trace(
    output_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Los pasos que produjeron una salida, en orden.

    Es la respuesta a «¿por qué dijo esto?». Sin ella solo se puede mirar el
    resultado y especular: la recuperación no encontró la sección, o la
    encontró y el modelo no la citó, o la citó y el verificador la rechazó. Son
    tres fallos distintos con tres arreglos distintos y desde fuera se parecen.
    """
    output = fetch_scoped_one(
        session,
        principal,
        table="agent_outputs",
        resource_id=output_id,
        columns="id, trace_id, kind",
        extra_where="deleted_at IS NULL",
        resource_type="agent_output",
    )

    steps = session.execute(
        text(
            "SELECT step, step_type, name, status, input_summary, output_summary, "
            "       latency_ms, created_at "
            "  FROM agent_traces WHERE trace_id = :trace_id ORDER BY step"
        ),
        {"trace_id": output["trace_id"]},
    ).mappings().all()

    return {
        "output_id": output_id,
        "trace_id": output["trace_id"],
        "steps": [dict(s) for s in steps],
        "step_count": len(steps),
    }
