"""Trazas del agente.

Una traza es la secuencia ordenada de pasos que produjo una salida: qué
contexto se construyó, qué políticas se evaluaron, qué herramientas se
llamaron, qué devolvió el modelo y qué dijo el verificador.

Sin esto, cuando una respuesta sale mal solo se puede mirar el resultado y
especular. Con esto se puede señalar el paso concreto: la recuperación no
encontró la sección, o la encontró y el modelo no la citó, o la citó y el
verificador la rechazó. Son tres fallos distintos con tres arreglos distintos, y
desde fuera se parecen.

Lo que se guarda de cada paso es un **resumen**, no la entrada y la salida
completas. Guardar los prompts íntegros con el contexto documental dentro
convertiría la tabla de trazas en una copia sin control de la biblioteca, y en
un sitio donde acabarían apareciendo datos personales que el resto del sistema
se esfuerza en minimizar.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger("trace")

StepType = Literal[
    "context_build", "policy_check", "retrieval", "tool_call", "llm_call", "verify", "repair"
]


@dataclass(slots=True)
class TraceStep:
    step_type: StepType
    name: str
    status: str
    latency_ms: int
    input_summary: dict[str, Any] = field(default_factory=dict)
    output_summary: dict[str, Any] = field(default_factory=dict)


class AgentTrace:
    """Acumula los pasos de una ejecución y los persiste al final.

    Se persiste al terminar y no paso a paso para que la traza sea una unidad:
    una ejecución abortada a mitad no deja media traza que parezca completa.
    """

    def __init__(self, trace_id: str, tenant_id: str | None) -> None:
        self.trace_id = trace_id
        self.tenant_id = tenant_id
        self.steps: list[TraceStep] = []
        self._started = time.perf_counter()

    def record(
        self,
        step_type: StepType,
        name: str,
        *,
        status: str = "ok",
        latency_ms: int = 0,
        input_summary: dict[str, Any] | None = None,
        output_summary: dict[str, Any] | None = None,
    ) -> None:
        self.steps.append(
            TraceStep(
                step_type=step_type,
                name=name,
                status=status,
                latency_ms=latency_ms,
                input_summary=input_summary or {},
                output_summary=output_summary or {},
            )
        )

    @property
    def total_latency_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)

    def persist(self, session: Session) -> None:
        """Escribe la traza. Nunca propaga excepción.

        Igual que en auditoría: un fallo al guardar la traza no puede tumbar la
        operación de negocio que sí funcionó.
        """
        try:
            for index, step in enumerate(self.steps, start=1):
                session.execute(
                    text(
                        "INSERT INTO agent_traces "
                        "  (tenant_id, trace_id, step, step_type, name, status, "
                        "   input_summary, output_summary, latency_ms) "
                        "VALUES (CAST(NULLIF(:tenant_id,'') AS uuid), :trace_id, :step, "
                        "        :step_type, :name, :status, CAST(:inp AS jsonb), "
                        "        CAST(:out AS jsonb), :latency_ms)"
                    ),
                    {
                        "tenant_id": self.tenant_id or "",
                        "trace_id": self.trace_id,
                        "step": index,
                        "step_type": step.step_type,
                        "name": step.name,
                        "status": step.status,
                        "inp": json.dumps(step.input_summary, ensure_ascii=False),
                        "out": json.dumps(step.output_summary, ensure_ascii=False),
                        "latency_ms": step.latency_ms,
                    },
                )
        except Exception:
            log.critical("trace_write_failed", trace_id=self.trace_id, exc_info=True)


class timed:  # noqa: N801 — se usa como `with timed() as t:`
    """Mide el tiempo de un bloque en milisegundos."""

    __slots__ = ("_start", "ms")

    def __enter__(self) -> timed:
        self._start = time.perf_counter()
        self.ms = 0
        return self

    def __exit__(self, *_args: object) -> Literal[False]:
        self.ms = int((time.perf_counter() - self._start) * 1000)
        return False
