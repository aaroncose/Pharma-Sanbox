"""Suite de evaluación: consulta de resultados y ejecución.

La lectura y la ejecución están separadas por permiso porque son operaciones de
naturaleza distinta. Ver los resultados es barato y lo puede hacer cualquiera con
`eval.read`, incluido el auditor. Ejecutar la suite invoca al agente veintitrés
veces, y por eso exige `eval.run`, que el auditor no tiene: un rol de solo
lectura que puede lanzar generaciones no es de solo lectura.

El endpoint que da sentido al módulo es `/evals/compare`. Un porcentaje aislado
no dice si el sistema es bueno —¿comparado con qué?—; dos ejecuciones de la misma
suite sobre dos versiones del prompt, en las mismas condiciones, sí dicen si el
cambio mejoró algo.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, rate_limit, require
from app.core.errors import NotFoundError
from app.core.permissions import EVAL_READ, EVAL_RUN
from app.evals import runner as suite
from app.evals.dataset import ALL_CASES, CASE_COUNT, DATASET_SLUG
from app.schemas.quality import EvalCompareRequest, EvalRunRequest

router = APIRouter(prefix="/evals", tags=["evals"])


def _serialize(result: suite.SuiteResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "prompt_name": result.prompt_name,
        "prompt_version": result.prompt_version,
        "model": result.model,
        "provider": result.provider,
        "metrics": result.metrics,
        "cases": [
            {
                "ref": verdict.ref,
                "passed": verdict.passed,
                "score": verdict.score,
                "checks": verdict.checks,
                "failure_note": verdict.failure_note,
                "latency_ms": verdict.latency_ms,
                "cost_eur": verdict.cost_eur,
                "actual": verdict.actual,
            }
            for verdict in result.verdicts
        ],
    }


@router.get(
    "",
    dependencies=[Depends(require(EVAL_READ)), Depends(rate_limit())],
)
def overview(
    session: TenantSession,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> dict[str, Any]:
    """Últimas ejecuciones, con el conjunto y sus objetivos.

    Devuelve los objetivos junto a los resultados en lugar de dejar que la
    interfaz los conozca por su cuenta. Un umbral que vive en el frontend se
    puede bajar hasta que todo salga verde sin que quede rastro; viniendo del
    servidor, cambiarlo es un cambio de código.
    """
    suite.ensure_dataset(session)

    runs = session.execute(
        text(
            "SELECT r.id, r.prompt_name, r.prompt_version, r.model, r.provider, "
            "       r.started_at, r.finished_at, r.metrics "
            "  FROM eval_runs r "
            "  JOIN eval_datasets d ON d.id = r.dataset_id "
            " WHERE d.slug = :slug "
            " ORDER BY r.started_at DESC LIMIT :limit"
        ),
        {"slug": DATASET_SLUG, "limit": limit},
    ).mappings().all()

    return {
        "dataset": {
            "slug": DATASET_SLUG,
            "case_count": CASE_COUNT,
            "categories": sorted({case.category for case in ALL_CASES}),
            "target_corpus": suite.TARGET_CORPUS,
        },
        "targets": suite.TARGETS,
        "runs": [dict(row) for row in runs],
        "count": len(runs),
    }


@router.get(
    "/cases",
    dependencies=[Depends(require(EVAL_READ)), Depends(rate_limit())],
)
def list_cases(session: TenantSession) -> dict[str, Any]:
    """El conjunto en sí.

    Se expone para que se pueda revisar qué se está midiendo. Un conjunto de
    evaluación cuyo contenido no es inspeccionable pide que se confíe en el
    porcentaje, y el porcentaje es exactamente lo que no hay que creerse sin
    ver los casos.
    """
    suite.ensure_dataset(session)
    return {
        "slug": DATASET_SLUG,
        "count": CASE_COUNT,
        "items": [
            {
                "ref": case.ref,
                "category": case.category,
                "question": case.question,
                "expectation": case.expectation,
                "notes": case.notes,
                "product_code": case.product_code,
                "kind": case.kind,
            }
            for case in ALL_CASES
        ],
    }


@router.get(
    "/runs/{run_id}",
    dependencies=[Depends(require(EVAL_READ)), Depends(rate_limit())],
)
def get_run(run_id: str, session: TenantSession) -> dict[str, Any]:
    """Una ejecución con el detalle caso a caso.

    Los casos que fallaron van primero. Es la pantalla donde se viene a mirar
    qué se rompió, y ordenar por referencia obligaría a buscar el rojo entre
    veinticinco filas verdes.
    """
    run = session.execute(
        text(
            "SELECT id, prompt_name, prompt_version, model, provider, "
            "       started_at, finished_at, metrics "
            "  FROM eval_runs WHERE id = CAST(:id AS uuid)"
        ),
        {"id": run_id},
    ).mappings().first()

    if run is None:
        raise NotFoundError("No existe esa ejecución")

    results = session.execute(
        text(
            "SELECT c.ref, c.category, c.notes, c.input, c.expectation, "
            "       r.passed, r.score, r.actual, r.failure_note, r.latency_ms, "
            "       r.cost_eur::double precision AS cost_eur "
            "  FROM eval_results r "
            "  JOIN eval_cases c ON c.id = r.case_id "
            " WHERE r.run_id = CAST(:id AS uuid) "
            " ORDER BY r.passed ASC, c.category, c.ref"
        ),
        {"id": run_id},
    ).mappings().all()

    return {"run": dict(run), "results": [dict(row) for row in results]}


@router.post(
    "/run",
    dependencies=[Depends(require(EVAL_RUN)), Depends(rate_limit("evals", 4))],
)
def execute_suite(
    payload: EvalRunRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Ejecuta la suite completa.

    El límite de tasa es estrecho —cuatro por minuto— y es un cubo propio. Cada
    ejecución son veintitrés invocaciones del agente: compartir presupuesto con
    la navegación dejaría sin poder generar a quien está trabajando mientras
    alguien mira las evaluaciones.
    """
    result = suite.run_suite(
        session,
        tenant_id=principal.tenant_id,
        prompt_version=payload.prompt_version,
        force_mock=payload.force_mock,
    )
    return _serialize(result)


