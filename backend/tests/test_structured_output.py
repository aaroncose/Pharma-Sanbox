"""El esquema JSON que se envía al modelo.

La salida estructurada es el mecanismo por el que el modelo no puede devolver
algo fuera del contrato. Eso lo convierte en un punto único de fallo silencioso:
si el esquema está mal, el modelo obedece un contrato equivocado y todo lo demás
—validación, verificador, políticas— opera sobre una salida que nunca pudo ser
correcta.

Estas pruebas existen por un fallo real. El filtro que elimina las palabras
clave no admitidas por la salida estructurada se aplicaba a **todos** los
diccionarios del esquema, incluido el mapa `properties`, donde las claves no son
palabras clave sino nombres de campo. `FollowUpTask.title` desapareció del
esquema entero: con `additionalProperties: false`, el modelo tenía prohibido
emitir un campo que Pydantic exigía. El contrato se contradecía a sí mismo y el
único síntoma era una salida degenerada de vez en cuando.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import BaseModel, Field

from app.agent.schemas import (
    SCHEMAS,
    BriefingOutput,
    ChatOutput,
    MeetingSummaryOutput,
    SimulatorTurn,
    VerifierOutput,
    json_schema_for,
)

ALL_OUTPUT_MODELS = [
    BriefingOutput,
    ChatOutput,
    MeetingSummaryOutput,
    VerifierOutput,
    SimulatorTurn,
]

# Palabras clave de JSON Schema que el filtro elimina. Cualquiera de ellas es un
# nombre de campo perfectamente razonable en un dominio real.
KEYWORD_NAMES = ["title", "default", "pattern", "examples", "maximum", "minLength"]


def _fields_of(node: dict) -> set[str]:
    return set(node.get("properties") or {})


def test_a_field_named_like_a_schema_keyword_survives() -> None:
    """El fallo original, reducido a su mínima expresión.

    Un campo llamado `title` es corriente. Que el esquema lo borre y a la vez
    Pydantic lo exija produce un contrato imposible de cumplir.
    """

    class Task(BaseModel):
        title: str = Field(description="Título de la tarea")
        default: str = Field(default="", description="Valor por defecto")
        pattern: str = Field(default="", description="Patrón asociado")

    schema = json_schema_for(Task)

    for name in ("title", "default", "pattern"):
        assert name in schema["properties"], f"el campo '{name}' se ha perdido"
        assert name in schema["required"], f"el campo '{name}' no se exige"


def test_schema_metadata_is_still_stripped_at_the_schema_level() -> None:
    """La corrección no puede convertirse en lo contrario.

    Distinguir la posición era el objetivo; dejar de limpiar las palabras clave
    reales rompería la petición con un 400 del proveedor.
    """

    class Bounded(BaseModel):
        confidence: int = Field(default=0, ge=0, le=100)

    schema = json_schema_for(Bounded)

    # `title` de metadatos —el que Pydantic pone en la raíz— sí se elimina.
    assert "title" not in schema
    field = schema["properties"]["confidence"]
    assert "title" not in field
    assert "minimum" not in field and "maximum" not in field
    # Y el rango sigue viajando dentro de la descripción, que es lo único que el
    # modelo puede leer.
    assert "rango permitido: 0..100" in field["description"]


@pytest.mark.parametrize("model_cls", ALL_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_every_declared_field_reaches_the_model(model_cls: type[BaseModel]) -> None:
    """Ningún campo del modelo puede faltar en el esquema.

    Es la comprobación general de la que el caso de `title` fue un ejemplo. Se
    hace sobre los modelos reales para que añadir un campo con un nombre
    desafortunado falle aquí y no en producción.
    """
    schema = json_schema_for(model_cls)
    declared = set(model_cls.model_fields)
    present = _fields_of(schema)

    assert declared == present, (
        f"faltan en el esquema: {sorted(declared - present)}; "
        f"sobran: {sorted(present - declared)}"
    )


@pytest.mark.parametrize("model_cls", ALL_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_the_schema_and_pydantic_agree_on_what_is_required(
    model_cls: type[BaseModel],
) -> None:
    """El contrato no puede exigir al modelo algo que le prohíbe emitir.

    La salida estructurada obliga a que `required` sea la lista completa de
    propiedades. Si una propiedad exigida no existe, el modelo no puede
    producirla y la validación posterior la reclamará siempre: reintento de
    reparación en cada llamada, y a la segunda, salida bloqueada.
    """
    schema = json_schema_for(model_cls)
    assert set(schema["required"]) == _fields_of(schema)
    assert schema["additionalProperties"] is False


def _walk_objects(node: object):
    """Recorre todos los subesquemas de tipo objeto, incluidos los anidados."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield node
        # Las claves de `properties` son nombres de campo; sus valores sí son
        # esquemas, así que se recorre por valores.
        for value in node.values():
            yield from _walk_objects(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_objects(item)


@pytest.mark.parametrize("model_cls", ALL_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_nested_objects_obey_the_same_contract(model_cls: type[BaseModel]) -> None:
    """Los modelos anidados son donde el fallo apareció.

    `FollowUpTask` y `Commitment` viven dentro de listas, y un `$ref` sin
    resolver o un `required` incompleto ahí dentro no se ve desde la raíz.
    """
    schema = json_schema_for(model_cls)

    for obj in _walk_objects(schema):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj.get("properties") or {})


@pytest.mark.parametrize("model_cls", ALL_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_no_unresolved_references_reach_the_provider(
    model_cls: type[BaseModel],
) -> None:
    """`$ref` y `$defs` no están admitidos: llegarían como error 400."""
    rendered = repr(json_schema_for(model_cls))
    assert "$ref" not in rendered
    assert "$defs" not in rendered


@pytest.mark.parametrize("model_cls", ALL_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_every_field_tells_the_model_what_it_is(model_cls: type[BaseModel]) -> None:
    """Todo campo lleva descripción.

    Con salida estructurada el esquema es lo que restringe la generación; el
    ejemplo JSON del prompt es prosa que el modelo puede o no seguir. Un campo
    sin `description` le llega como «pon una cadena aquí», y eso produjo una
    respuesta real en la que `answer` valía literalmente `"placeholder"` ante
    una pregunta perfectamente contestable.

    Se exceptúan los campos cuyo nombre ya es la descripción completa y que
    forman parte de un `enum`, donde los valores admitidos dicen todo lo que hay
    que saber.
    """
    schema = json_schema_for(model_cls)
    sin_descripcion = [
        name
        for name, field in (schema.get("properties") or {}).items()
        if not field.get("description") and "enum" not in field
    ]
    assert not sin_descripcion, (
        f"{model_cls.__name__}: campos sin descripción para el modelo: "
        f"{sin_descripcion}"
    )


def test_promotional_pricing_applies_and_expires_on_its_own() -> None:
    """El coste que la auditoría presenta como real tiene que serlo.

    Sonnet 5 tiene precio de lanzamiento hasta el 31-08-2026. Cobrarle el precio
    estándar hoy sobreestima un 50%; escribir el promocional a pelo lo
    subestimaría a partir de septiembre. El precio se resuelve por fecha para
    que ninguna de las dos cosas ocurra sin que nadie lo toque.
    """
    from datetime import date

    from app.agent.provider import LLMUsage, capabilities_for

    caps = capabilities_for("claude-sonnet-5")
    assert caps.promotional is not None

    uso = LLMUsage(input_tokens=1_000_000, output_tokens=1_000_000)

    ultimo_dia_promo = uso.cost_eur(caps, on=caps.promotional.until)
    primer_dia_estandar = uso.cost_eur(
        caps, on=caps.promotional.until + timedelta(days=1)
    )

    assert ultimo_dia_promo < primer_dia_estandar, (
        "el precio promocional debe ser más barato que el estándar"
    )
    # 2 + 10 USD promocional frente a 3 + 15 estándar, convertido a euros.
    assert ultimo_dia_promo == round(12.0 * 0.92, 6)
    assert primer_dia_estandar == round(18.0 * 0.92, 6)

    # Un modelo sin promoción cobra lo mismo cualquier día.
    haiku = capabilities_for("claude-haiku-4-5")
    assert haiku.promotional is None
    assert uso.cost_eur(haiku, on=date(2026, 1, 1)) == uso.cost_eur(
        haiku, on=date(2027, 1, 1)
    )


def test_cache_reads_are_not_billed_as_full_input() -> None:
    """Contar la caché como entrada normal sobreestimaría sistemáticamente.

    Importa en el simulador, donde el prompt de sistema se repite en cada turno
    y la mayor parte de la entrada acaba siendo lectura de caché.
    """
    from app.agent.provider import LLMUsage, capabilities_for

    caps = capabilities_for("claude-sonnet-5")

    todo_fresco = LLMUsage(input_tokens=100_000).cost_eur(caps)
    todo_cacheado = LLMUsage(cache_read_tokens=100_000).cost_eur(caps)

    assert todo_cacheado < todo_fresco / 5, (
        "la lectura de caché debe costar una fracción de la entrada normal"
    )


def test_every_agent_task_has_a_registered_schema() -> None:
    """Una tarea sin esquema declarado no debe poder existir en silencio."""
    from app.agent.tools.registry import TASK_TOOLSETS

    for task in TASK_TOOLSETS:
        assert task in SCHEMAS, f"la tarea '{task}' no tiene esquema de salida"
