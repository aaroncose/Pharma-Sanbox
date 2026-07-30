"""Fixtures compartidas.

Las pruebas se ejecutan contra la base de datos de desarrollo ya cargada con
datos sintéticos. No se usa una base efímera por prueba a propósito: lo que hay
que verificar es el comportamiento de las políticas RLS reales sobre el conjunto
de datos real de la demostración, no sobre un montaje simplificado que podría no
reproducir el problema.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.db.session import (
    SessionFactory,
    TenantContext,
    apply_tenant_context,
    create_privileged_engine,
)


@pytest.fixture(scope="session")
def privileged_conn() -> Iterator[Connection]:
    """Conexión con el rol propietario. Solo para preparar y comprobar datos.

    Nunca se usa para ejercitar el comportamiento bajo prueba: el propietario no
    está sujeto a RLS y cualquier verificación hecha con él sería vacía.
    """
    engine = create_privileged_engine()
    with engine.connect() as conn:
        yield conn


@pytest.fixture(scope="session")
def tenant_ids(privileged_conn: Connection) -> dict[str, str]:
    rows = privileged_conn.execute(text("SELECT slug, id FROM tenants")).all()
    ids = {slug: str(tid) for slug, tid in rows}
    assert "nph_01" in ids and "bhl_01" in ids, (
        "La base de datos no tiene los datos de demostración. "
        "Ejecuta 'make reset-db' antes de las pruebas."
    )
    return ids


def _session_as(ctx: TenantContext) -> Session:
    session = SessionFactory()
    session.begin()
    apply_tenant_context(session, ctx)
    return session


@pytest.fixture
def novapharma_session(tenant_ids: dict[str, str]) -> Iterator[Session]:
    """Sesión de aplicación con el contexto de NovaPharma."""
    session = _session_as(
        TenantContext(tenant_id=tenant_ids["nph_01"], user_id=None, role="sales_rep")
    )
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def biohealth_session(tenant_ids: dict[str, str]) -> Iterator[Session]:
    session = _session_as(
        TenantContext(tenant_id=tenant_ids["bhl_01"], user_id=None, role="sales_rep")
    )
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def anonymous_session() -> Iterator[Session]:
    """Sesión sin tenant fijado. Simula un fallo de la capa de autenticación."""
    session = _session_as(TenantContext(tenant_id=None, user_id=None, role="anonymous"))
    try:
        yield session
    finally:
        session.rollback()
        session.close()
