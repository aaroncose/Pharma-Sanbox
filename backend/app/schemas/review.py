"""Contratos de la cola de revisión humana.

El mínimo de 20 caracteres en `rationale` no es una validación de formulario. La
restricción `review_decision_needs_rationale` de la base de datos exige 10; aquí
se pide el doble a propósito, porque la base de datos es la red de seguridad y
la API es donde se decide qué se considera un motivo aceptable. «Vale», «ok» y
«revisado» caben en diez caracteres y no explican nada.

Es la diferencia entre supervisión humana real y un botón de aprobar. Si el
motivo no se exige, la cola se vacía igual y el registro queda inservible: seis
meses después nadie puede decir por qué se dejó pasar aquel contenido.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MIN_RATIONALE = 20


class ReviewDecision(BaseModel):
    rationale: str = Field(min_length=MIN_RATIONALE, max_length=4000)

    @field_validator("rationale")
    @classmethod
    def substantive(cls, value: str) -> str:
        cleaned = value.strip()
        if len(cleaned) < MIN_RATIONALE:
            raise ValueError(
                f"el motivo debe tener al menos {MIN_RATIONALE} caracteres reales"
            )
        return cleaned


class ReviewEdit(ReviewDecision):
    """Corrección del contenido, no solo un juicio sobre él.

    Cuando compliance reescribe la respuesta está produciendo el par
    `(lo que el agente dijo, lo que debería haber dicho)`, que es exactamente la
    forma de un caso de evaluación. Por eso una edición genera siempre un
    ejemplo de feedback y una aprobación no.
    """

    edited_content: str = Field(min_length=10, max_length=50_000)
    # Qué debería hacer el sistema la próxima vez ante un caso así. Se pide por
    # separado del contenido corregido porque son cosas distintas: el contenido
    # es este caso, la expectativa es la regla. Sin ella, el ejemplo sirve para
    # una comparación literal y no para evaluar comportamiento.
    expected_behaviour: str = Field(default="", max_length=2000)


class RegenerationRequest(ReviewDecision):
    """Devolver al agente con instrucciones, en lugar de rechazar sin más."""

    guidance: str = Field(default="", max_length=2000)


ReviewOutcome = Literal["approved", "rejected", "edited", "regeneration_requested"]
