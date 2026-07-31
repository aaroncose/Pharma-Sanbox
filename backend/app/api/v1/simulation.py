"""Simulador conversacional.

Practicar una visita con un profesional sanitario ficticio, bajo las mismas
reglas que rigen en producción: lo que el comercial dice se evalúa con el motor
de políticas del agente, no con una versión relajada para el entrenamiento.

El simulador **no recibe la biblioteca documental**, y eso es una decisión de
producto, no un olvido. Un médico real no ha leído el material comercial
aprobado; si el simulador lo conociera, haría preguntas antinaturalmente
alineadas con las respuestas disponibles y el entrenamiento dejaría de
parecerse a una visita. El informe final sí la recibe, porque para decir qué
fuente debería haberse citado hay que saber cuál existía.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.agent.prompts import get_prompt_registry
from app.agent.provider import get_provider
from app.agent.runner import get_runner
from app.agent.schemas import SimulationFeedback, SimulatorTurn, json_schema_for
from app.api.deps import (
    CurrentPrincipal,
    TenantSession,
    agent_rate_limit,
    rate_limit,
    require,
)
from app.config import settings
from app.core.errors import ConflictError, ProviderUnavailableError
from app.core.permissions import SIMULATION_USE
from app.schemas.simulation import RepTurn, SimulationStart
from app.services import outputs, simulation
from app.services.access import fetch_scoped_one

router = APIRouter(prefix="/simulations", tags=["simulation"])

# Tope de turnos. Una simulación no es una conversación indefinida: pasado
# cierto punto deja de entrenar y solo consume presupuesto. El límite se declara
# aquí y se devuelve en la respuesta para que la interfaz pueda avisar antes de
# llegar, en lugar de cortar por sorpresa.
MAX_TURNS = 40


def _load_simulation(
    session: TenantSession, principal: CurrentPrincipal, simulation_id: str
) -> dict[str, Any]:
    return fetch_scoped_one(
        session,
        principal,
        table="simulations",
        resource_id=simulation_id,
        columns=(
            "id, user_id, hcp_id, product_id, scenario, objective, modality, "
            "started_at, ended_at, score, feedback"
        ),
        resource_type="simulation",
    )


def _next_ordinal(session: TenantSession, simulation_id: str) -> int:
    return int(
        session.execute(
            text(
                "SELECT COALESCE(max(ordinal), 0) + 1 FROM simulation_turns "
                " WHERE simulation_id = CAST(:id AS uuid)"
            ),
            {"id": simulation_id},
        ).scalar()
        or 1
    )


def _record_turn(
    session: TenantSession,
    *,
    tenant_id: str,
    simulation_id: str,
    ordinal: int,
    speaker: str,
    content: str,
    compliance_flag: str | None = None,
    started_ms: int | None = None,
    duration_ms: int | None = None,
    was_interrupted: bool = False,
) -> None:
    session.execute(
        text(
            "INSERT INTO simulation_turns "
            "  (tenant_id, simulation_id, ordinal, speaker, content, "
            "   compliance_flag, started_ms, duration_ms, was_interrupted) "
            "VALUES (CAST(:tenant_id AS uuid), CAST(:simulation_id AS uuid), "
            "        :ordinal, :speaker, :content, :flag, :started_ms, "
            "        :duration_ms, :was_interrupted)"
        ),
        {
            "tenant_id": tenant_id,
            "simulation_id": simulation_id,
            "ordinal": ordinal,
            "speaker": speaker,
            "content": content,
            "flag": compliance_flag,
            "started_ms": started_ms,
            "duration_ms": duration_ms,
            "was_interrupted": was_interrupted,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Ciclo de vida
# ─────────────────────────────────────────────────────────────────────────────


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require(SIMULATION_USE)), Depends(agent_rate_limit())],
)
def start_simulation(
    payload: SimulationStart,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Abre una simulación y deja hablar primero al profesional sanitario.

    Que abra el profesional y no el comercial es deliberado: en una visita real
    el comercial entra a un despacho donde ya hay una persona con prisa y una
    opinión formada. Empezar con la pantalla en blanco esperando al comercial
    entrena una situación que no ocurre.
    """
    hcp = fetch_scoped_one(
        session,
        principal,
        table="healthcare_professionals",
        resource_id=payload.hcp_id,
        columns="id, full_name, specialty, institution",
        extra_where="deleted_at IS NULL",
        resource_type="healthcare_professional",
    )
    product = fetch_scoped_one(
        session,
        principal,
        table="products",
        resource_id=payload.product_id,
        columns="id, name",
        extra_where="is_active = true",
        resource_type="product",
    )

    simulation_id = str(uuid.uuid4())
    session.execute(
        text(
            "INSERT INTO simulations "
            "  (id, tenant_id, user_id, hcp_id, product_id, scenario, objective, "
            "   modality) "
            "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), "
            "        CAST(:user_id AS uuid), CAST(:hcp_id AS uuid), "
            "        CAST(:product_id AS uuid), :scenario, :objective, :modality)"
        ),
        {
            "id": simulation_id,
            "tenant_id": principal.tenant_id,
            "user_id": principal.user_id,
            "hcp_id": payload.hcp_id,
            "product_id": payload.product_id,
            "scenario": payload.scenario,
            "objective": payload.objective,
            "modality": payload.modality,
        },
    )

    opening = _hcp_turn(
        session,
        hcp=hcp,
        product=product,
        scenario=payload.scenario,
        attitude=payload.attitude,
        modality=payload.modality,
        history="(la conversación acaba de empezar)",
        prompt_version=payload.prompt_version,
    )

    _record_turn(
        session,
        tenant_id=principal.tenant_id,
        simulation_id=simulation_id,
        ordinal=1,
        speaker="hcp",
        content=opening.utterance,
    )

    return {
        "id": simulation_id,
        "hcp": {k: hcp[k] for k in ("full_name", "specialty", "institution")},
        "product": product["name"],
        "objective": payload.objective,
        "modality": payload.modality,
        "max_turns": MAX_TURNS,
        "opening_turn": {
            "ordinal": 1,
            "speaker": "hcp",
            "content": opening.utterance,
            "intent": opening.intent,
        },
    }


