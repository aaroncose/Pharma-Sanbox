"""Failure Lab: escenarios de fallo controlados."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, rate_limit, require
from app.core.errors import NotFoundError
from app.core.permissions import FAILURE_LAB_READ, FAILURE_LAB_RUN
from app.schemas.quality import FailureRunRequest
from app.services import failure_lab

router = APIRouter(prefix="/failure-lab", tags=["failure-lab"])


@router.get(
    "",
    dependencies=[Depends(require(FAILURE_LAB_READ)), Depends(rate_limit())],
)
def list_scenarios(session: TenantSession) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT s.id, s.slug, s.name, s.description, s.expectation, s.ordinal, "
            "       r.passed AS last_passed, r.executed_at AS last_executed_at, "
            "       r.trace_id AS last_trace_id, r.id AS last_run_id "
            "  FROM failure_scenarios s "
            "  LEFT JOIN LATERAL ( "
            "       SELECT id, passed, executed_at, trace_id FROM failure_runs "
            "        WHERE scenario_id = s.id ORDER BY executed_at DESC LIMIT 1 "
            "  ) r ON true "
            " ORDER BY s.ordinal"
        )
    ).mappings().all()

    items = [dict(row) for row in rows]
    executed = [i for i in items if i["last_passed"] is not None]

    return {
        "items": items,
        "count": len(items),
        "executed": len(executed),
        "passed": sum(1 for i in executed if i["last_passed"]),
        "failed": sum(1 for i in executed if not i["last_passed"]),
    }


@router.get(
    "/runs",
    dependencies=[Depends(require(FAILURE_LAB_READ)), Depends(rate_limit())],
)
def list_runs(
    session: TenantSession,
    slug: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    rows = session.execute(
        text(
            "SELECT r.id, r.trace_id, r.passed, r.request, r.result, "
            "       r.executed_at, r.audit_log_id, s.slug, s.name "
            "  FROM failure_runs r "
            "  JOIN failure_scenarios s ON s.id = r.scenario_id "
            " WHERE (CAST(:slug AS text) IS NULL OR s.slug = :slug) "
            " ORDER BY r.executed_at DESC LIMIT :limit"
        ),
        {"slug": slug, "limit": limit},
    ).mappings().all()

    return {"items": [dict(row) for row in rows], "count": len(rows)}


@router.post(
    "/run",
    dependencies=[Depends(require(FAILURE_LAB_RUN)), Depends(rate_limit("failure_lab", 10))],
)
def run_scenario(
    payload: FailureRunRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    try:
        outcome = failure_lab.execute(session, principal, payload.slug)
    except KeyError as unknown:
        raise NotFoundError("No existe ese escenario") from unknown

    return {
        "slug": outcome.slug,
        "passed": outcome.passed,
        "trace_id": outcome.trace_id,
        "request": outcome.request,
        "result": outcome.result,
        "audit_log_id": outcome.audit_log_id,
    }
