"""Prompts versionados.

Los prompts se cargan de la tabla `prompt_versions`, no de disco. El motivo es
la trazabilidad: cada salida del agente guarda el nombre y la versión exacta
del prompt que la produjo, y esa referencia tiene que apuntar a algo inmutable.
Si el prompt viviera en un fichero editable, «briefing v1.3» significaría cosas
distintas según el día, y la comparación entre versiones de la evaluación no
mediría nada.

Los ficheros de `prompts/` son la fuente para los seeds; a partir de ahí manda
la base de datos.

El renderizado es sustitución literal de `{{variable}}`, sin motor de
plantillas. Deliberado: un motor con lógica —condicionales, bucles, filtros—
convierte el prompt en código que nadie revisa y abre la puerta a que el
contenido sustituido se interprete. Aquí lo sustituido es siempre texto.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.logging import get_logger

log = get_logger("prompts")

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    template: str

    @property
    def ref(self) -> str:
        return f"{self.name}.{self.version}"

    def render(self, **variables: object) -> str:
        """Sustituye los marcadores. Falla si falta alguno.

        Un marcador sin sustituir llegaría al modelo como el literal
        `{{documents}}`, y el modelo respondería igual, con una respuesta
        plausible construida sobre un contexto vacío. Es el peor fallo posible
        aquí: silencioso y con salida convincente. Mejor romper.
        """
        missing = {
            name
            for name in _PLACEHOLDER_RE.findall(self.template)
            if name not in variables
        }
        if missing:
            raise KeyError(
                f"Faltan variables para {self.ref}: {sorted(missing)}. "
                "Un marcador sin sustituir produce una respuesta plausible "
                "sobre un contexto vacío."
            )

        def replace(match: re.Match[str]) -> str:
            return str(variables[match.group(1)])

        return _PLACEHOLDER_RE.sub(replace, self.template)


class PromptRegistry:
    """Carga y cachea prompts. La caché es por `(nombre, versión)`, inmutable."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str | None], Prompt] = {}

    def get(self, session: Session, name: str, version: str | None = None) -> Prompt:
        """Devuelve una versión concreta, o la activa si no se indica ninguna.

        Pedir una versión concreta es lo que permite ejecutar la suite de
        evaluación contra `briefing.v1.2` y `briefing.v1.3` en la misma
        ejecución y comparar los resultados.
        """
        key = (name, version)
        if key in self._cache:
            return self._cache[key]

        if version is None:
            row = session.execute(
                text(
                    "SELECT name, version, template FROM prompt_versions "
                    " WHERE name = :name AND is_active"
                ),
                {"name": name},
            ).mappings().first()
        else:
            row = session.execute(
                text(
                    "SELECT name, version, template FROM prompt_versions "
                    " WHERE name = :name AND version = :version"
                ),
                {"name": name, "version": version},
            ).mappings().first()

        if row is None:
            raise LookupError(
                f"No existe el prompt {name}"
                + (f" versión {version}" if version else " con versión activa")
            )

        prompt = Prompt(name=row["name"], version=row["version"], template=row["template"])
        self._cache[key] = prompt
        # La versión concreta también se cachea bajo su clave explícita, para
        # que pedir la activa y pedirla por número devuelvan el mismo objeto.
        self._cache[(name, prompt.version)] = prompt
        return prompt

    def list_versions(self, session: Session, name: str) -> list[tuple[str, bool]]:
        rows = session.execute(
            text(
                "SELECT version, is_active FROM prompt_versions "
                " WHERE name = :name ORDER BY version"
            ),
            {"name": name},
        ).all()
        return [(version, active) for version, active in rows]

    def invalidate(self) -> None:
        self._cache.clear()


_registry: PromptRegistry | None = None


def get_prompt_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        _registry = PromptRegistry()
    return _registry
