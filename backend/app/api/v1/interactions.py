"""Interacciones con profesionales sanitarios.

Este router es corto a propósito: existe en esta fase porque es el escenario
literal del enunciado del proyecto —un usuario de NovaPharma pidiendo el
identificador de una interacción de BioHealth— y por tanto es donde se
demuestra el `403 ACCESS_DENIED_CROSS_TENANT` de extremo a extremo.

Obsérvese que ninguna consulta filtra por tenant. No es un descuido: es la
demostración. El aislamiento lo aplica PostgreSQL, y `fetch_scoped_one` traduce
el conjunto vacío en denegación auditada.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, rate_limit, require
from app.core.permissions import INTERACTION_READ
from app.services.access import fetch_scoped_one

router = APIRouter(prefix="/interactions", tags=["interactions"])


@router.get(
    "",
    dependencies=[Depends(require(INTERACTION_READ)), Depends(rate_limit())],
)
def list_interactions(
    session: TenantSession,
    hcp_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> dict[str, Any]:
    # El filtro opcional se expresa dentro del SQL en vez de componiendo la
    # cláusula en Python. Cuesta una línea más y elimina por completo la
    # construcción dinámica de consultas: en un proyecto cuyo argumento es la
    # seguridad, no debería haber ni un solo punto donde alguien tenga que
    # razonar si una interpolación es segura.
    rows = session.execute(
        text(
            "SELECT i.id, i.occurred_at, i.channel, i.topics, i.summary, "
            "       i.open_questions, h.full_name AS hcp_name, p.name AS product_name "
            "  FROM interactions i "
            "  JOIN healthcare_professionals h ON h.id = i.hcp_id "
            "  LEFT JOIN products p ON p.id = i.product_id "
            " WHERE i.deleted_at IS NULL "
            "   AND (CAST(:hcp_id AS uuid) IS NULL OR i.hcp_id = CAST(:hcp_id AS uuid)) "
            " ORDER BY i.occurred_at DESC LIMIT :limit"
        ),
        {"limit": limit, "hcp_id": hcp_id},
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get(
    "/{interaction_id}",
    dependencies=[Depends(require(INTERACTION_READ)), Depends(rate_limit())],
)
def get_interaction(
    interaction_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Recupera una interacción o deniega.

    La respuesta es idéntica —403 `ACCESS_DENIED_CROSS_TENANT`— tanto si la
    interacción pertenece a otra organización como si el identificador no
    existe. Distinguirlas con un 404 permitiría enumerar identificadores
    válidos ajenos sin leer ni un campo, que ya es una fuga.
    """
    row = fetch_scoped_one(
        session,
        principal,
        table="interactions",
        resource_id=interaction_id,
        columns="id, hcp_id, user_id, product_id, occurred_at, channel, topics, "
                "summary, open_questions, created_at",
        extra_where="deleted_at IS NULL",
        resource_type="interaction",
    )
    return row
