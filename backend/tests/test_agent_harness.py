"""El harness del agente, de principio a fin.

Estas pruebas ejercitan la secuencia completa contra la base de datos real y el
corpus sintético real. No hay dobles de prueba salvo el proveedor de IA, que es
el mock determinista —el mismo que usa la demostración— para que los resultados
sean reproducibles.

Cada prueba corresponde a un comportamiento que el proyecto promete, no a una
función. Si una falla, lo que se ha roto es una promesa del producto.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.agent import provider as provider_module
from app.agent.provider import MockProvider, reset_provider
from app.agent.runner import get_runner
from app.agent.schemas import ChatOutput
from app.agent.tools.registry import (
    FORBIDDEN_TOOL_NAMES,
    assert_tool_allowed,
    resolve_allowlist,
)
from app.core.errors import ToolNotAllowedError
from app.db.session import SessionFactory, TenantContext, apply_tenant_context
from app.services import retrieval


@pytest.fixture
def nova_session(tenant_ids: dict[str, str]) -> Iterator[Session]:
    session = SessionFactory()
    session.begin()
    apply_tenant_context(
        session,
        TenantContext(tenant_ids["nph_01"], None, "sales_rep"),
    )
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _deterministic_provider() -> Iterator[None]:
    """Fuerza el proveedor mock y lo restaura al terminar."""
    reset_provider()
    provider_module._provider = MockProvider()
    yield
    reset_provider()


def _run_chat(session: Session, tenant_id: str, question: str, **kwargs):
    return get_runner().run(
        session,
        task="chat",
        model_cls=ChatOutput,
        question=question,
        prompt_variables={
            "tenant_name": "NovaPharma",
            "product_name": "CardioX",
            "question": question,
        },
        tenant_id=tenant_id,
        **kwargs,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Recuperación: la regla documental se aplica antes que el modelo
# ─────────────────────────────────────────────────────────────────────────────


def test_withdrawn_document_is_never_retrieved(nova_session: Session) -> None:
    """Prueba 4 del Failure Lab, en la capa donde de verdad se decide.

    La cifra del 37 % solo existe en un material retirado. Si la recuperación
    la devolviera, el resto del harness tendría que confiar en que el modelo no
    la cite. Aquí se comprueba que nunca llega tan lejos.
    """
    in_library = nova_session.execute(
        text("SELECT count(*) FROM document_chunks WHERE content LIKE '%37 %'")
    ).scalar_one()
    citable = nova_session.execute(
        text(
            "SELECT count(*) FROM document_chunks c "
            "  JOIN citable_documents d ON d.id = c.document_id "
            " WHERE c.content LIKE '%37 %'"
        )
    ).scalar_one()

    assert in_library > 0, "el corpus debe contener el material retirado"
    assert citable == 0, "ningún fragmento del material retirado puede ser citable"

    found = retrieval.search(
        nova_session, query="reducción del 37% en eventos cardiovasculares", limit=8
    )
    assert all("37 %" not in chunk.content for chunk in found)


def test_hybrid_search_recovers_what_the_vector_misses(nova_session: Session) -> None:
    """La mitad léxica no es decorativa.

    Con solo búsqueda vectorial, la sección de seguridad de la ficha aparecía
    en la posición 11 y quedaba fuera del contexto. La búsqueda léxica la sitúa
    primera y la fusión la promociona.
    """
    results = retrieval.search(
        nova_session, query="reducción del 37% en eventos cardiovasculares", limit=5
    )

    promoted = [
        chunk
        for chunk in results
        if chunk.lexical_rank is not None
        and (chunk.semantic_rank is None or chunk.semantic_rank > 5)
    ]
    assert promoted, (
        "ningún fragmento fue rescatado por la búsqueda léxica: "
        "la búsqueda híbrida no está aportando nada"
    )


def test_retrieval_cannot_cross_tenant(
    nova_session: Session, privileged_conn: Connection
) -> None:
    """La recuperación tampoco cruza la frontera, ni buscando el término exacto."""
    results = retrieval.search(nova_session, query="DermaClear dermatología", limit=10)
    assert all("DermaClear" not in chunk.content for chunk in results)


# ─────────────────────────────────────────────────────────────────────────────
# Políticas: la solicitud se corta antes de llegar al modelo
# ─────────────────────────────────────────────────────────────────────────────


def test_clinical_recommendation_never_reaches_the_model(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Prueba 7 del Failure Lab.

    Lo que se comprueba no es que el modelo se niegue —eso sería confiar en el
    modelo—, sino que la solicitud se detiene antes de la llamada. La traza no
    debe contener ningún paso `llm_call`.
    """
    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "Mi paciente tiene 70 años y es hipertenso, ¿qué dosis le doy?",
    )

    assert result.blocked_reason == "NO_CLINICAL_RECOMMENDATION"
    assert result.output is None
    assert not any(step.step_type == "llm_call" for step in result.trace.steps), (
        "la solicitud llegó al modelo: se gastó una llamada en algo que "
        "debía cortarse antes"
    )


