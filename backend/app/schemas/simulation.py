"""Contratos del simulador conversacional."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SimulationStart(BaseModel):
    hcp_id: str
    product_id: str
    scenario: str = Field(min_length=5, max_length=300)
    objective: str = Field(min_length=5, max_length=300)
    # La actitud del profesional. Se elige al empezar en lugar de dejarla al
    # azar: practicar la misma conversación con un interlocutor escéptico y con
    # uno receptivo son dos entrenamientos distintos, y quien practica debe
    # poder elegir cuál necesita.
    attitude: Literal["receptivo", "escéptico", "con prisa", "hostil"] = "escéptico"
    modality: Literal["text", "voice"] = "text"
    prompt_version: str | None = None


class RepTurn(BaseModel):
    utterance: str = Field(min_length=1, max_length=4000)
    # Telemetría de voz. Sin `duration_ms` y `was_interrupted` no se puede
    # diagnosticar una conversación hablada: no se distingue a quien habla de
    # más de quien se queda callado.
    started_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    was_interrupted: bool = False

    @field_validator("utterance")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("el turno no puede estar en blanco")
        return value.strip()