def _hcp_turn(
    session: TenantSession,
    *,
    hcp: dict[str, Any],
    product: dict[str, Any],
    scenario: str,
    attitude: str,
    modality: str,
    history: str,
    prompt_version: str | None,
) -> SimulatorTurn:
    """Genera el turno del profesional sanitario.

    Llama al proveedor directamente y no a `AgentRunner`, y conviene justificar
    por qué es la excepción. El harness existe para el contenido que se entrega
    como información: recupera documentación, comprueba políticas sobre lo
    generado y verifica el respaldo. Aquí nada de eso aplica —el profesional
    ficticio no informa a nadie, interpreta un papel— y pasarlo por el harness
    haría dos daños: recuperar biblioteca para él rompe la asimetría que hace
    útil el entrenamiento, y evaluar sus frases con las políticas de producto
    bloquearía justo los turnos que debe hacer, porque preguntar algo que no se
    puede responder es su función.

    Lo que sí se controla con políticas es el turno **del comercial**, que es
    quien está aprendiendo.
    """
    prompt = get_prompt_registry().get(session, "simulator", prompt_version)
    rendered = prompt.render(
        hcp_name=hcp["full_name"],
        specialty=hcp["specialty"],
        institution=hcp["institution"],
        attitude=attitude,
        scenario=scenario,
        product_name=product["name"],
        modality=modality,
    )

    try:
        response = get_provider().complete(
            system=(
                "Interpretas un papel en una simulación de entrenamiento. "
                "Responde exclusivamente con el objeto JSON del esquema indicado."
            ),
            user=f"{rendered}\n\n<conversacion_hasta_ahora>\n{history}\n"
            "</conversacion_hasta_ahora>",
            model=settings.llm_primary_model,
            # Turnos cortos: nadie pronuncia un párrafo de cinco líneas en una
            # conversación real, y en voz la latencia de síntesis crece con la
            # longitud.
            max_tokens=800,
            effort="low",
            json_schema=json_schema_for(SimulatorTurn),
            thinking=False,
        )
    except ProviderUnavailableError:
        # La simulación no se pierde: el profesional "se queda pensando" y el
        # comercial puede seguir. Cortar la sesión entera por una caída
        # transitoria perdería la práctica hecha hasta ese punto.
        return SimulatorTurn(
            utterance="Disculpe, me distraje un momento. ¿Puede repetirlo?",
            intent="ask_evidence",
            internal_note="turno degradado: el proveedor no respondió",
        )

    return SimulatorTurn.model_validate(response.parsed or {})