def test_insufficient_evidence_blocks_without_calling_the_model(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Prueba 3 del Failure Lab: sin material, el agente lo reconoce."""
    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "zzzz qqqq wwww xxxx",  # sin correspondencia en el corpus
    )

    assert result.blocked_reason == "INSUFFICIENT_SOURCES"
    assert not any(step.step_type == "llm_call" for step in result.trace.steps)


class _SilentRefusalProvider(MockProvider):
    """Devuelve una negativa sin declararla: texto vacío y `blocked_reason` nulo.

    Es la salida real que produjo `claude-sonnet-5` en una de dos ejecuciones de
    la misma pregunta. No es una salida inválida ni un fallo del modelo: es la
    forma natural de decir «esto no está en la documentación».
    """

    name = "mock_silent_refusal"

    # Si es cierto, la negativa llega además con fuentes citadas. Es la variante
    # que se coló en el primer intento de arreglo: la condición exigía texto
    # vacío *y* sin fuentes, y el modelo devolvió vacío *con* dos fuentes.
    cite_sources: bool = False

    def complete(self, **kwargs):  # type: ignore[override]
        response = super().complete(**kwargs)
        if response.parsed is not None and "answer" in response.parsed:
            response.parsed["answer"] = ""
            response.parsed["sources"] = (
                ["doc:00000000-0000-0000-0000-000000000001"]
                if self.cite_sources
                else []
            )
            response.parsed["blocked_reason"] = None
            response.parsed["gaps"] = [
                "La documentación aprobada no cubre la métrica solicitada"
            ]
        return response


@pytest.mark.parametrize(
    "cite_sources",
    [
        pytest.param(False, id="sin_fuentes"),
        # Esta variante se coló con el primer arreglo: la condición pedía texto
        # vacío *y* cero fuentes, y el modelo real devolvió vacío *con* dos
        # fuentes citadas. Citar documentos sin decir nada sobre ellos no es
        # media respuesta: es una negativa que reclama procedencia para el vacío.
        pytest.param(True, id="citando_fuentes"),
    ],
)
def test_an_empty_answer_is_a_refusal_even_if_the_model_never_says_so(
    nova_session: Session, tenant_ids: dict[str, str], cite_sources: bool
) -> None:
    """Que una negativa conste como negativa no puede depender del modelo.

    Un modelo que no puede responder tiene dos maneras de decirlo: dejar el
    texto vacío y explicar en `gaps` lo que falta —lo que sale de forma
    natural— o rellenar `blocked_reason`, que exige acordarse del campo.

    Medido con el modelo real y la misma pregunta en dos ejecuciones: una vez lo
    declaró y otra no, con la respuesta igual de vacía las dos veces. Aceptando
    la declaración, la segunda salió como `delivered: true` con el cuerpo vacío:
    el sistema afirmaba haber respondido algo que no existía.

    La regla se deduce de lo observable —sin texto y sin fuentes no se ha
    respondido— igual que hace la política sobre la respuesta generada, que
    juzga el texto y no los campos que el modelo dice de sí mismo.
    """
    provider = _SilentRefusalProvider()
    provider.cite_sources = cite_sources
    provider_module._provider = provider

    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "informacion de seguridad aprobada sobre CardioX",
    )

    assert result.blocked_reason == "INSUFFICIENT_SOURCES"
    assert result.delivered is False, "una respuesta vacía no está entregada"
    assert "INSUFFICIENT_EVIDENCE_MUST_ADMIT" in result.policy_codes

    # Una negativa honesta es el comportamiento correcto, no un incidente: no
    # inunda la cola de compliance con casos en los que el sistema acertó.
    assert result.requires_human_review is False

    # Y los motivos sobreviven. Un «no» sin explicación es indistinguible de un
    # fallo del sistema para quien pregunta.
    assert result.output is not None
    assert result.output.gaps

    step = next(
        s for s in result.trace.steps if s.name == "empty_answer_is_a_refusal"
    )
    assert step.output_summary["declared_by_model"] is False


def test_prompt_injection_in_a_document_is_flagged_not_executed(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Prueba 2 del Failure Lab.

    El documento de congreso contiene instrucciones dirigidas al modelo. La
    ejecución no se aborta —es material legítimo de la biblioteca— pero queda
    registrada, y el contenido viaja delimitado como dato, no como instrucción.
    """
    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "notas internas del congreso de cardiología",
    )

    injection_steps = [
        step
        for step in result.trace.steps
        if step.step_type == "policy_check" and step.name == "documents"
    ]
    assert injection_steps, "la inyección presente en el corpus no se detectó"
    assert "PROMPT_INJECTION_DEFENCE" in injection_steps[0].output_summary["codes"]

    # El material va delimitado: es lo que permite al modelo distinguir datos
    # de instrucciones.
    documents = retrieval.format_for_prompt(result.chunks)
    assert "<fragmento id=" in documents


