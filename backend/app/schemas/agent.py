"""Contratos de entrada de los endpoints que invocan al agente.

Los límites de longitud tienen dos funciones y conviene no confundirlas. La
primera es de coste: cada carácter entra en el prompt y se paga. La segunda es
de seguridad: un campo de texto sin tope es el vehículo natural de un intento de
inyección largo, y aunque la defensa real sea la separación estructural del
contexto, no hay ningún motivo legítimo para que un objetivo de visita ocupe
veinte mil caracteres.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class BriefingRequest(BaseModel):
    hcp_id: str
    product_id: str
    objective: str = Field(min_length=5, max_length=500)
    duration_minutes: int = Field(default=15, ge=5, le=90)
    # Permite ejecutar la misma petición contra dos versiones del prompt. Es lo
    # que hace posible la comparación v1.2 frente a v1.3 de la suite de
    # evaluación sin un camino de código distinto del de producción.
    prompt_version: str | None = None

    @field_validator("objective")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("el objetivo no puede estar en blanco")
        return value.strip()


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    product_id: str | None = None
    prompt_version: str | None = None

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("la pregunta no puede estar en blanco")
        return value.strip()


class MeetingSummaryRequest(BaseModel):
    hcp_id: str
    product_id: str
    # Las notas crudas del comercial. Es el único campo del sistema donde entra
    # texto libre largo y no revisado, y por eso el resumen se genera con el
    # mismo harness que todo lo demás en lugar de con una llamada directa.
    notes: str = Field(min_length=20, max_length=20_000)
    channel: str = Field(default="in_person", max_length=30)
    occurred_at: str | None = None
    # Si el resumen debe quedar registrado como interacción con el profesional.
    # Por defecto sí: un resumen que no se archiva no sirve para el briefing de
    # la visita siguiente, que es donde este módulo aporta de verdad.
    record_interaction: bool = True
    prompt_version: str | None = None

    @field_validator("notes")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("las notas no pueden estar en blanco")
        return value.strip()
