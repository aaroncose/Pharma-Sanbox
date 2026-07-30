"""Sesiones de base de datos y anclaje del tenant.

Este módulo es el punto donde el aislamiento multi-tenant deja de ser una
convención de código y pasa a estar impuesto por el motor de base de datos.

Modelo de seguridad
───────────────────
1. La aplicación se conecta con un rol (`pharma_app`) que **no** es superusuario
   y **no** tiene `BYPASSRLS`. Sin esto, PostgreSQL ignora las políticas RLS y
   el aislamiento sería puramente decorativo.
2. Cada transacción fija tres variables de sesión mediante `set_config(..., true)`,
   que es equivalente a `SET LOCAL` pero acepta parámetros vinculados y por tanto
   no es inyectable:
       app.tenant_id · app.user_id · app.role
3. Las políticas RLS definidas en las migraciones comparan la columna `tenant_id`
   de cada fila contra `current_setting('app.tenant_id')`.
4. `set_config(..., is_local=true)` limita el alcance a la transacción actual, de
   modo que una conexión devuelta al pool nunca arrastra el tenant anterior.

Consecuencia práctica: aunque un endpoint olvide filtrar por tenant, o un
atacante manipule un ID en la URL, la consulta devuelve cero filas.
La capa de aplicación traduce ese vacío a `ACCESS_DENIED_CROSS_TENANT`.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# ── Motores ──────────────────────────────────────────────────────────────────
# `engine` es el de la aplicación: rol restringido, sujeto a RLS.
engine: Engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.db_echo,
    # `application_name` hace que los intentos bloqueados sean identificables
    # en pg_stat_activity y en los logs del servidor.
    connect_args={"application_name": "pharma-sandbox-api"},
)

SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_privileged_engine() -> Engine:
    """Motor con el rol propietario. Solo para migraciones y seeds.

    Nunca debe usarse para servir peticiones: el propietario de las tablas evita
    RLS por defecto en PostgreSQL.
    """
    return create_engine(
        settings.migration_database_url,
        pool_pre_ping=True,
        connect_args={"application_name": "pharma-sandbox-migrations"},
    )


# ── Contexto de tenant ───────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Identidad efectiva de una transacción.

    `tenant_id` en None representa el ámbito de plataforma (superadministrador).
    En ese caso las políticas RLS conceden acceso solo a las tablas marcadas
    explícitamente como administrables a nivel de plataforma; nunca a contenido
    comercial de un cliente.
    """

    tenant_id: str | None
    user_id: str | None
    role: str


PLATFORM_CONTEXT = TenantContext(tenant_id=None, user_id=None, role="platform_superadmin")
SYSTEM_CONTEXT = TenantContext(tenant_id=None, user_id=None, role="system")


def apply_tenant_context(session: Session, ctx: TenantContext) -> None:
    """Fija el contexto en la transacción actual.

    Se usa `set_config(name, value, true)` en lugar de `SET LOCAL nombre = valor`
    porque la segunda forma no admite parámetros vinculados y obligaría a
    interpolar valores en SQL.
    """
    session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true),"
            "       set_config('app.user_id',   :user_id,   true),"
            "       set_config('app.role',      :role,      true)"
        ),
        {
            # `set_config` requiere texto; '' representa "sin valor" y las
            # políticas RLS lo tratan como ausencia, no como comodín.
            "tenant_id": ctx.tenant_id or "",
            "user_id": ctx.user_id or "",
            "role": ctx.role,
        },
    )


@contextlib.contextmanager
def tenant_session(ctx: TenantContext) -> Iterator[Session]:
    """Abre una sesión con el contexto de tenant ya aplicado.

    El contexto se fija dentro de la transacción y desaparece al cerrarla, de
    forma que una conexión reciclada por el pool no hereda el tenant anterior.
    """
    session = SessionFactory()
    try:
        session.begin()
        apply_tenant_context(session, ctx)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── Verificación de arranque ─────────────────────────────────────────────────


class RlsMisconfiguredError(RuntimeError):
    """El rol de aplicación puede saltarse RLS. Arrancar sería inseguro."""


def assert_rls_enforced() -> dict[str, Any]:
    """Comprueba en el arranque que RLS puede aplicarse de verdad.

    Es una salvaguarda contra el fallo más silencioso y más grave de este
    diseño: conectar la API con un rol privilegiado. En ese escenario todo
    funciona, todas las pruebas de humo pasan, y el aislamiento entre clientes
    simplemente no existe.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS role_name,"
                "       rolsuper     AS is_superuser,"
                "       rolbypassrls AS bypasses_rls"
                "  FROM pg_roles WHERE rolname = current_user"
            )
        ).mappings().one()

    if row["is_superuser"] or row["bypasses_rls"]:
        raise RlsMisconfiguredError(
            f"El rol '{row['role_name']}' es superusuario o tiene BYPASSRLS. "
            "Row-Level Security no se aplicaría y el aislamiento entre tenants "
            "sería ficticio. Configura DATABASE_URL con un rol restringido."
        )
    return dict(row)