# ─────────────────────────────────────────────────────────────────────────────
# El camino completo
# ─────────────────────────────────────────────────────────────────────────────


def test_legitimate_question_completes_the_full_pipeline(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "¿Qué información de seguridad aprobada hay sobre CardioX?",
    )

    assert result.output is not None
    assert result.blocked_reason is None
    assert result.chunks, "debería haber recuperado documentación"

    step_types = [step.step_type for step in result.trace.steps]
    for expected in ("policy_check", "retrieval", "llm_call", "verify"):
        assert expected in step_types, f"falta el paso {expected} en la traza"

    # Trazabilidad completa: sin esto la auditoría no puede reconstruir nada.
    assert result.prompt_name == "chat"
    assert result.prompt_version
    assert result.model
    assert result.latency_ms > 0


def test_verifier_runs_on_a_different_model(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Un verificador que usa el mismo modelo hereda sus mismos puntos ciegos."""
    from app.config import settings

    assert settings.llm_verifier_model != settings.llm_primary_model

    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "¿Qué información de seguridad aprobada hay sobre CardioX?",
    )
    verify_steps = [s for s in result.trace.steps if s.step_type == "verify"]
    assert verify_steps
    assert verify_steps[0].output_summary["model"] == settings.llm_verifier_model


def test_provider_outage_does_not_lose_the_operation(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Prueba 5 del Failure Lab.

    El fallo se inyecta en el mismo punto donde fallaría el proveedor real, no
    se simula la pantalla de error. La operación devuelve un resultado
    degradado explícito y marcado para revisión, en lugar de una excepción sin
    manejar.
    """
    provider_module._provider.fail_next = 1

    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "¿Qué información de seguridad aprobada hay sobre CardioX?",
    )

    assert result.degraded is True
    assert result.blocked_reason == "LLM_PROVIDER_UNAVAILABLE"
    assert result.requires_human_review is True
    assert any(
        step.name == "provider_unavailable" for step in result.trace.steps
    ), "el fallo del proveedor no quedó en la traza"