@router.post(
    "/{simulation_id}/turns",
    dependencies=[Depends(require(SIMULATION_USE)), Depends(agent_rate_limit())],
)
def take_turn(
    simulation_id: str,
    payload: RepTurn,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """El comercial habla; el profesional responde.

    El turno del comercial se evalúa **antes** de que el profesional conteste, y
    la marca se guarda en ese momento. Así la interfaz puede enseñar el riesgo
    en vivo, que es cuando sirve para corregir: una infracción señalada tres
    turnos después ya se convirtió en costumbre.
    """
    sim = _load_simulation(session, principal, simulation_id)
    if sim["ended_at"] is not None:
        raise ConflictError(
            "La simulación ya ha terminado",
            details={"ended_at": sim["ended_at"].isoformat()},
        )

    ordinal = _next_ordinal(session, simulation_id)
    if ordinal > MAX_TURNS:
        raise ConflictError(
            f"Se ha alcanzado el máximo de {MAX_TURNS} turnos. Finaliza la "
            "simulación para ver el informe.",
            details={"max_turns": MAX_TURNS},
        )

    # ── El turno del comercial, bajo las políticas de producción ─────────────
    evaluation = simulation.evaluate_rep_turn(
        session, tenant_id=principal.tenant_id, utterance=payload.utterance
    )

    _record_turn(
        session,
        tenant_id=principal.tenant_id,
        simulation_id=simulation_id,
        ordinal=ordinal,
        speaker="rep",
        content=payload.utterance,
        compliance_flag=evaluation.flag,
        started_ms=payload.started_ms,
        duration_ms=payload.duration_ms,
        was_interrupted=payload.was_interrupted,
    )

    # ── La respuesta del profesional ─────────────────────────────────────────
    hcp = fetch_scoped_one(
        session,
        principal,
        table="healthcare_professionals",
        resource_id=str(sim["hcp_id"]),
        columns="id, full_name, specialty, institution",
        resource_type="healthcare_professional",
    )
    product = fetch_scoped_one(
        session,
        principal,
        table="products",
        resource_id=str(sim["product_id"]),
        columns="id, name",
        resource_type="product",
    )

    turns = simulation.load_turns(session, simulation_id)
    reply = _hcp_turn(
        session,
        hcp=hcp,
        product=product,
        scenario=sim["scenario"],
        attitude="escéptico",
        modality=sim["modality"],
        history=simulation.format_transcript(turns),
        prompt_version=None,
    )

    _record_turn(
        session,
        tenant_id=principal.tenant_id,
        simulation_id=simulation_id,
        ordinal=ordinal + 1,
        speaker="hcp",
        content=reply.utterance,
    )

    return {
        "rep_turn": {
            "ordinal": ordinal,
            # El riesgo en vivo. Es el dato del panel lateral, y por eso sale
            # aquí y no solo en el informe final.
            "compliance_flag": evaluation.flag,
            "risk_level": evaluation.risk_level,
            "policy_codes": evaluation.decision.codes,
            "hint": evaluation.decision.message,
        },
        "hcp_turn": {
            "ordinal": ordinal + 1,
            "content": reply.utterance,
            "intent": reply.intent,
            # Se dice a la interfaz que este turno es una pregunta fuera de
            # límites, pero NO se le dice al comercial: la nota interna del
            # simulador no se expone. Avisarle destruiría lo que se evalúa.
            "is_out_of_bounds": reply.is_out_of_bounds,
        },
        "turns_used": ordinal + 1,
        "max_turns": MAX_TURNS,
    }


@router.post(
    "/{simulation_id}/end",
    dependencies=[Depends(require(SIMULATION_USE)), Depends(agent_rate_limit())],
)
def end_simulation(
    simulation_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Cierra la simulación y produce el informe.

    Aquí el informe **sí** recibe la biblioteca documental. Es la otra mitad de
    la asimetría: el simulador no puede conocerla, porque haría preguntas
    alineadas con las respuestas disponibles; el informe tiene que conocerla,
    porque «qué fuente deberías haber citado» no se puede responder sin ella.
    """
    sim = _load_simulation(session, principal, simulation_id)
    if sim["ended_at"] is not None:
        raise ConflictError("La simulación ya está cerrada")

    turns = simulation.load_turns(session, simulation_id)
    hcp = fetch_scoped_one(
        session,
        principal,
        table="healthcare_professionals",
        resource_id=str(sim["hcp_id"]),
        columns="id, full_name, specialty",
        resource_type="healthcare_professional",
    )
    product = fetch_scoped_one(
        session,
        principal,
        table="products",
        resource_id=str(sim["product_id"]),
        columns="id, name",
        resource_type="product",
    )

    result = get_runner().run(
        session,
        task="simulation_debrief",
        model_cls=SimulationFeedback,
        # Se recupera documentación con lo que dijo el comercial: son sus
        # afirmaciones las que hay que poder respaldar o corregir.
        question=" ".join(
            t["content"] for t in turns if t["speaker"] == "rep"
        )[:1000]
        or sim["objective"],
        prompt_variables={
            "hcp_name": hcp["full_name"],
            "specialty": hcp["specialty"],
            "scenario": sim["scenario"],
            "objective": sim["objective"],
            "product_name": product["name"],
            "transcript": simulation.format_transcript(turns),
            "compliance_flags": simulation.format_flags(turns),
            # Se le dicen explícitamente los turnos que puede referenciar. Con
            # el prompt anterior señaló como mejorable un turno del médico.
            "rep_turns": ", ".join(str(o) for o in simulation.rep_ordinals(turns))
            or "(ninguno)",
        },
        tenant_id=principal.tenant_id,
        product_id=str(sim["product_id"]),
        trace_id=principal.trace_id,
    )

    # Segunda capa: aunque el prompt lo diga, se valida a la salida. Decírselo
    # al modelo reduce el fallo; no lo elimina.
    mejorables, ancladas_mal = simulation.anchor_improvable_answers(
        list(getattr(result.output, "improvable_answers", []) if result.output else []),
        turns,
    )

    handled_well = bool(
        result.output and getattr(result.output, "handled_out_of_bounds_well", False)
    )
    compliance = simulation.compliance_score(
        turns, handled_out_of_bounds_well=handled_well
    )
    communication = int(
        getattr(result.output, "communication_score", 0) if result.output else 0
    )
    total = simulation.overall_score(communication, compliance)

    ids = outputs.persist_result(
        session,
        principal,
        result,
        kind="simulation_feedback",
        answer_text=_feedback_text(result),
        hcp_id=str(sim["hcp_id"]),
        product_id=str(sim["product_id"]),
    )

    feedback = {
        "communication": {
            "score": communication,
            "summary": getattr(result.output, "communication_summary", "")
            if result.output
            else "",
            "strengths": getattr(result.output, "strengths", []) if result.output else [],
        },
        # El desglose viaja con la nota. Un número sin desglose es una opinión:
        # el comercial no puede comprobarlo ni saber qué corregir.
        "compliance": {
            "score": compliance.score,
            "flagged_turns": compliance.flagged_turns,
            "total_rep_turns": compliance.total_rep_turns,
            "penalties": compliance.penalties,
            "bonus_applied": compliance.bonus_applied,
        },
        "improvable_answers": [a.model_dump(mode="json") for a in mejorables],
        "handled_out_of_bounds_well": handled_well,
        "misanchored_feedback": ancladas_mal,
        "score_breakdown": (
            "0.4 * comunicacion + 0.6 * cumplimiento, con techo por gravedad"
        ),
        "score_cap": simulation.applicable_cap(compliance),
    }

    session.execute(
        text(
            "UPDATE simulations SET ended_at = now(), score = :score, "
            "       feedback = CAST(:feedback AS jsonb) "
            " WHERE id = CAST(:id AS uuid)"
        ),
        {
            "id": simulation_id,
            "score": total,
            "feedback": json.dumps(feedback, ensure_ascii=False),
        },
    )

    return {
        "id": simulation_id,
        "score": total,
        **feedback,
        "sources_you_could_have_used": [
            {
                "source_id": c.source_id,
                "title": c.document_title,
                "section": c.section,
                "excerpt": c.excerpt(240),
            }
            for c in result.chunks
        ],
        "meta": ids,
    }


def _feedback_text(result: Any) -> str:
    """Texto plano del informe, para la cola de revisión si hiciera falta."""
    if result.output is None:
        return ""
    partes = [result.output.communication_summary, *result.output.strengths]
    partes += [
        f"Turno {a.turn_ordinal}: {a.why} → {a.suggested_rewrite}"
        for a in result.output.improvable_answers
    ]
    return "\n".join(p for p in partes if p)


# ─────────────────────────────────────────────────────────────────────────────
# Consulta
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "",
    dependencies=[Depends(require(SIMULATION_USE)), Depends(rate_limit())],
)
def list_simulations(
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT s.id, s.scenario, s.objective, s.modality, s.started_at, "
            "       s.ended_at, s.score, h.full_name AS hcp_name, "
            "       p.name AS product_name, "
            "       (SELECT count(*) FROM simulation_turns t "
            "         WHERE t.simulation_id = s.id) AS turn_count, "
            "       (SELECT count(*) FROM simulation_turns t "
            "         WHERE t.simulation_id = s.id "
            "           AND t.compliance_flag IS NOT NULL) AS flagged_turns "
            "  FROM simulations s "
            "  JOIN healthcare_professionals h ON h.id = s.hcp_id "
            "  JOIN products p ON p.id = s.product_id "
            " ORDER BY s.started_at DESC LIMIT :limit"
        ),
        {"limit": limit},
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get(
    "/{simulation_id}",
    dependencies=[Depends(require(SIMULATION_USE)), Depends(rate_limit())],
)
def get_simulation(
    simulation_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """La transcripción completa, con las marcas donde ocurrieron.

    `internal_note` del simulador no se guarda ni se devuelve: era el motivo por
    el que el profesional preguntó algo, y enseñárselo al comercial convertiría
    el entrenamiento en un examen con las respuestas al final.
    """
    sim = _load_simulation(session, principal, simulation_id)
    turns = simulation.load_turns(session, simulation_id)

    return {
        **sim,
        "turns": [
            {
                "ordinal": t["ordinal"],
                "speaker": t["speaker"],
                "content": t["content"],
                "compliance_flag": t["compliance_flag"],
                "risk_level": t["risk_level"],
                "duration_ms": t["duration_ms"],
                "was_interrupted": t["was_interrupted"],
            }
            for t in turns
        ],
        "turn_count": len(turns),
    }
