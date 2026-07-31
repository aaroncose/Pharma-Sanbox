"""CLI de la suite de evaluación: `make eval`.

Corre fuera de la API, con su propia sesión y el contexto de tenant fijado a
mano. Devuelve código de salida 1 si alguna métrica incumple su objetivo, para
que pueda usarse como puerta de calidad en integración continua.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

from app.core.logging import configure_logging, get_logger
from app.db.session import SessionFactory, TenantContext, apply_tenant_context
from app.evals import runner as suite

log = get_logger("evals.cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Suite de evaluación del agente")
    parser.add_argument("--dataset", default=suite.DATASET_SLUG)
    parser.add_argument(
        "--prompt-version",
        default=None,
        help="Versión a evaluar. Por defecto, la activa.",
    )
    parser.add_argument(
        "--tenant",
        default=suite.TARGET_CORPUS,
        help="Organización sobre cuyo corpus se ejecuta.",
    )
    parser.add_argument(
        "--real-provider",
        action="store_true",
        help="Llama al modelo real una vez por caso. Tiene coste.",
    )
    args = parser.parse_args()

    if args.dataset != suite.DATASET_SLUG:
        log.error("dataset_desconocido", requested=args.dataset)
        return 2

    configure_logging("INFO")
    session = SessionFactory()

    try:
        session.begin()
        apply_tenant_context(session, TenantContext(None, None, "platform_superadmin"))

        tenant = session.execute(
            text("SELECT id, name FROM tenants WHERE name = :n"),
            {"n": args.tenant},
        ).mappings().first()

        if tenant is None:
            log.error("tenant_no_encontrado", tenant=args.tenant)
            return 2

        admin = session.execute(
            text(
                "SELECT id FROM users WHERE tenant_id = :t "
                " AND role = 'compliance_officer' LIMIT 1"
            ),
            {"t": tenant["id"]},
        ).scalar()

        apply_tenant_context(
            session,
            TenantContext(
                tenant_id=str(tenant["id"]),
                user_id=str(admin) if admin else None,
                role="compliance_officer",
            ),
        )

        result = suite.run_suite(
            session,
            tenant_id=str(tenant["id"]),
            prompt_version=args.prompt_version,
            force_mock=not args.real_provider,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    targets = result.metrics.get("targets_met", {})
    failed_targets = [key for key, ok in targets.items() if not ok]

    print(f"\n  {result.prompt_name}.{result.prompt_version}  ·  {result.provider}")
    skipped = sum(1 for v in result.verdicts if v.skipped)
    scored = len(result.verdicts) - skipped
    print(f"  {result.passed}/{scored} casos superados", end="")
    print(f"  ·  {skipped} omitidos\n" if skipped else "\n")

    for key, target in suite.TARGETS.items():
        if key not in result.metrics:
            continue
        ok = targets.get(key)
        mark = " " if ok is None else ("✓" if ok else "✕")
        print(f"  {mark} {target['label']:<28} {result.metrics[key]}")

    for verdict in result.verdicts:
        if verdict.skipped:
            print(f"\n  ~ {verdict.ref}: omitido")
        elif not verdict.passed:
            print(f"\n  ✕ {verdict.ref}: {verdict.failure_note}")

    if not result.metrics.get("corpus_match", True):
        print(
            f"\n  Aviso: ejecutado sobre {result.metrics['executed_for_tenant']}, "
            f"los casos apuntan a {result.metrics['target_corpus']}."
        )

    print()
    return 1 if failed_targets else 0


if __name__ == "__main__":
    sys.exit(main())
