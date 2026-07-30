"""Aplicación del esquema.

Este proyecto usa un fichero SQL único y versionado en lugar de una cadena de
migraciones incrementales. Es una decisión consciente para un sandbox de
demostración: el esquema completo se lee de una sentada, las políticas RLS están
junto a las tablas que protegen, y `make reset-db` reconstruye el estado exacto.

Alternativa descartada: Alembic con revisiones incrementales. Es lo correcto en
un producto con datos en producción que hay que migrar sin pérdida, y por eso la
dependencia está declarada y `docs/adr/0003` explica cuándo se haría el cambio.
Aquí habría añadido ruido sin aportar nada: no hay datos que preservar.

Se ejecuta con el rol propietario (`pharma_owner`), nunca con el de la API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.db.session import create_privileged_engine

SCHEMA_PATH = Path(__file__).parent / "schema.sql"

log = get_logger("migrate")


def drop_schema() -> None:
    """Reconstruye el esquema `public` desde cero.

    Destructivo por diseño y solo alcanzable con `--drop`. La base de datos de
    este proyecto contiene únicamente datos sintéticos regenerables.
    """
    engine = create_privileged_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # El propietario del esquema debe seguir siendo el rol de migraciones:
        # de él heredan los DEFAULT PRIVILEGES que dan acceso a `pharma_app`.
        conn.execute(text("ALTER SCHEMA public OWNER TO pharma_owner"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO pharma_app"))
    log.info("schema_dropped")


def apply_schema() -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    engine = create_privileged_engine()
    # Una única transacción: si algo falla, no queda un esquema a medias con
    # tablas creadas y políticas RLS sin crear, que es el peor estado posible.
    with engine.begin() as conn:
        # Se ejecuta contra la conexión del driver sin pasar parámetros.
        # El esquema contiene `format('%I', ...)` dentro de bloques DO, y
        # psycopg interpretaría esos `%` como marcadores de posición si se le
        # entregara una secuencia de parámetros, aunque estuviera vacía.
        conn.connection.driver_connection.execute(sql)
    log.info("schema_applied", statements_file=str(SCHEMA_PATH))


def verify() -> int:
    """Comprueba que ninguna tabla con `tenant_id` quedó sin proteger.

    Devuelve el número de tablas desprotegidas. Distinto de cero es un fallo de
    migración, no una advertencia.
    """
    engine = create_privileged_engine()
    with engine.connect() as conn:
        gaps = conn.execute(text("SELECT * FROM assert_rls_coverage()")).mappings().all()
        tables = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
        ).scalar_one()
        policies = conn.execute(text("SELECT count(*) FROM pg_policy")).scalar_one()

    if gaps:
        for gap in gaps:
            log.error(
                "rls_gap",
                table=gap["table_name"],
                rls_enabled=gap["rls_enabled"],
                policies=gap["policy_count"],
            )
        return len(gaps)

    log.info("rls_coverage_ok", tables=tables, policies=policies)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Aplica el esquema y las políticas RLS")
    parser.add_argument(
        "--drop",
        action="store_true",
        help="Elimina el esquema completo antes de aplicarlo. Destructivo.",
    )
    args = parser.parse_args()

    configure_logging("INFO")

    if args.drop:
        drop_schema()

    apply_schema()
    return verify()


if __name__ == "__main__":
    sys.exit(main())
