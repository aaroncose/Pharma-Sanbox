"""Matriz de roles y permisos.

Es la definición canónica: la documentación de `docs/permissions-matrix.md` se
genera desde aquí, de modo que no puedan divergir. Un documento que describe
permisos distintos de los que aplica el código es peor que no tener documento.

Principios
──────────
**Denegar por defecto.** `has_permission` devuelve False para cualquier permiso
que no esté listado explícitamente. Añadir un endpoint sin declarar su permiso
lo deja inaccesible para todos, que es el fallo correcto: se detecta en la
primera prueba, en lugar de quedar abierto.

**El superadministrador de plataforma no es un comodín.** Puede crear
organizaciones y administrar modelos, y no puede leer documentos, interacciones
ni salidas del agente de ningún cliente. Esto no es una convención de la capa de
aplicación: las políticas RLS tampoco le conceden acceso a contenido comercial.
Un operador que quiera ver datos de un cliente necesita el procedimiento
extraordinario, que deja rastro.

**El auditor no genera nada.** Tiene la lectura más amplia del sistema y ningún
permiso de escritura ni de invocación del agente. Un rol de solo lectura que
pueda lanzar generaciones no es de solo lectura: consume presupuesto, escribe en
trazas y puede provocar contenido que alguien tendrá que revisar.

**Compliance no usa el agente para trabajo comercial.** Puede leer todo lo
generado y decidir sobre ello, pero no crear briefings: quien revisa no debería
ser también quien produce.
"""

from __future__ import annotations

from typing import Final

# ─────────────────────────────────────────────────────────────────────────────
# Catálogo de permisos
# ─────────────────────────────────────────────────────────────────────────────

# Plataforma
PLATFORM_ORG_MANAGE: Final = "platform.org.manage"
PLATFORM_METRICS_READ: Final = "platform.metrics.read"
PLATFORM_MODELS_MANAGE: Final = "platform.models.manage"

# Organización
USER_READ: Final = "user.read"
USER_MANAGE: Final = "user.manage"
PRODUCT_READ: Final = "product.read"
PRODUCT_ASSIGN: Final = "product.assign"

# Biblioteca documental
DOCUMENT_READ: Final = "document.read"
DOCUMENT_CREATE: Final = "document.create"
DOCUMENT_APPROVE: Final = "document.approve"
DOCUMENT_WITHDRAW: Final = "document.withdraw"

# Actividad comercial
HCP_READ: Final = "hcp.read"
INTERACTION_READ: Final = "interaction.read"
TASK_READ: Final = "task.read"
TASK_MANAGE: Final = "task.manage"

# Agente
BRIEFING_CREATE: Final = "briefing.create"
BRIEFING_READ: Final = "briefing.read"
CHAT_USE: Final = "chat.use"
SIMULATION_USE: Final = "simulation.use"
SUMMARY_CREATE: Final = "summary.create"

# Compliance
REVIEW_READ: Final = "review.read"
REVIEW_DECIDE: Final = "review.decide"
REVIEW_REQUEST: Final = "review.request"
POLICY_READ: Final = "policy.read"
POLICY_MANAGE: Final = "policy.manage"

# Trazabilidad
AUDIT_READ: Final = "audit.read"
AUDIT_EXPORT: Final = "audit.export"
TRACE_READ: Final = "trace.read"

# Calidad
EVAL_READ: Final = "eval.read"
EVAL_RUN: Final = "eval.run"
FAILURE_LAB_READ: Final = "failure_lab.read"
FAILURE_LAB_RUN: Final = "failure_lab.run"

ALL_PERMISSIONS: Final[frozenset[str]] = frozenset(
    {
        PLATFORM_ORG_MANAGE, PLATFORM_METRICS_READ, PLATFORM_MODELS_MANAGE,
        USER_READ, USER_MANAGE, PRODUCT_READ, PRODUCT_ASSIGN,
        DOCUMENT_READ, DOCUMENT_CREATE, DOCUMENT_APPROVE, DOCUMENT_WITHDRAW,
        HCP_READ, INTERACTION_READ, TASK_READ, TASK_MANAGE,
        BRIEFING_CREATE, BRIEFING_READ, CHAT_USE, SIMULATION_USE, SUMMARY_CREATE,
        REVIEW_READ, REVIEW_DECIDE, REVIEW_REQUEST, POLICY_READ, POLICY_MANAGE,
        AUDIT_READ, AUDIT_EXPORT, TRACE_READ,
        EVAL_READ, EVAL_RUN, FAILURE_LAB_READ, FAILURE_LAB_RUN,
    }
)


