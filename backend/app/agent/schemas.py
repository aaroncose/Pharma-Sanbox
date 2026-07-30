"""Esquemas de salida del agente.

Toda salida del agente valida contra uno de estos esquemas antes de mostrarse.
La garantía tiene tres capas, y las tres hacen falta:

1. **El API restringe la generación.** `output_config.format` con un esquema
   JSON impide que el modelo produzca algo que no valide. Es la capa más
   fuerte, y no está disponible en todos los modelos ni proveedores.
2. **La aplicación valida.** Pydantic vuelve a comprobar lo recibido. Protege
   del caso en que la primera capa no exista —proveedor mock, modelo antiguo,
   cambio de proveedor— y del que valide sintácticamente pero incumpla una
   regla de negocio, como declarar confianza alta sin citar ninguna fuente.
3. **El harness repara una vez.** Si la validación falla, se reintenta pasando
   el error como contexto. Si vuelve a fallar, se degrada a un mensaje
   controlado. El usuario nunca ve JSON roto ni texto libre sin verificar.

Sobre las restricciones del esquema: la salida estructurada del API no admite
`minLength`, `maximum` ni restricciones numéricas. Los límites de ese tipo se
expresan en Pydantic (capa 2) y se omiten del esquema que viaja al modelo. Por
eso los esquemas se generan con una función explícita en lugar de volcar
`model_json_schema()` directamente.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

RiskLevel = Literal["low", "medium", "high", "critical"]


# ─────────────────────────────────────────────────────────────────────────────
# Piezas comunes
# ─────────────────────────────────────────────────────────────────────────────


class SourceRef(BaseModel):
    """Referencia a un documento citado."""

    source_id: str = Field(description="Identificador en formato doc:UUID")
    quote: str | None = Field(
        default=None, description="Fragmento literal utilizado, si aplica"
    )


class SupportedStatement(BaseModel):
    """Afirmación junto con las fuentes que la respaldan.

    Que la unidad mínima sea `(afirmación, fuentes)` y no un texto con una
    lista de fuentes al final es lo que permite que el verificador compruebe el
    respaldo afirmación por afirmación. Con una lista global solo se puede
    comprobar que se citó *algo*.
    """

    statement: str
    source_ids: list[str] = Field(default_factory=list)


class RiskNote(BaseModel):
    risk: str
    mitigation: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Envoltura común de toda salida
# ─────────────────────────────────────────────────────────────────────────────


class AgentEnvelope(BaseModel):
    """Campos que acompañan a cualquier salida del agente."""

    sources: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
    risk_level: RiskLevel = "low"
    requires_human_review: bool = False
    blocked_reason: str | None = None
    gaps: list[str] = Field(
        default_factory=list,
        description="Qué no cubre la documentación disponible",
    )

    @model_validator(mode="after")
    def _coherence(self) -> AgentEnvelope:
        """Reglas de negocio que el esquema JSON no puede expresar.

        Son exactamente los estados incoherentes que un modelo produce cuando
        se le pide que rellene una plantilla: bloquear algo y declarar
        confianza alta, o afirmar con confianza sin citar nada.
        """
        if self.blocked_reason and self.confidence > 0:
            # Una respuesta bloqueada con confianza declarada es una
            # contradicción que la interfaz mostraría como respuesta válida.
            object.__setattr__(self, "confidence", 0)

        if not self.sources and self.confidence > 40:
            # Sin fuentes no se sostiene una confianza alta. En lugar de
            # rechazar la salida, se corrige a la baja y se marca para
            # revisión: la información sigue siendo útil, pero no como
            # afirmación respaldada.
            object.__setattr__(self, "confidence", min(self.confidence, 40))
            object.__setattr__(self, "requires_human_review", True)

        if self.risk_level in ("high", "critical"):
            object.__setattr__(self, "requires_human_review", True)

        return self


# ─────────────────────────────────────────────────────────────────────────────
# Briefing
# ─────────────────────────────────────────────────────────────────────────────


class BriefingTopic(BaseModel):
    topic: str
    rationale: str = ""
    source_ids: list[str] = Field(default_factory=list)


class BriefingQuestion(BaseModel):
    question: str
    suggested_answer: str = ""
    source_ids: list[str] = Field(default_factory=list)


class BriefingOutput(AgentEnvelope):
    hcp_summary: str = ""
    history_highlights: list[str] = Field(default_factory=list)
    recommended_topics: list[BriefingTopic] = Field(default_factory=list)
    likely_questions: list[BriefingQuestion] = Field(default_factory=list)
    permitted_information: list[SupportedStatement] = Field(default_factory=list)
    risks: list[RiskNote] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Asistente documental
# ─────────────────────────────────────────────────────────────────────────────


class ChatOutput(AgentEnvelope):
    answer: str = ""
    used_excerpts: list[SourceRef] = Field(default_factory=list)
    flags: list[str] = Field(
        default_factory=list,
        description="Anomalías detectadas, p. ej. instrucciones dentro de un documento",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Verificador
# ─────────────────────────────────────────────────────────────────────────────


class UnsupportedClaim(BaseModel):
    claim: str
    why: str = ""
    severity: RiskLevel = "medium"


class VerifierOutput(BaseModel):
    """Salida del segundo paso. No hereda de `AgentEnvelope`.

    El verificador no produce contenido para el usuario, así que no tiene
    fuentes ni confianza propias: lo que devuelve es un juicio sobre la salida
    del generador.
    """

    unsupported_claims: list[UnsupportedClaim] = Field(default_factory=list)
    missing_citations: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    policy_concerns: list[str] = Field(default_factory=list)
    verdict: Literal["supported", "partially_supported", "unsupported"] = "supported"
    requires_human_review: bool = False
    confidence_adjustment: int = Field(default=0, le=0, ge=-100)

    @model_validator(mode="after")
    def _verdict_consistency(self) -> VerifierOutput:
        """Un veredicto no puede ser más benévolo que los hallazgos.

        Cubre el fallo típico del verificador: enumerar cuatro afirmaciones sin
        respaldo y cerrar con `verdict: supported`.
        """
        has_findings = bool(
            self.unsupported_claims or self.contradictions or self.policy_concerns
        )
        if has_findings and self.verdict == "supported":
            object.__setattr__(self, "verdict", "partially_supported")

        critical = any(
            c.severity in ("high", "critical") for c in self.unsupported_claims
        )
        if critical or self.contradictions or self.policy_concerns:
            object.__setattr__(self, "requires_human_review", True)

        return self


# ─────────────────────────────────────────────────────────────────────────────
# Simulador
# ─────────────────────────────────────────────────────────────────────────────


class SimulatorTurn(BaseModel):
    utterance: str
    intent: Literal[
        "ask_evidence", "challenge", "change_topic", "out_of_bounds_question", "close"
    ] = "ask_evidence"
    is_out_of_bounds: bool = False
    internal_note: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Esquemas JSON para el API
# ─────────────────────────────────────────────────────────────────────────────


def _strip_unsupported(node: Any) -> Any:
    """Elimina del esquema las palabras clave que la salida estructurada no admite.

    `minimum`, `maximum`, `minLength`, `maxLength` y las restricciones de array
    no están soportadas y provocan un error. Se mantienen en Pydantic, que es
    donde de verdad se comprueban.
    """
    unsupported = {
        "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf",
        "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems",
        "default", "title", "examples",
    }
    if isinstance(node, dict):
        # Antes de descartar los límites numéricos, se pliegan en la
        # descripción. `description` sí está soportada, y sin esto el modelo no
        # tiene forma de saber que `confidence_adjustment` debe ser negativo:
        # la restricción viviría solo en Pydantic y el modelo la incumpliría en
        # cada llamada, forzando el reintento de reparación siempre.
        low, high = node.get("minimum"), node.get("maximum")
        if low is not None or high is not None:
            # Formato deliberadamente ASCII: el signo menos Unicode se parece
            # al guion pero no lo es, y quien lea el rango al otro lado —modelo
            # o proveedor simulado— lo parsearía mal sin ningún error visible.
            bounds = (
                f"rango permitido: {low if low is not None else 'inf'}"
                f"..{high if high is not None else 'inf'}"
            )
            existing = node.get("description", "")
            node = {**node, "description": f"{existing} ({bounds})".strip()}

        cleaned = {k: _strip_unsupported(v) for k, v in node.items() if k not in unsupported}
        if cleaned.get("type") == "object":
            # La salida estructurada exige `additionalProperties: false` y una
            # lista `required` explícita en cada objeto.
            cleaned["additionalProperties"] = False
            props = cleaned.get("properties") or {}
            cleaned["required"] = sorted(props)
        return cleaned
    if isinstance(node, list):
        return [_strip_unsupported(item) for item in node]
    return node


def json_schema_for(model: type[BaseModel]) -> dict[str, Any]:
    """Esquema JSON apto para `output_config.format`.

    Se resuelven las referencias internas (`$ref`/`$defs`): la salida
    estructurada no admite esquemas recursivos, y dejar referencias sin
    resolver funciona hasta que alguien anida un modelo y deja de funcionar.
    """
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any, depth: int = 0) -> Any:
        if depth > 12:
            return {"type": "string"}
        if isinstance(node, dict):
            if "$ref" in node:
                ref_name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(defs.get(ref_name, {"type": "string"}), depth + 1)
            return {k: resolve(v, depth + 1) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(item, depth + 1) for item in node]
        return node

    return _strip_unsupported(resolve(raw))


SCHEMAS: dict[str, type[BaseModel]] = {
    "briefing": BriefingOutput,
    "chat": ChatOutput,
    "verifier": VerifierOutput,
    "simulator": SimulatorTurn,
}
