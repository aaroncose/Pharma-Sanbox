"""Motor de políticas.

Evalúa la solicitud **antes** de llamar al modelo y la respuesta **después**.
Las reglas se leen de la tabla `policies`, no del código: quien decide qué se
bloquea es compliance, y una decisión suya no debería requerir un despliegue.

Dos ideas que sostienen el diseño:

**La detección por patrones es la capa débil, y se sabe.** Una expresión
regular no entiende una petición reformulada. Por eso no es la única defensa:
el bloqueo de recomendación clínica se apoya además en que el agente solo puede
citar documentación aprobada, en el verificador posterior y en la revisión
humana. Los patrones sirven para lo que sirven —cortar lo evidente y **dejar
registro del intento**— y `docs/limitations.md` lo dice sin adornos.

**Ante la duda, revisión humana, no bloqueo.** Un falso positivo que llega a
una persona cuesta dos minutos. Un falso negativo llega a un profesional
sanitario. Cuando dos políticas discrepan gana la más restrictiva, y el empate
entre `flag` y `require_review` se resuelve hacia `require_review`.

La evaluación de la respuesta se hace sobre el texto **ya generado**, no sobre
la intención declarada por el modelo: un modelo que se autoevalúa como seguro
no es una comprobación.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.policies.catalog import POLICIES_BY_CODE, PolicyDefinition

log = get_logger("policy")

Action = Literal["allow", "flag", "require_review", "block"]

# Orden de severidad. Al combinar decisiones siempre gana la mayor.
_ACTION_RANK: dict[Action, int] = {
    "allow": 0,
    "flag": 1,
    "require_review": 2,
    "block": 3,
}


@dataclass(slots=True)
class PolicyHit:
    """Una política que se ha disparado."""

    code: str
    version: str
    action: Action
    severity: str
    title: str
    reason: str
    # Fragmento que disparó la regla, recortado. Se guarda para que compliance
    # pueda juzgar si fue un falso positivo sin tener que reproducir el caso.
    evidence: str = ""


@dataclass(slots=True)
class PolicyDecision:
    action: Action = "allow"
    hits: list[PolicyHit] = field(default_factory=list)
    message: str | None = None

    @property
    def blocked(self) -> bool:
        return self.action == "block"

    @property
    def requires_review(self) -> bool:
        return self.action in ("require_review", "block")

    @property
    def codes(self) -> list[str]:
        return [hit.code for hit in self.hits]

    def merge(self, other: PolicyDecision) -> PolicyDecision:
        """Combina dos decisiones quedándose con la más restrictiva."""
        combined = PolicyDecision(
            action=(
                self.action
                if _ACTION_RANK[self.action] >= _ACTION_RANK[other.action]
                else other.action
            ),
            hits=[*self.hits, *other.hits],
        )
        combined.message = (
            self.message
            if _ACTION_RANK[self.action] >= _ACTION_RANK[other.action]
            else other.message
        ) or self.message or other.message
        return combined


def _excerpt(haystack: str, match: re.Match[str], width: int = 60) -> str:
    start = max(0, match.start() - width // 2)
    end = min(len(haystack), match.end() + width // 2)
    prefix = "..." if start else ""
    suffix = "..." if end < len(haystack) else ""
    return prefix + haystack[start:end].strip() + suffix


class PolicyEngine:
    """Evalúa políticas cargadas desde la base de datos.

    Las políticas se recargan por tenant y se cachean en memoria durante la
    vida del proceso. El catálogo del código actúa de respaldo si la tabla no
    tiene la política: preferimos evaluar con la definición conocida a no
    evaluar nada.
    """

    def __init__(self) -> None:
        self._cache: dict[str | None, dict[str, dict[str, Any]]] = {}
        self._compiled: dict[tuple[str, str], list[re.Pattern[str]]] = {}

    # ── Carga ────────────────────────────────────────────────────────────────

    def load(self, session: Session, tenant_id: str | None) -> dict[str, dict[str, Any]]:
        if tenant_id in self._cache:
            return self._cache[tenant_id]

        rows = session.execute(
            text(
                "SELECT code, version, category, title, description, severity, "
                "       action, config, tenant_id "
                "  FROM policies WHERE is_enabled "
                # Las filas del tenant van después para que sobrescriban a las
                # globales al construir el diccionario.
                " ORDER BY (tenant_id IS NULL) DESC"
            )
        ).mappings().all()

        policies = {row["code"]: dict(row) for row in rows}
        self._cache[tenant_id] = policies
        return policies

    def invalidate(self) -> None:
        self._cache.clear()
        self._compiled.clear()

    def _patterns(
        self, code: str, version: str, config: dict[str, Any]
    ) -> list[re.Pattern[str]]:
        key = (code, version)
        if key not in self._compiled:
            self._compiled[key] = [
                re.compile(pattern, re.IGNORECASE)
                for pattern in config.get("patterns", [])
            ]
        return self._compiled[key]

    def _definition(
        self, code: str, loaded: dict[str, dict[str, Any]]
    ) -> dict[str, Any] | None:
        if code in loaded:
            return loaded[code]
        fallback: PolicyDefinition | None = POLICIES_BY_CODE.get(code)
        if fallback is None:
            return None
        log.warning("policy_missing_in_db_using_catalog", code=code)
        return {
            "code": fallback.code,
            "version": fallback.version,
            "title": fallback.title,
            "severity": fallback.severity,
            "action": fallback.action,
            "config": fallback.config,
        }

    # ── Evaluación ───────────────────────────────────────────────────────────

    def _match_patterns(
        self,
        code: str,
        content: str,
        loaded: dict[str, dict[str, Any]],
        *,
        reason: str,
    ) -> PolicyDecision:
        definition = self._definition(code, loaded)
        if definition is None:
            return PolicyDecision()

        config = definition["config"] or {}
        for pattern in self._patterns(code, definition["version"], config):
            match = pattern.search(content)
            if match:
                return PolicyDecision(
                    action=definition["action"],
                    hits=[
                        PolicyHit(
                            code=definition["code"],
                            version=definition["version"],
                            action=definition["action"],
                            severity=definition["severity"],
                            title=definition["title"],
                            reason=reason,
                            evidence=_excerpt(content, match),
                        )
                    ],
                    message=config.get("referral_message"),
                )
        return PolicyDecision()

    def evaluate_request(
        self, session: Session, *, tenant_id: str | None, question: str
    ) -> PolicyDecision:
        """Evalúa lo que pide el usuario, antes de gastar una llamada al modelo.

        Bloquear aquí ahorra coste y latencia, pero ese no es el motivo: el
        motivo es que una solicitud de recomendación clínica no debe llegar al
        modelo en absoluto. Que el modelo se niegue correctamente es una
        segunda línea, no la primera.
        """
        loaded = self.load(session, tenant_id)
        decision = PolicyDecision()

        for code, reason in (
            ("NO_CLINICAL_RECOMMENDATION",
             "La consulta solicita criterio clínico individualizado"),
            ("NO_RISK_CONCEALMENT",
             "La consulta pide omitir o minimizar información de seguridad"),
            ("NO_UNVERIFIED_PROMOTION",
             "La consulta induce una afirmación promocional no verificada"),
        ):
            decision = decision.merge(
                self._match_patterns(code, question, loaded, reason=reason)
            )

        return decision

    def evaluate_documents(
        self, session: Session, *, tenant_id: str | None, documents: str
    ) -> PolicyDecision:
        """Busca inyecciones de prompt en el material recuperado.

        La defensa efectiva contra la inyección **no es esta**: es la
        separación estructural, entregar el material delimitado y declarado
        como datos no fiables. Esta comprobación existe para registrar el
        intento, porque un documento aprobado que contiene instrucciones
        dirigidas al modelo es un incidente de seguridad aunque no funcione.
        """
        loaded = self.load(session, tenant_id)
        return self._match_patterns(
            "PROMPT_INJECTION_DEFENCE",
            documents,
            loaded,
            reason="El material recuperado contiene instrucciones dirigidas al modelo",
        )

    def evaluate_response(
        self,
        session: Session,
        *,
        tenant_id: str | None,
        answer: str,
        source_count: int,
    ) -> PolicyDecision:
        """Evalúa la respuesta generada.

        Se hace sobre el texto producido y sobre el número real de fuentes
        recuperadas, no sobre los campos que el modelo se ha autoasignado. Un
        modelo que declara `requires_human_review: false` no es una
        comprobación de que no haga falta revisión.
        """
        loaded = self.load(session, tenant_id)
        decision = PolicyDecision()

        for code, reason in (
            ("NO_CLINICAL_RECOMMENDATION",
             "La respuesta contiene una recomendación clínica"),
            ("NO_UNVERIFIED_PROMOTION",
             "La respuesta contiene una afirmación promocional no verificada"),
            ("NO_RISK_CONCEALMENT",
             "La respuesta minimiza u omite información de seguridad"),
        ):
            decision = decision.merge(
                self._match_patterns(code, answer, loaded, reason=reason)
            )

        # Afirmación de producto sin fuente.
        claim_policy = self._definition("PRODUCT_CLAIM_REQUIRES_SOURCE", loaded)
        min_sources = 1
        if claim_policy:
            min_sources = (claim_policy["config"] or {}).get("min_sources", 1)
        if claim_policy and source_count < min_sources:
            markers = (claim_policy["config"] or {}).get("claim_markers", [])
            lowered = answer.lower()
            triggered = next((m for m in markers if m in lowered), None)
            if triggered:
                decision = decision.merge(
                    PolicyDecision(
                        action=claim_policy["action"],
                        hits=[
                            PolicyHit(
                                code=claim_policy["code"],
                                version=claim_policy["version"],
                                action=claim_policy["action"],
                                severity=claim_policy["severity"],
                                title=claim_policy["title"],
                                reason=(
                                    "La respuesta afirma algo sobre el producto sin "
                                    "ninguna fuente aprobada que lo respalde"
                                ),
                                evidence=f"término detectado: «{triggered}»",
                            )
                        ],
                    )
                )

        return decision


_engine: PolicyEngine | None = None


def get_policy_engine() -> PolicyEngine:
    global _engine
    if _engine is None:
        _engine = PolicyEngine()
    return _engine