# ─────────────────────────────────────────────────────────────────────────────
# Asignación por rol
# ─────────────────────────────────────────────────────────────────────────────

ROLE_PERMISSIONS: Final[dict[str, frozenset[str]]] = {
    # Administra la plataforma. Deliberadamente sin acceso a contenido
    # comercial de ningún cliente: ni documentos, ni interacciones, ni salidas
    # del agente, ni la cola de revisión.
    "platform_superadmin": frozenset(
        {
            PLATFORM_ORG_MANAGE,
            PLATFORM_METRICS_READ,
            PLATFORM_MODELS_MANAGE,
            EVAL_READ,
            EVAL_RUN,
            FAILURE_LAB_READ,
        }
    ),
    # Administra su organización. Puede subir documentación pero no aprobarla:
    # la separación entre quien sube material y quien lo aprueba es el control
    # que impide que una sola persona introduzca contenido no validado.
    "org_admin": frozenset(
        {
            USER_READ, USER_MANAGE, PRODUCT_READ, PRODUCT_ASSIGN,
            DOCUMENT_READ, DOCUMENT_CREATE,
            HCP_READ, INTERACTION_READ, TASK_READ,
            POLICY_READ,
            AUDIT_READ, TRACE_READ,
            EVAL_READ, FAILURE_LAB_READ,
            BRIEFING_READ,
        }
    ),
    # Aprueba documentos y decide sobre contenido generado. No produce
    # contenido comercial: quien revisa no debería ser también quien genera.
    "compliance_officer": frozenset(
        {
            DOCUMENT_READ, DOCUMENT_CREATE, DOCUMENT_APPROVE, DOCUMENT_WITHDRAW,
            HCP_READ, INTERACTION_READ,
            BRIEFING_READ,
            REVIEW_READ, REVIEW_DECIDE,
            POLICY_READ, POLICY_MANAGE,
            AUDIT_READ, AUDIT_EXPORT, TRACE_READ,
            USER_READ,
            EVAL_READ, EVAL_RUN,
            FAILURE_LAB_READ, FAILURE_LAB_RUN,
        }
    ),
    # El usuario del producto. Genera contenido y puede pedir revisión, pero no
    # aprobar nada ni modificar políticas.
    "sales_rep": frozenset(
        {
            PRODUCT_READ,
            DOCUMENT_READ,
            HCP_READ, INTERACTION_READ,
            TASK_READ, TASK_MANAGE,
            BRIEFING_CREATE, BRIEFING_READ,
            CHAT_USE, SIMULATION_USE, SUMMARY_CREATE,
            REVIEW_REQUEST,
            POLICY_READ,
        }
    ),
    # Solo lectura. Sin ningún permiso de escritura ni de invocación del
    # agente: un auditor que puede lanzar generaciones no es un auditor.
    "auditor": frozenset(
        {
            DOCUMENT_READ,
            HCP_READ, INTERACTION_READ, TASK_READ,
            BRIEFING_READ,
            REVIEW_READ,
            POLICY_READ,
            AUDIT_READ, AUDIT_EXPORT, TRACE_READ,
            EVAL_READ,
            FAILURE_LAB_READ,
            USER_READ,
        }
    ),
}


def has_permission(role: str, permission: str) -> bool:
    """Denegar por defecto: un rol o permiso desconocido devuelve False."""
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


# Comprobación de integridad al importar: ningún rol puede conceder un permiso
# que no exista en el catálogo. Un permiso mal escrito en `ROLE_PERMISSIONS`
# sería un permiso que nadie tiene y un endpoint inalcanzable, con un error que
# solo aparecería al probar ese endpoint concreto.
for _role, _perms in ROLE_PERMISSIONS.items():
    _unknown = _perms - ALL_PERMISSIONS
    if _unknown:
        raise RuntimeError(f"El rol {_role} declara permisos inexistentes: {sorted(_unknown)}")

# Ningún permiso debe quedar huérfano: si existe en el catálogo pero ningún rol
# lo tiene, o sobra o falta asignarlo.
_orphans = ALL_PERMISSIONS - set().union(*ROLE_PERMISSIONS.values())
if _orphans:
    raise RuntimeError(f"Permisos que ningún rol concede: {sorted(_orphans)}")