@router.post(
    "/compare",
    dependencies=[Depends(require(EVAL_RUN)), Depends(rate_limit("evals", 2))],
)
def compare_versions(
    payload: EvalCompareRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Ejecuta la suite sobre varias versiones y devuelve las métricas juntas.

    Las ejecuciones son secuenciales y comparten corpus, proveedor y conjunto.
    Es la condición para que la comparación signifique algo: si una versión
    corriera contra el modelo real y otra contra el mock, la diferencia mediría
    el proveedor y no el prompt.

    `deltas` se calcula aquí y no en la interfaz porque la resta necesita saber
    el sentido de cada métrica: en latencia y coste, menos es mejor; en las
    demás, más. Dejar eso al frontend es pedir que alguien pinte de verde una
    subida de coste.
    """
    runs = [
        suite.run_suite(
            session,
            tenant_id=principal.tenant_id,
            prompt_version=version,
            force_mock=payload.force_mock,
        )
        for version in payload.versions
    ]

    lower_is_better = {"mean_latency_ms", "mean_cost_eur", "hallucination_pct",
                       "cross_tenant_leaks", "total_cost_eur"}

    baseline, latest = runs[0], runs[-1]
    deltas: dict[str, Any] = {}
    for key, target in suite.TARGETS.items():
        before = baseline.metrics.get(key)
        after = latest.metrics.get(key)
        if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
            continue
        change = round(after - before, 6)
        deltas[key] = {
            "before": before,
            "after": after,
            "change": change,
            "improved": (change < 0) if key in lower_is_better else (change > 0),
            "label": target.get("label", key),
        }

    return {
        "runs": [_serialize(run) for run in runs],
        "deltas": deltas,
        "baseline_version": baseline.prompt_version,
        "latest_version": latest.prompt_version,
    }
