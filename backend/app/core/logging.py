"""Logging estructurado.

Dos decisiones que importan en un entorno regulado:

1. **Todo log es JSON en staging/producción.** Un log que solo se lee con los
   ojos no sirve para investigar un incidente seis meses después.
2. **Redacción por defecto.** Hay un conjunto de claves que nunca se escriben
   en claro. No depende de que quien escribe el log se acuerde: el procesador
   las intercepta. El log de auditoría registra *que* algo ocurrió y *sobre qué
   recurso*, no el contenido personal implicado.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

# Claves cuyo valor nunca debe aparecer en claro en un log.
_REDACTED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "jwt_secret",
        "api_key",
        "anthropic_api_key",
        "field_encryption_key",
        "secret",
        "email",
        "phone",
        "ip",
        "ip_address",
        "prompt_input",
        "raw_document_text",
    }
)

_REDACTED = "«redacted»"


def _redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS and event_dict[key] not in (None, ""):
            event_dict[key] = _REDACTED
    return event_dict


def configure_logging(level: str = "INFO", *, json_output: bool = False) -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )

    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "app") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
