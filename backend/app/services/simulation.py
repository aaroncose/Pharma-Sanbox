"""Simulador conversacional: práctica bajo las reglas reales.

La idea que sostiene el módulo: **lo que dice el comercial en la simulación se
evalúa con el mismo motor de políticas que gobierna al agente en producción.**

No es una comodidad de implementación. Un simulador que juzga con reglas propias
—más laxas, o simplemente distintas— entrena para un examen que no existe. Si
«CardioX reduce el riesgo un 37%» se marca cuando lo dice el agente, tiene que
marcarse cuando lo dice la persona, y por el mismo código. Así el entrenamiento
mide lo mismo que la producción.

De ahí salen tres consecuencias:

**Las marcas se registran turno a turno, no al final.** Es lo que permite
enseñar el riesgo en vivo mientras se practica. Reconstruirlas al final las
haría depender de que el modelo del informe se acuerde, y una infracción
detectada tres turnos después llega tarde para corregir el hábito.

**La puntuación de cumplimiento se cuenta, no se genera.** El modelo juzga la
comunicación, que es cualitativa y es su terreno; el código cuenta las
infracciones. Pedir una única cifra al modelo produciría un número que parece
preciso, no es reproducible y mezcla dos cosas que se comprueban de forma
distinta.

**El simulador no ve la biblioteca; el informe sí.** La asimetría es
deliberada. Un médico real no ha leído el material comercial aprobado, y si el
simulador lo conociera haría preguntas antinaturalmente alineadas con las
respuestas disponibles. El informe, en cambio, necesita la biblioteca para poder
decir qué fuente debería haberse citado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.policies.engine import PolicyDecision, get_policy_engine

log = get_logger("simulation")

# Cuánto resta cada infracción de la puntuación de cumplimiento, por gravedad.
# Son constantes visibles y no un modelo entrenado a propósito: el comercial
# tiene derecho a saber por qué su nota es la que es, y un número que nadie
# puede reconstruir no corrige ningún hábito.
SEVERITY_PENALTY: dict[str, int] = {
    "critical": 40,
    "high": 25,
    "medium": 12,
    "low": 5,
}

# Bonificación por reconocer un límite en lugar de improvisar. Es la habilidad
# que este entrenamiento busca, así que puntúa: sin esto, la nota óptima se
# consigue hablando poco, que es justo lo contrario de lo que se quiere enseñar.
OUT_OF_BOUNDS_BONUS = 10


@dataclass(slots=True)
class TurnEvaluation:
    """Resultado de evaluar un turno del comercial."""

    decision: PolicyDecision
    flag: str | None = None
    risk_level: str = "low"
    # Documentos aprobados que el comercial nombró de viva voz. Es el dato que
    # permite decirle «lo citaste bien» en lugar de solo «no lo citaste».
    cited_documents: list[str] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.decision.hits)


# Un identificador de estudio como «CARDIO-101»: letras, guion y dígitos. Es la
# forma en que un comercial cita de viva voz, y la que hay que poder reconocer.
_STUDY_CODE_RE = re.compile(r"\b[A-Z][A-Z0-9]{2,}[- ]?\d{2,}\b")


def count_verbal_sources(session: Session, *, utterance: str) -> list[str]:
    """Qué documentos aprobados menciona el comercial, por su nombre.

    Un comercial no dice «[doc:109773df]». Dice «según el resumen aprobado del
    estudio CARDIO-101», que es exactamente la atribución correcta en una
    conversación hablada.

    El primer diseño pasaba `source_count=0` siempre, con el argumento de que
    una persona no cita identificadores. La consecuencia, encontrada por
    `test_a_prudent_answer_is_not_flagged`, era que la respuesta **modélica**
    —nombrar el estudio y ceñirse al dato aprobado— se marcaba igual que la
    afirmación inventada. Un entrenamiento así enseña a no mencionar evidencia,
    que es lo contrario de lo que se busca.

    La comprobación se hace contra `citable_documents`, no contra una lista de
    palabras como «según» o «el estudio». Nombrar un estudio que no existe, o
    uno retirado, no cuenta como atribución: sería premiar exactamente la
    invención que el sistema persigue.
    """
    if not utterance.strip():
        return []

    candidatos = {
        m.group(0).upper().replace(" ", "-")
        for m in _STUDY_CODE_RE.finditer(utterance)
    }

    filas = session.execute(
        text("SELECT id, title FROM citable_documents")
    ).mappings().all()

    reconocidos: list[str] = []
    lowered = utterance.lower()

    for fila in filas:
        titulo = fila["title"]
        # Coincidencia por código de estudio presente en el título.
        codigos = {
            m.group(0).upper().replace(" ", "-")
            for m in _STUDY_CODE_RE.finditer(titulo)
        }
        if codigos & candidatos:
            reconocidos.append(str(fila["id"]))
            continue
        # O por el nombre del documento dicho casi literalmente. Se exige una
        # longitud mínima para que «FAQ» o «Ficha» no valgan por sí solas.
        nucleo = titulo.split("—")[0].strip().lower()
        if len(nucleo) >= 12 and nucleo in lowered:
            reconocidos.append(str(fila["id"]))

    return reconocidos


def evaluate_rep_turn(
    session: Session, *, tenant_id: str, utterance: str
) -> TurnEvaluation:
    """Pasa lo que dijo el comercial por el motor de políticas de producción.

    La única adaptación es cómo se cuenta la atribución: el agente cita con
    identificadores y una persona cita nombrando el estudio. Se resuelve
    reconociendo el nombre contra la biblioteca aprobada, no relajando la
    política. La regla que se aplica sigue siendo la misma.
    """
    fuentes = count_verbal_sources(session, utterance=utterance)

    decision = get_policy_engine().evaluate_response(
        session,
        tenant_id=tenant_id,
        answer=utterance,
        source_count=len(fuentes),
    )

    if not decision.hits:
        return TurnEvaluation(decision=decision, cited_documents=fuentes)

    # La marca guarda el código de la política más grave, no un texto libre:
    # así el informe y el recuento operan sobre el mismo vocabulario cerrado
    # que el resto del sistema.
    peor = max(decision.hits, key=lambda h: SEVERITY_PENALTY.get(h.severity, 0))
    return TurnEvaluation(
        decision=decision,
        flag=peor.code,
        risk_level=peor.severity,
        cited_documents=fuentes,
    )


@dataclass(slots=True)
class ComplianceScore:
    """Puntuación de cumplimiento, con su desglose.

    Se devuelve el desglose y no solo el número porque el número sin el desglose
    es una opinión: el comercial no puede comprobarla ni saber qué corregir.
    """

    score: int
    flagged_turns: int
    total_rep_turns: int
    penalties: list[dict[str, Any]] = field(default_factory=list)
    bonus_applied: int = 0

    @property
    def clean(self) -> bool:
        return self.flagged_turns == 0


def compliance_score(
    turns: list[dict[str, Any]], *, handled_out_of_bounds_well: bool
) -> ComplianceScore:
    """Cuenta las infracciones registradas y las convierte en nota.

    Opera sobre las marcas que el motor dejó **durante** la conversación, no
    sobre una relectura de la transcripción. Dos motivos: la nota se puede
    reconstruir exactamente a partir de la base de datos, y no depende de que un
    modelo vuelva a detectar lo mismo en una segunda pasada.
    """
    rep_turns = [t for t in turns if t["speaker"] == "rep"]
    flagged = [t for t in rep_turns if t.get("compliance_flag")]

    score = 100
    penalties: list[dict[str, Any]] = []

    for turn in flagged:
        severity = turn.get("risk_level") or "medium"
        penalty = SEVERITY_PENALTY.get(severity, SEVERITY_PENALTY["medium"])
        score -= penalty
        penalties.append(
            {
                "turn_ordinal": turn["ordinal"],
                "policy_code": turn["compliance_flag"],
                "severity": severity,
                "penalty": penalty,
            }
        )

    bonus = OUT_OF_BOUNDS_BONUS if handled_out_of_bounds_well else 0
    score = max(0, min(100, score + bonus))

    return ComplianceScore(
        score=score,
        flagged_turns=len(flagged),
        total_rep_turns=len(rep_turns),
        penalties=penalties,
        bonus_applied=bonus,
    )


# Techo de la nota final cuando hubo una infracción de esta gravedad. No es un
# peso: es un tope. Ver `overall_score`.
SEVERITY_CAP: dict[str, int] = {
    "critical": 50,
    "high": 70,
}


def applicable_cap(compliance: ComplianceScore) -> int | None:
    """Techo que impone la infracción más grave, si alguna lo impone.

    Se devuelve al cliente junto con la nota: si el techo actuó, el comercial
    tiene que saber que su nota no la limitó cómo habló sino qué afirmó.
    """
    techos = [
        SEVERITY_CAP[p["severity"]]
        for p in compliance.penalties
        if p["severity"] in SEVERITY_CAP
    ]
    return min(techos) if techos else None


def overall_score(communication: int, compliance: ComplianceScore) -> int:
    """Nota final. El cumplimiento no pondera: limita.

    El primer diseño era una media ponderada 60/40 a favor del cumplimiento, con
    el argumento de que una torpeza al expresarse pierde una oportunidad
    comercial y una afirmación no aprobada crea un problema regulatorio.

    `test_compliance_weighs_more_than_communication` demostró que la aritmética
    no sostenía el argumento: una infracción crítica restaba 40 puntos de
    cumplimiento —24 de la nota final— mientras que el margen entre expresarse
    mal y expresarse muy bien valía 26. **Un comercial elocuente que afirmaba
    algo no aprobado sacaba mejor nota que uno torpe e impecable.** Con esa
    escala, el entrenamiento premia justo lo que debe corregir.

    El fallo no eran los pesos, era el modelo: mientras cumplimiento y
    comunicación se sumen, siempre existe una cantidad de elocuencia que
    compensa una infracción. Ninguna cantidad debería.

    Así que el cumplimiento actúa como techo. La comunicación decide la nota
    dentro de lo que el cumplimiento permite, y una infracción grave impide
    aprobar por bien que se hable. Es lo que de verdad ocurre: ninguna
    presentación brillante arregla una afirmación que la agencia reguladora
    puede sancionar.
    """
    base = round(0.4 * communication + 0.6 * compliance.score)

    techo = min(
        (
            SEVERITY_CAP[p["severity"]]
            for p in compliance.penalties
            if p["severity"] in SEVERITY_CAP
        ),
        default=100,
    )
    return min(base, techo)


def format_transcript(turns: list[dict[str, Any]]) -> str:
    """Serializa la conversación para el prompt del informe.

    Cada turno lleva su número porque el informe tiene que poder señalar cuál
    concretamente conviene reformular. «Algunas respuestas» no sirve de nada.
    """
    lineas: list[str] = []
    for turn in turns:
        quien = {"hcp": "PROFESIONAL", "rep": "COMERCIAL", "system": "SISTEMA"}.get(
            turn["speaker"], turn["speaker"].upper()
        )
        lineas.append(f"[turno {turn['ordinal']}] {quien}: {turn['content']}")
    return "\n".join(lineas) or "(la simulación no tuvo turnos)"


def format_flags(turns: list[dict[str, Any]]) -> str:
    """Presenta las marcas al modelo como hechos, no como algo que deba juzgar.

    Si se le pidiera detectarlas, el informe dependería de que las volviera a
    encontrar, y dos ejecuciones sobre la misma simulación darían informes
    distintos. Se le dan hechas para que se ocupe de lo suyo: explicarlas y
    proponer cómo decirlo bien.
    """
    marcadas = [t for t in turns if t.get("compliance_flag")]
    if not marcadas:
        return "(ninguna: no se detectó ninguna infracción de política)"

    return "\n".join(
        f"- Turno {t['ordinal']}: {t['compliance_flag']} "
        f"(gravedad {t.get('risk_level') or 'medium'}) sobre: «{t['content'][:160]}»"
        for t in marcadas
    )


def rep_ordinals(turns: list[dict[str, Any]]) -> list[int]:
    """Turnos que dijo el comercial. Los únicos que se le pueden reformular."""
    return [t["ordinal"] for t in turns if t["speaker"] == "rep"]


def anchor_improvable_answers(
    answers: list[Any], turns: list[dict[str, Any]]
) -> tuple[list[Any], int]:
    """Anula las referencias a turnos que el comercial no dijo.

    Encontrado con el modelo real: el informe señaló como mejorable el turno 5,
    que era del médico. La observación era válida —el comercial dejó una
    pregunta sin responder— pero el ancla era falsa, y una interfaz que muestra
    «tu respuesta del turno 5» sobre la frase del interlocutor invalida el
    informe entero a ojos de quien lo lee.

    Se anula el ancla y se conserva el contenido. Descartar el elemento perdería
    feedback correcto; dejarlo como está mostraría una atribución falsa.
    Devuelve también cuántas se corrigieron, para que quede en la traza y se
    pueda medir si el prompt necesita ajuste.
    """
    validos = set(rep_ordinals(turns))
    corregidas = 0

    for answer in answers:
        ordinal = getattr(answer, "turn_ordinal", None)
        if ordinal is not None and ordinal not in validos:
            log.info(
                "improvable_answer_anchor_dropped",
                claimed_ordinal=ordinal,
                valid_ordinals=sorted(validos),
            )
            object.__setattr__(answer, "turn_ordinal", None)
            corregidas += 1

    return answers, corregidas


def load_turns(session: Session, simulation_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            "SELECT ordinal, speaker, content, compliance_flag, started_ms, "
            "       duration_ms, was_interrupted, created_at "
            "  FROM simulation_turns WHERE simulation_id = CAST(:id AS uuid) "
            " ORDER BY ordinal"
        ),
        {"id": simulation_id},
    ).mappings().all()

    # `risk_level` no está en la tabla: se deriva del código de política, que sí
    # se guardó. Guardar la gravedad duplicada permitiría que divergiera de la
    # política si esta se recalifica más adelante.
    definiciones = {
        p["code"]: p["severity"]
        for p in session.execute(
            text("SELECT code, severity::text AS severity FROM policies")
        ).mappings()
    }

    return [
        {**dict(r), "risk_level": definiciones.get(r["compliance_flag"] or "", None)}
        for r in rows
    ]
