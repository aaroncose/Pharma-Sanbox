"""Punto de entrada de la API.

Composición del monolito modular: aquí se montan los routers, los manejadores
de error y el middleware transversal. La lógica vive en `app/services`,
`app/agent` y `app/policies`; este fichero solo cablea.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import settings
from app.core.errors import DomainError
from app.core.logging import configure_logging, get_logger
from app.db.session import assert_rls_enforced

configure_logging(settings.log_level, json_output=settings.is_production_like)
log = get_logger("api")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Arranque y parada.

    La comprobación de RLS es deliberadamente bloqueante: si el rol de base de
    datos puede saltarse las políticas, el aislamiento entre clientes no existe
    y arrancar sería peor que no arrancar.
    """
    role_info = assert_rls_enforced()
    log.info(
        "startup",
        env=settings.app_env,
        db_role=role_info["role_name"],
        rls_enforced=True,
        llm_provider="anthropic" if settings.llm_uses_real_provider else "mock",
    )
    yield
    log.info("shutdown")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "Entorno de demostración con datos exclusivamente sintéticos. "
        "DEMO ENVIRONMENT — SYNTHETIC DATA ONLY."
    ),
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production_like else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-Id"],
    expose_headers=["X-Request-Id"],
)

if settings.is_production_like:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*.avatia.demo"])


# ── Middleware de trazabilidad ───────────────────────────────────────────────


@app.middleware("http")
async def trace_requests(
    request: Request, call_next: Callable[[Request], Awaitable[JSONResponse]]
) -> JSONResponse:
    """Asigna un identificador de traza a cada petición.

    El mismo identificador viaja al log de auditoría, a las trazas del agente y
    a la cabecera de respuesta, de modo que desde un evento de la interfaz se
    puede reconstruir la cadena completa de decisiones.
    """
    request_id = request.headers.get("X-Request-Id") or f"tr_{uuid.uuid4().hex[:12]}"
    structlog.contextvars.bind_contextvars(request_id=request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.unbind_contextvars("request_id")
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-Id"] = request_id
    log.info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        latency_ms=elapsed_ms,
    )
    return response


# ── Manejadores de error ─────────────────────────────────────────────────────


@app.exception_handler(DomainError)
async def handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
    if exc.security_event:
        # Un intento bloqueado es información de seguridad, no ruido.
        # El registro persistente en `audit_log` lo hace el servicio de
        # auditoría; aquí queda la traza operativa inmediata.
        log.warning(
            "security_event",
            code=exc.code,
            path=request.url.path,
            method=request.method,
            details=exc.details,
        )
    return JSONResponse(status_code=exc.status_code, content=exc.to_payload())


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_FAILED",
            "message": "Los datos enviados no son válidos",
            "details": {"errors": exc.errors()},
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Último recurso.

    Nunca se filtra la excepción original al cliente: un stack trace puede
    revelar estructura interna, nombres de tabla o datos. El detalle queda en
    el log, correlacionado por identificador de traza.
    """
    log.exception("unhandled_error", path=request.url.path, error=type(exc).__name__)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "Error interno. El incidente ha quedado registrado",
        },
    )


# ── Salud ────────────────────────────────────────────────────────────────────


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """Vivo. No comprueba dependencias: sirve para el supervisor de procesos."""
    return {"status": "ok", "environment": "DEMO — SYNTHETIC DATA ONLY"}


@app.get("/readyz", tags=["system"])
async def readyz() -> dict[str, object]:
    """Listo para recibir tráfico. Comprueba base de datos y RLS."""
    role_info = assert_rls_enforced()
    return {
        "status": "ready",
        "database": "ok",
        "rls_enforced": True,
        "db_role": role_info["role_name"],
        "llm_provider": "anthropic" if settings.llm_uses_real_provider else "mock",
    }
