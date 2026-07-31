"""Contratos de entrada de evaluaciones y Failure Lab.

Los dos módulos comparten fichero porque comparten propósito: son las dos
superficies desde las que se comprueba que el sistema hace lo que dice. Uno mide
la calidad de lo que produce el agente; el otro, que las defensas responden
cuando se las ataca.

`force_mock` aparece en los dos y por defecto vale True. No es una comodidad de
desarrollo: es lo que hace comparables dos ejecuciones. Ponerlo en False es una
decisión con coste en euros y en latencia, y por eso hay que escribirla.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvalRunRequest(BaseModel):
    """Ejecución de la suite sobre una versión concreta del prompt."""

    # None significa «la versión activa». Se distingue de una cadena vacía a
    # propósito: la ejecución guarda después la versión que realmente se usó, de
    # modo que «la activa» nunca queda registrado como tal sino con su número.
    prompt_version: str | None = Field(
        default=None,
        max_length=20,
        description="Versión del prompt a evaluar. Ausente usa la activa.",
    )
    force_mock: bool = Field(
        default=True,
        description=(
            "Ejecuta contra el proveedor determinista. Desactivarlo llama al "
            "modelo real una vez por caso, con su coste."
        ),
    )


class EvalCompareRequest(BaseModel):
    """Dos versiones, una tras otra, en las mismas condiciones.

    Existe como endpoint propio en lugar de dejar que el cliente llame dos veces
    porque la comparación solo vale si ambas corren contra el mismo corpus y el
    mismo proveedor. Encadenar dos peticiones independientes permite comparar sin
    darse cuenta una ejecución mock con una real.
    """

    versions: list[str] = Field(
        min_length=2,
        max_length=4,
        description="Versiones a comparar, p. ej. ['v1.2', 'v1.3'].",
    )
    force_mock: bool = True


class FailureRunRequest(BaseModel):
    """Ejecución de un escenario del laboratorio."""

    slug: str = Field(min_length=3, max_length=60)
