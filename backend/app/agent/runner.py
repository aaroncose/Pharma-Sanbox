"""Orquestador del harness.

El agente no es el modelo. El agente es esta secuencia, y el modelo es un paso
dentro de ella:

    1. Política sobre la solicitud    ¿debe llegar siquiera al modelo?
    2. Construcción de contexto       solo tenant correcto, producto autorizado,
                                      documentación aprobada y vigente
    3. Política sobre el material     ¿hay instrucciones dentro de un documento?
    4. Suficiencia de evidencia       ¿hay material que respalde una respuesta?
    5. Llamada al modelo              salida restringida por esquema
    6. Validación y reparación        una vez, con el error como contexto
    7. Verificación adversarial       segundo modelo intentando refutar
    8. Política sobre la respuesta    sobre el texto generado, no sobre lo que
                                      el modelo dice de sí mismo
    9. Decisión final                 entregar, marcar para revisión o bloquear

Cada paso puede terminar la ejecución. Los pasos 1 y 4 lo hacen sin gastar una
llamada al modelo, que es donde está el ahorro, aunque no es el motivo: el
motivo es que una solicitud de recomendación clínica no debe llegar al modelo.

Sobre el orden de 7 y 8: el verificador se ejecuta antes que las políticas de
respuesta porque puede reducir la confianza declarada, y la política de fuentes
depende de esa confianza. Al revés se bloquearían respuestas que el verificador
habría degradado por sí solo.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.agent.prompts import get_prompt_registry
from app.agent.provider import LLMResponse, get_provider
from app.agent.schemas import VerifierOutput, json_schema_for
from app.agent.trace import AgentTrace, timed
from app.config import settings
from app.core.errors import ProviderUnavailableError
from app.core.logging import get_logger
from app.policies.engine import PolicyDecision, get_policy_engine
from app.services import retrieval
from app.services.retrieval import RetrievedChunk

log = get_logger("agent")

# Por debajo de este umbral de relevancia, el material recuperado no sostiene
# una respuesta. Es el valor de la política `INSUFFICIENT_EVIDENCE_MUST_ADMIT`;
# aquí solo está el respaldo por si la política no estuviera cargada.
DEFAULT_MIN_RELEVANCE = 0.15


@dataclass(slots=True)
class AgentResult:
    """Todo lo que produce una ejecución, listo para persistir y auditar."""

    output: BaseModel | None
    chunks: list[RetrievedChunk]
    trace: AgentTrace
    prompt_name: str
    prompt_version: str
    model: str
    provider: str
    latency_ms: int
    cost_eur: float
    input_tokens: int = 0
    output_tokens: int = 0
    blocked_reason: str | None = None
    requires_human_review: bool = False
    policy_codes: list[str] = field(default_factory=list)
    verifier: VerifierOutput | None = None
    degraded: bool = False

    @property
    def delivered(self) -> bool:
        return self.output is not None and self.blocked_reason is None


class AgentRunner:
    """Ejecuta una tarea del agente de principio a fin."""

    def __init__(self) -> None:
        self.policies = get_policy_engine()
        self.prompts = get_prompt_registry()

    # ── Paso 6: validación y reparación ──────────────────────────────────────

    def _validate_or_repair(
        self,
        *,
        response: LLMResponse,
        model_cls: type[BaseModel],
        system: str,
        user: str,
        schema: dict[str, Any],
        trace: AgentTrace,
    ) -> BaseModel | None:
        """Valida la salida y reintenta una vez si no valida.

        Un solo reintento, no un bucle. Si el modelo no produce una salida
        válida con el error delante, insistir no lo arregla: consume
        presupuesto y latencia para llegar al mismo sitio. A la segunda se
        degrada a un mensaje controlado.
        """
        try:
            return model_cls.model_validate(response.parsed or {})
        except ValidationError as first_error:
            trace.record(
                "repair",
                "schema_validation_failed",
                status="retrying",
                output_summary={"errors": first_error.error_count()},
            )

            repair_prompt = (
                f"{user}\n\n"
                "Tu respuesta anterior no cumplió el esquema requerido. "
                "Errores concretos:\n"
                f"{first_error.json(indent=2)}\n\n"
                "Devuelve exclusivamente el objeto JSON corregido."
            )

            with timed() as t:
                try:
                    retry = get_provider().complete(
                        system=system,
                        user=repair_prompt,
                        model=settings.llm_primary_model,
                        json_schema=schema,
                    )
                except ProviderUnavailableError:
                    trace.record("repair", "provider_unavailable", status="error")
                    return None

            try:
                repaired = model_cls.model_validate(retry.parsed or {})
            except ValidationError:
                trace.record(
                    "repair", "schema_validation_failed_twice", status="error",
                    latency_ms=t.ms,
                )
                return None

            trace.record("repair", "schema_repaired", status="ok", latency_ms=t.ms)
            return repaired

    # ── Paso 7: verificación adversarial ─────────────────────────────────────

    def _verify(
        self,
        session: Session,
        *,
        answer_text: str,
        chunks: list[RetrievedChunk],
        trace: AgentTrace,
    ) -> VerifierOutput | None:
        """Segundo paso con un modelo distinto, en modo refutar.

        El verificador recibe la respuesta y las fuentes, y **no** recibe la
        pregunta original ni el razonamiento del generador. Si los recibiera
        heredaría el mismo encuadre y confirmaría lo que el generador ya
        decidió, que es el modo de fallo clásico de la autoevaluación.
        """
        prompt = self.prompts.get(session, "verifier")
        schema = json_schema_for(VerifierOutput)

        rendered = prompt.render(
            answer=answer_text,
            sources=retrieval.format_for_prompt(chunks),
        )

        with timed() as t:
            try:
                response = get_provider().complete(
                    system="Eres un verificador adversarial de contenido farmacéutico.",
                    user=rendered,
                    model=settings.llm_verifier_model,
                    json_schema=schema,
                    # Haiku no admite `effort`; la capa de proveedor lo omite
                    # según la matriz de capacidades.
                    effort="medium",
                )
            except ProviderUnavailableError:
                # Sin verificador no se entrega como verificado: se marca para
                # revisión humana. Degradar a "sin verificar pero entregado"
                # convertiría una caída de infraestructura en una relajación
                # silenciosa del control.
                trace.record("verify", "provider_unavailable", status="error", latency_ms=t.ms)
                return None

        try:
            verdict = VerifierOutput.model_validate(response.parsed or {})
        except ValidationError:
            trace.record("verify", "invalid_verifier_output", status="error", latency_ms=t.ms)
            return None

        trace.record(
            "verify",
            "verifier_completed",
            status="ok",
            latency_ms=t.ms,
            output_summary={
                "verdict": verdict.verdict,
                "unsupported_claims": len(verdict.unsupported_claims),
                "requires_human_review": verdict.requires_human_review,
                "model": response.model,
            },
        )
        return verdict

    # ── Ejecución completa ───────────────────────────────────────────────────

    def run(
        self,
        session: Session,
        *,
        task: str,
        model_cls: type[BaseModel],
        question: str,
        prompt_variables: dict[str, Any],
        tenant_id: str,
        product_id: str | None = None,
        prompt_version: str | None = None,
        trace_id: str | None = None,
        retrieve: bool = True,
        verify: bool = True,
    ) -> AgentResult:
        trace_id = trace_id or f"tr_{uuid.uuid4().hex[:12]}"
        trace = AgentTrace(trace_id, tenant_id)
        prompt = self.prompts.get(session, task, prompt_version)
        schema = json_schema_for(model_cls)

        def result(**overrides: Any) -> AgentResult:
            base = {
                "output": None,
                "chunks": [],
                "trace": trace,
                "prompt_name": prompt.name,
                "prompt_version": prompt.version,
                "model": settings.llm_primary_model,
                "provider": get_provider().name,
                "latency_ms": trace.total_latency_ms,
                "cost_eur": 0.0,
            }
            return AgentResult(**{**base, **overrides})

        # ── 1. Política sobre la solicitud ───────────────────────────────────
        with timed() as t:
            request_decision = self.policies.evaluate_request(
                session, tenant_id=tenant_id, question=question
            )
        trace.record(
            "policy_check", "request", status=request_decision.action, latency_ms=t.ms,
            output_summary={"codes": request_decision.codes},
        )

        if request_decision.blocked:
            return result(
                blocked_reason=request_decision.hits[0].code,
                requires_human_review=False,
                policy_codes=request_decision.codes,
            )

        # ── 2. Construcción de contexto ──────────────────────────────────────
        chunks: list[RetrievedChunk] = []
        if retrieve:
            with timed() as t:
                chunks = retrieval.search(
                    session, query=question, product_id=product_id, limit=8
                )
            relevance = retrieval.relevance_of(chunks)
            trace.record(
                "retrieval", "hybrid_search", latency_ms=t.ms,
                output_summary={
                    "chunks": len(chunks),
                    "relevance": relevance,
                    "documents": sorted({c.document_id for c in chunks}),
                },
            )
        else:
            relevance = 1.0

        documents = retrieval.format_for_prompt(chunks)

        # ── 3. Política sobre el material recuperado ─────────────────────────
        injection = self.policies.evaluate_documents(
            session, tenant_id=tenant_id, documents=documents
        )
        if injection.hits:
            # No se corta la ejecución: el contenido es material legítimo de la
            # biblioteca y la defensa real es la separación estructural. Lo que
            # se hace es dejar constancia.
            trace.record(
                "policy_check", "documents", status=injection.action,
                output_summary={"codes": injection.codes,
                                "evidence": injection.hits[0].evidence},
            )

        # ── 4. Suficiencia de evidencia ──────────────────────────────────────
        if retrieve and relevance < DEFAULT_MIN_RELEVANCE:
            trace.record(
                "policy_check", "insufficient_evidence", status="block",
                output_summary={"relevance": relevance},
            )
            return result(
                chunks=chunks,
                blocked_reason="INSUFFICIENT_SOURCES",
                requires_human_review=False,
                policy_codes=["INSUFFICIENT_EVIDENCE_MUST_ADMIT"],
            )

        # ── 5. Llamada al modelo ─────────────────────────────────────────────
        rendered = prompt.render(documents=documents, **prompt_variables)
        system = (
            "Operas dentro de un sistema con restricciones de cumplimiento. "
            "Responde exclusivamente con el objeto JSON del esquema indicado."
        )

        with timed() as t:
            try:
                response = get_provider().complete(
                    system=system,
                    user=rendered,
                    model=settings.llm_primary_model,
                    json_schema=schema,
                )
            except ProviderUnavailableError:
                trace.record(
                    "llm_call", "provider_unavailable",
                    status="error", latency_ms=t.ms,
                )
                # La operación no se pierde: se devuelve un resultado degradado
                # explícito, que la capa de servicio persiste para poder
                # reintentar. Es el resultado esperado de la prueba 5.
                return result(
                    chunks=chunks,
                    blocked_reason="LLM_PROVIDER_UNAVAILABLE",
                    requires_human_review=True,
                    degraded=True,
                    latency_ms=t.ms,
                )

        trace.record(
            "llm_call", "generate", latency_ms=response.latency_ms,
            input_summary={"prompt": prompt.ref, "chunks": len(chunks)},
            output_summary={
                "model": response.model,
                "stop_reason": response.stop_reason,
                "output_tokens": response.usage.output_tokens,
                "cost_eur": response.cost_eur,
            },
        )

        # ── 6. Validación y reparación ───────────────────────────────────────
        output = self._validate_or_repair(
            response=response, model_cls=model_cls, system=system,
            user=rendered, schema=schema, trace=trace,
        )
        if output is None:
            return result(
                chunks=chunks,
                blocked_reason="STRUCTURED_OUTPUT_INVALID",
                requires_human_review=True,
                policy_codes=["STRUCTURED_OUTPUT_REQUIRED"],
                cost_eur=response.cost_eur,
                latency_ms=response.latency_ms,
            )

        answer_text = _extract_answer_text(output)

        # ── 7. Verificación adversarial ──────────────────────────────────────
        verdict: VerifierOutput | None = None
        if verify and answer_text.strip():
            verdict = self._verify(
                session, answer_text=answer_text, chunks=chunks, trace=trace
            )
            if verdict is None:
                # Sin verificación no se entrega como verificado.
                _force_review(output)
            else:
                _apply_verdict(output, verdict)

        # ── 8. Política sobre la respuesta generada ──────────────────────────
        response_decision = self.policies.evaluate_response(
            session,
            tenant_id=tenant_id,
            answer=answer_text,
            source_count=len(getattr(output, "sources", []) or []),
        )
        trace.record(
            "policy_check", "response", status=response_decision.action,
            output_summary={"codes": response_decision.codes},
        )

        # ── 9. Decisión final ────────────────────────────────────────────────
        combined_codes = [
            *request_decision.codes, *injection.codes, *response_decision.codes
        ]

        if response_decision.blocked:
            return result(
                chunks=chunks,
                blocked_reason=response_decision.hits[0].code,
                requires_human_review=True,
                policy_codes=combined_codes,
                cost_eur=response.cost_eur,
                latency_ms=response.latency_ms,
                verifier=verdict,
            )

        requires_review = (
            response_decision.requires_review
            or bool(injection.hits)
            or getattr(output, "requires_human_review", False)
        )
        if requires_review:
            _force_review(output)

        return result(
            output=output,
            chunks=chunks,
            requires_human_review=requires_review,
            policy_codes=combined_codes,
            cost_eur=response.cost_eur,
            latency_ms=response.latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            verifier=verdict,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Utilidades sobre la salida
# ─────────────────────────────────────────────────────────────────────────────


# Campos de texto plano y campos-lista-de-objetos que contienen afirmaciones.
# Se enumeran explícitamente y no por introspección: un campo nuevo que deba
# verificarse tiene que añadirse aquí a conciencia. La alternativa —recorrer
# todo lo que sea texto— metería en el verificador cosas como `blocked_reason` o
# los `concern`, que son juicios del propio sistema y no afirmaciones a
# comprobar.
_TEXT_FIELDS = ("answer", "hcp_summary", "summary")
_STATEMENT_FIELDS = (
    "permitted_information",
    "recommended_topics",
    "likely_questions",
    # Del resumen de visita: los compromisos del comercial son exactamente las
    # afirmaciones que hay que contrastar. Faltaban, y como el resumen no tiene
    # campo `answer`, `_extract_answer_text` devolvía cadena vacía y el paso de
    # verificación se saltaba entero sin que nada lo señalara.
    "rep_commitments",
)
_STATEMENT_KEYS = ("statement", "topic", "rationale", "suggested_answer")


def _extract_answer_text(output: BaseModel) -> str:
    """Texto que el verificador y las políticas deben examinar.

    Se concatenan todos los campos textuales relevantes de la salida, no solo
    `answer`. En un briefing la afirmación arriesgada suele estar en
    `permitted_information` o en una respuesta sugerida, no en el resumen.

    Devolver cadena vacía tiene una consecuencia fuerte: el verificador no se
    ejecuta. Por eso una salida cuyo texto no se sepa extraer no es un caso
    neutro, es un control que se apaga solo.
    """
    parts: list[str] = []
    data = output.model_dump()

    for field_name in _TEXT_FIELDS:
        if value := data.get(field_name):
            parts.append(str(value))

    for field_name in _STATEMENT_FIELDS:
        for item in data.get(field_name) or []:
            if isinstance(item, dict):
                parts.extend(
                    str(item[k]) for k in _STATEMENT_KEYS if item.get(k)
                )

    return "\n".join(parts)


def _force_review(output: BaseModel) -> None:
    if hasattr(output, "requires_human_review"):
        object.__setattr__(output, "requires_human_review", True)


def _apply_verdict(output: BaseModel, verdict: VerifierOutput) -> None:
    """Aplica el juicio del verificador sobre la salida del generador.

    La confianza solo puede bajar. Un verificador que pudiera subirla sería un
    segundo generador de optimismo, no un control.
    """
    if verdict.confidence_adjustment and hasattr(output, "confidence"):
        adjusted = max(0, output.confidence + verdict.confidence_adjustment)
        object.__setattr__(output, "confidence", adjusted)

    if verdict.requires_human_review or verdict.verdict != "supported":
        _force_review(output)

    if verdict.verdict == "unsupported" and hasattr(output, "blocked_reason"):
        object.__setattr__(output, "blocked_reason", "UNSUPPORTED_BY_SOURCES")
        object.__setattr__(output, "confidence", 0)


_runner: AgentRunner | None = None


def get_runner() -> AgentRunner:
    global _runner
    if _runner is None:
        _runner = AgentRunner()
    return _runner


__all__ = ["AgentResult", "AgentRunner", "PolicyDecision", "get_runner"]