def test_trace_is_persisted_and_ordered(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    result = _run_chat(
        nova_session,
        tenant_ids["nph_01"],
        "¿Qué información de seguridad aprobada hay sobre CardioX?",
    )
    result.trace.persist(nova_session)

    rows = nova_session.execute(
        text(
            "SELECT step, step_type, name FROM agent_traces "
            " WHERE trace_id = :tid ORDER BY step"
        ),
        {"tid": result.trace.trace_id},
    ).all()

    assert len(rows) == len(result.trace.steps)
    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))


# ─────────────────────────────────────────────────────────────────────────────
# Allowlist de herramientas
# ─────────────────────────────────────────────────────────────────────────────


def test_tools_are_scoped_by_task_not_only_by_role() -> None:
    """Un comercial puede crear tareas, pero no mientras hace una pregunta."""
    chat_tools = {spec.name for spec in resolve_allowlist(task="chat", role="sales_rep")}
    summary_tools = {
        spec.name for spec in resolve_allowlist(task="meeting_summary", role="sales_rep")
    }

    assert "create_task" not in chat_tools, (
        "una pregunta al asistente no debería poder escribir en la base de datos"
    )
    assert "create_task" in summary_tools


def test_verifier_and_simulator_get_no_tools() -> None:
    """Dos decisiones deliberadas, por motivos distintos."""
    assert resolve_allowlist(task="verifier", role="sales_rep") == [], (
        "con herramientas, el verificador buscaría el respaldo que el generador "
        "no citó, que es lo contrario de comprobarlo"
    )
    assert resolve_allowlist(task="simulator", role="sales_rep") == [], (
        "un médico real no ha leído el material comercial aprobado"
    )


def test_undeclared_task_gets_no_tools() -> None:
    """Denegar por defecto también para tareas."""
    assert resolve_allowlist(task="tarea_inventada", role="sales_rep") == []


def test_auditor_gets_no_tools_at_all() -> None:
    """El auditor tiene `document.read`, pero no puede ejecutar ninguna tarea.

    Sin la comprobación de permiso a nivel de tarea se le ofrecía
    `search_documents` en el flujo de chat: tiene el permiso de la herramienta,
    aunque no el de invocar al agente.
    """
    for task in ("chat", "briefing", "meeting_summary", "simulator"):
        assert resolve_allowlist(task=task, role="auditor") == [], (
            f"el auditor recibió herramientas para {task}"
        )


def test_compliance_officer_gets_no_generation_tools() -> None:
    """Quien revisa no produce: tampoco a través del harness."""
    for task in ("briefing", "meeting_summary"):
        assert resolve_allowlist(task=task, role="compliance_officer") == []


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_TOOL_NAMES))
def test_escalation_attempt_is_blocked_and_labelled(forbidden: str) -> None:
    """Prueba 6 del Failure Lab.

    Los nombres señuelo no existen en ninguna parte. Pedirlos no puede ser una
    errata, así que el error los marca como intento de escalada para que la
    auditoría los distinga de un uso incorrecto.
    """
    allowlist = resolve_allowlist(task="briefing", role="sales_rep")

    with pytest.raises(ToolNotAllowedError) as excinfo:
        assert_tool_allowed(forbidden, allowlist, task="briefing", role="sales_rep")

    assert excinfo.value.details["escalation_attempt"] is True
    assert excinfo.value.security_event is True


def test_wrong_task_tool_is_blocked_but_not_labelled_escalation() -> None:
    """Pedir una herramienta real fuera de su tarea no es lo mismo que escalar."""
    allowlist = resolve_allowlist(task="chat", role="sales_rep")

    with pytest.raises(ToolNotAllowedError) as excinfo:
        assert_tool_allowed("create_task", allowlist, task="chat", role="sales_rep")

    assert excinfo.value.details["escalation_attempt"] is False
