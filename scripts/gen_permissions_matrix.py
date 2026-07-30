#!/usr/bin/env python
"""Genera `docs/permissions-matrix.md` desde el código.

La matriz es un entregable del proyecto. Escribirla a mano garantiza que
acabe describiendo permisos distintos de los que el sistema aplica, y un
documento de seguridad que miente es peor que no tenerlo.

    make docs      # o: backend/.venv/bin/python scripts/gen_permissions_matrix.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.permissions import ALL_PERMISSIONS, ROLE_PERMISSIONS  # noqa: E402

ROLE_LABELS = {
    "platform_superadmin": "Superadmin plataforma",
    "org_admin": "Admin organización",
    "compliance_officer": "Compliance",
    "sales_rep": "Comercial",
    "auditor": "Auditor",
}

GROUP_LABELS = {
    "platform": "Plataforma",
    "user": "Usuarios",
    "product": "Productos",
    "document": "Biblioteca documental",
    "hcp": "Profesionales sanitarios",
    "interaction": "Interacciones",
    "task": "Tareas",
    "briefing": "Briefings",
    "chat": "Asistente documental",
    "simulation": "Simulador",
    "summary": "Resumen posterior",
    "review": "Revisión humana",
    "policy": "Políticas",
    "audit": "Auditoría",
    "trace": "Trazas",
    "eval": "Evaluaciones",
    "failure_lab": "Failure Lab",
}

HEADER = """# Matriz de roles y permisos

> Generado automáticamente desde `backend/app/core/permissions.py`.
> No editar a mano: ejecutar `make docs`.

El backend comprueba estos permisos en cada endpoint mediante la dependencia
`require(...)`. La interfaz recibe la lista de permisos del usuario al iniciar
sesión y la usa para ocultar lo que no procede, pero eso es una comodidad de
presentación: **ocultar un botón no es un control de acceso**. La comprobación
que cuenta es la del servidor.

Criterio general: **denegar por defecto**. Un permiso que no aparezca marcado
para un rol se rechaza, y un endpoint que olvide declarar el suyo queda
inaccesible en lugar de quedar abierto.

## Decisiones de diseño

**El superadministrador de plataforma no es un comodín.** Crea organizaciones y
administra modelos; no puede leer documentos, interacciones, briefings ni la
cola de revisión de ningún cliente. No es solo una restricción de la capa de
aplicación: las políticas RLS tampoco le conceden acceso al contenido comercial.

**El auditor no genera nada.** Tiene la lectura más amplia del sistema y ningún
permiso de escritura ni de invocación del agente. Un rol de solo lectura capaz
de lanzar generaciones consumiría presupuesto, escribiría trazas y podría
producir contenido que alguien tendría que revisar.

**Compliance revisa, no produce.** Decide sobre contenido generado y aprueba
documentos, pero no crea briefings ni resúmenes: quien revisa no debería ser
también quien genera.

**El administrador de organización sube documentos pero no los aprueba.** Es la
separación que impide que una sola persona introduzca material no validado y lo
deje disponible para el agente.

"""


def group_of(permission: str) -> str:
    return permission.split(".", 1)[0]


def main() -> int:
    roles = list(ROLE_PERMISSIONS)
    lines = [HEADER, "## Matriz\n"]

    lines.append("| Permiso | " + " | ".join(ROLE_LABELS[r] for r in roles) + " |")
    lines.append("|---|" + "|".join([":---:"] * len(roles)) + "|")

    grouped: dict[str, list[str]] = {}
    for permission in sorted(ALL_PERMISSIONS):
        grouped.setdefault(group_of(permission), []).append(permission)

    for group in sorted(grouped, key=lambda g: list(GROUP_LABELS).index(g)):
        label = GROUP_LABELS.get(group, group)
        lines.append(f"| **{label}** | " + " | ".join([""] * len(roles)) + " |")
        for permission in grouped[group]:
            marks = [
                "✅" if permission in ROLE_PERMISSIONS[role] else "—" for role in roles
            ]
            lines.append(f"| `{permission}` | " + " | ".join(marks) + " |")

    lines.append("\n## Recuento\n")
    lines.append("| Rol | Permisos |")
    lines.append("|---|---:|")
    for role in roles:
        lines.append(f"| {ROLE_LABELS[role]} | {len(ROLE_PERMISSIONS[role])} |")
    lines.append(f"| **Total definidos** | **{len(ALL_PERMISSIONS)}** |")
    lines.append("")

    target = ROOT / "docs" / "permissions-matrix.md"
    target.write_text("\n".join(lines), encoding="utf-8")
    print(f"escrito {target.relative_to(ROOT)} ({len(ALL_PERMISSIONS)} permisos, "
          f"{len(roles)} roles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
