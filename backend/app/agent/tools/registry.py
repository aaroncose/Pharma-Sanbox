"""Allowlist de herramientas.

La idea central: **una herramienta no autorizada no se rechaza al ejecutarse,
no se le ofrece al modelo en absoluto.**

La diferencia importa. Si se le ofrecen diez herramientas y se rechazan seis al
invocarlas, el modelo intentará usarlas, gastará turnos y la conversación se
llenará de errores. Si solo ve las cuatro que le corresponden, no hay nada que
rechazar. La comprobación en ejecución sigue existiendo, pero como red de
seguridad y detector de incidentes, no como control principal: si el agente
pide una herramienta que nunca se le ofreció, eso no es un error de uso, es una
señal de que algo va mal, y se registra como evento de seguridad.

La lista se resuelve por **rol y por tarea**, no solo por rol. Un comercial
puede crear tareas de seguimiento en el flujo de resumen posterior, pero no
mientras hace una pregunta al asistente documental: la capacidad depende de qué
está haciendo, no solo de quién es.

Las herramientas señuelo (`FORBIDDEN_TOOL_NAMES`) no existen en ninguna parte.
Están declaradas para que pedir cualquiera de ellas sea inequívocamente un
intento de escalada y no un nombre mal escrito.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ToolNotAllowedError
from app.core.logging import get_logger
from app.core.permissions import (
    BRIEFING_CREATE,
    CHAT_USE,
    DOCUMENT_READ,
    HCP_READ,
    INTERACTION_READ,
    REVIEW_REQUEST,
    SIMULATION_USE,
    SUMMARY_CREATE,
    TASK_MANAGE,
    has_permission,
)

log = get_logger("tools")


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    # Permiso que debe tener el rol para que la herramienta se le ofrezca.
    required_permission: str
    # Si es True, la herramienta modifica estado y su uso queda en auditoría
    # con el detalle completo de la llamada.
    mutating: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Catálogo
# ─────────────────────────────────────────────────────────────────────────────

SEARCH_DOCUMENTS = ToolSpec(
    name="search_documents",
    description=(
        "Busca en la documentación aprobada y vigente de la organización. "
        "Devuelve fragmentos con su identificador de documento, versión y "
        "sección. No accede a borradores, material retirado ni caducado."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Qué se busca"},
            "product_code": {
                "type": "string",
                "description": "Código de producto para acotar la búsqueda",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    required_permission=DOCUMENT_READ,
)

GET_HCP_HISTORY = ToolSpec(
    name="get_hcp_history",
    description=(
        "Recupera el historial de interacciones autorizadas con un profesional "
        "sanitario. Solo devuelve datos si el profesional tiene registrado el "
        "consentimiento correspondiente."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "hcp_id": {"type": "string"},
            "limit": {"type": "integer", "description": "Máximo de interacciones"},
        },
        "required": ["hcp_id"],
        "additionalProperties": False,
    },
    required_permission=INTERACTION_READ,
)

GET_HCP_PROFILE = ToolSpec(
    name="get_hcp_profile",
    description="Datos básicos del profesional sanitario: especialidad y centro.",
    input_schema={
        "type": "object",
        "properties": {"hcp_id": {"type": "string"}},
        "required": ["hcp_id"],
        "additionalProperties": False,
    },
    required_permission=HCP_READ,
)

CREATE_DRAFT = ToolSpec(
    name="create_draft",
    description=(
        "Guarda un borrador de contenido generado. Un borrador no es contenido "
        "publicable: requiere confirmación del usuario y, si procede, revisión "
        "de compliance."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["briefing", "meeting_summary"]},
            "title": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["kind", "title", "content"],
        "additionalProperties": False,
    },
    required_permission=BRIEFING_CREATE,
    mutating=True,
)

CREATE_TASK = ToolSpec(
    name="create_task",
    description="Crea una tarea de seguimiento para el comercial que hizo la petición.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "detail": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "due_in_days": {"type": "integer"},
        },
        "required": ["title"],
        "additionalProperties": False,
    },
    required_permission=TASK_MANAGE,
    mutating=True,
)

REQUEST_HUMAN_REVIEW = ToolSpec(
    name="request_human_review",
    description=(
        "Envía contenido a la cola de revisión de compliance. Se usa cuando el "
        "agente no puede sostener una afirmación con documentación aprobada."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "reason": {"type": "string"},
            "content": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
        },
        "required": ["reason", "content"],
        "additionalProperties": False,
    },
    required_permission=REVIEW_REQUEST,
    mutating=True,
)

ALL_TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        SEARCH_DOCUMENTS,
        GET_HCP_HISTORY,
        GET_HCP_PROFILE,
        CREATE_DRAFT,
        CREATE_TASK,
        REQUEST_HUMAN_REVIEW,
    )
}


# ─────────────────────────────────────────────────────────────────────────────
# Composición por tarea
# ─────────────────────────────────────────────────────────────────────────────

# Qué herramientas tiene sentido ofrecer en cada tarea, antes de filtrar por
# permisos. El asistente documental no crea tareas ni borradores: si pudiera,
# una pregunta acabaría escribiendo en la base de datos.
# Permiso necesario para **ejecutar la tarea**, distinto del permiso de cada
# herramienta.
#
# Hueco encontrado por `test_auditor_gets_no_tools_at_all`: el auditor tiene
# `document.read`, así que se le ofrecía `search_documents` en el flujo de chat
# aunque no tenga `chat.use` y no pueda invocar al agente en absoluto. La
# comprobación del endpoint lo habría frenado, pero el harness no debe depender
# de que la capa de arriba se acuerde: si el rol no puede hacer la tarea, no
# recibe ninguna herramienta para ella.
TASK_PERMISSIONS: dict[str, str] = {
    "briefing": BRIEFING_CREATE,
    "chat": CHAT_USE,
    "meeting_summary": SUMMARY_CREATE,
    "simulator": SIMULATION_USE,
    # El verificador es un paso interno del harness, no una acción de usuario.
    "verifier": "",
}

TASK_TOOLSETS: dict[str, tuple[str, ...]] = {
    "briefing": (
        "search_documents",
        "get_hcp_profile",
        "get_hcp_history",
        "create_draft",
        "request_human_review",
    ),
    "chat": ("search_documents", "request_human_review"),
    "meeting_summary": ("search_documents", "create_draft", "create_task"),
    # El simulador interpreta a un profesional sanitario: no tiene acceso a la
    # biblioteca documental a propósito. Un médico real no ha leído el material
    # comercial aprobado, y si lo conociera haría preguntas antinaturalmente
    # alineadas con las respuestas disponibles.
    "simulator": (),
    # El verificador solo juzga el texto que recibe. Darle herramientas le
    # permitiría buscar respaldo que el generador no citó, que es justo lo
    # contrario de lo que debe comprobar.
    "verifier": (),
}

# Nombres que no existen. Pedirlos es un intento de escalada, no una errata.
FORBIDDEN_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "execute_sql",
        "update_permissions",
        "change_role",
        "list_tenants",
        "disable_policy",
        "read_audit_log",
        "delete_document",
        "approve_document",
    }
)


def resolve_allowlist(*, task: str, role: str) -> list[ToolSpec]:
    """Herramientas que se ofrecerán al modelo para esta tarea y este rol.

    La intersección se hace en este orden: primero qué tiene sentido en la
    tarea, después qué permite el rol. Un comercial en el flujo de chat no
    recibe `create_task` aunque tenga el permiso, porque en esa tarea no
    procede.
    """
    if task not in TASK_TOOLSETS:
        # Denegar por defecto: una tarea no declarada no recibe herramientas.
        log.warning("tool_task_not_declared", task=task)
        return []

    task_permission = TASK_PERMISSIONS.get(task, "")
    if task_permission and not has_permission(role, task_permission):
        log.info("tool_task_not_permitted", task=task, role=role)
        return []

    candidates = TASK_TOOLSETS[task]
    return [
        ALL_TOOLS[name]
        for name in candidates
        if name in ALL_TOOLS and has_permission(role, ALL_TOOLS[name].required_permission)
    ]


def to_api_tools(specs: list[ToolSpec]) -> list[dict[str, Any]]:
    """Traduce al formato de definición de herramientas de la API."""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
            # Garantiza que los argumentos validen exactamente contra el
            # esquema en lugar de aproximarse a él.
            "strict": True,
        }
        for spec in specs
    ]


def assert_tool_allowed(
    tool_name: str, allowlist: list[ToolSpec], *, task: str, role: str
) -> ToolSpec:
    """Comprobación en ejecución. Red de seguridad, no control principal.

    Si esto salta, algo ya ha ido mal: el modelo pidió una herramienta que no
    se le ofreció. Se trata como evento de seguridad, no como error de
    validación, y por eso lanza `ToolNotAllowedError`, que el manejador global
    registra como tal.
    """
    allowed = {spec.name for spec in allowlist}
    if tool_name in allowed:
        return next(spec for spec in allowlist if spec.name == tool_name)

    escalation = tool_name in FORBIDDEN_TOOL_NAMES
    log.warning(
        "tool_not_allowed",
        tool=tool_name,
        task=task,
        role=role,
        offered=sorted(allowed),
        looks_like_escalation=escalation,
    )
    raise ToolNotAllowedError(
        details={
            "tool": tool_name,
            "task": task,
            "allowed": sorted(allowed),
            # Distingue "pidió algo que no toca en esta tarea" de "pidió
            # modificar permisos". Lo segundo es mucho más grave.
            "escalation_attempt": escalation,
        }
    )


# ─────────────────────────────────────────────────────────────────────────────
# Ejecución
# ─────────────────────────────────────────────────────────────────────────────

ToolHandler = Callable[..., dict[str, Any]]


@dataclass(slots=True)
class ToolInvocation:
    """Registro de una llamada, para la traza del agente."""

    name: str
    arguments: dict[str, Any]
    status: str
    latency_ms: int
    result_summary: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
