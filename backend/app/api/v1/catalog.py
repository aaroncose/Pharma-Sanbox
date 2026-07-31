"""Catálogo: profesionales sanitarios, productos y usuarios.

Lecturas simples que alimentan los selectores de la interfaz. Existen como
router propio y no repartidas entre los módulos que las usan porque son
transversales: el mismo listado de profesionales lo necesitan el briefing, el
simulador y el resumen de visita.

Los profesionales devuelven `consent_data_analysis`. No es un detalle de
implementación filtrado a la interfaz: es lo que permite avisar **antes** de
generar de que el briefing será más pobre porque ese profesional no ha
consentido el análisis de datos. Sin ese aviso, el usuario recibe un resultado
peor sin saber por qué y concluye que el sistema funciona mal.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import TenantSession, rate_limit, require
from app.core.permissions import HCP_READ, PRODUCT_READ, USER_READ

router = APIRouter(tags=["catalog"])


@router.get(
    "/hcps",
    dependencies=[Depends(require(HCP_READ)), Depends(rate_limit())],
)
def list_hcps(
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT h.id, h.full_name, h.specialty, h.institution, h.city, "
            "       h.consent_contact, h.consent_data_analysis, "
            "       (SELECT count(*) FROM interactions i "
            "         WHERE i.hcp_id = h.id AND i.deleted_at IS NULL) "
            "         AS interaction_count "
            "  FROM healthcare_professionals h "
            " WHERE h.deleted_at IS NULL "
            " ORDER BY h.full_name LIMIT :limit"
        ),
        {"limit": limit},
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get(
    "/products",
    dependencies=[Depends(require(PRODUCT_READ)), Depends(rate_limit())],
)
def list_products(session: TenantSession) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT p.id, p.code, p.name, p.therapeutic_area, p.description, "
            "       (SELECT count(*) FROM citable_documents d "
            "         WHERE d.product_id = p.id) AS citable_documents "
            "  FROM products p WHERE p.is_active = true ORDER BY p.name"
        )
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get(
    "/users",
    dependencies=[Depends(require(USER_READ)), Depends(rate_limit())],
)
def list_users(session: TenantSession) -> dict[str, Any]:
    """Usuarios de la organización. Sin hash de contraseña, evidentemente.

    Las columnas se enumeran en lugar de usar `*` por eso mismo: con `*`, añadir
    una columna sensible a la tabla la publicaría en este endpoint sin que nadie
    tomara la decisión de publicarla.
    """
    rows = session.execute(
        text(
            "SELECT id, email, full_name, role::text AS role, status::text AS status, "
            "       last_login_at, created_at "
            "  FROM users ORDER BY full_name"
        )
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}
