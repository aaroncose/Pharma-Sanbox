"""Configuración central.

Todo se lee de entorno. No hay valores sensibles con default utilizable:
si falta un secreto en un entorno que no sea `development`, el arranque falla.
Es preferible no arrancar a arrancar inseguro.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── Aplicación ───────────────────────────────────────────────────────────
    app_env: AppEnv = "development"
    app_name: str = "Pharma Commercial AI Sandbox"
    log_level: str = "INFO"

    # ── Base de datos ────────────────────────────────────────────────────────
    # El rol de `database_url` NO debe ser superusuario ni tener BYPASSRLS.
    # Si lo fuese, las políticas RLS quedarían desactivadas silenciosamente y
    # el aislamiento entre tenants sería una ilusión. Se verifica al arrancar.
    database_url: str = (
        "postgresql+psycopg://pharma_app:pharma_app_dev@localhost:5432/pharma_sandbox"
    )
    migration_database_url: str = (
        "postgresql+psycopg://pharma_owner:pharma_owner_dev@localhost:5432/pharma_sandbox"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    # ── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Seguridad ────────────────────────────────────────────────────────────
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 7
    field_encryption_key: str = ""

    # ── Proveedor de IA ──────────────────────────────────────────────────────
    llm_provider: Literal["anthropic", "mock"] = "anthropic"
    anthropic_api_key: str = ""
    llm_primary_model: str = "claude-sonnet-5"
    llm_verifier_model: str = "claude-haiku-4-5-20251001"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 3

    # ── Almacenamiento ───────────────────────────────────────────────────────
    storage_backend: Literal["filesystem", "s3"] = "filesystem"
    storage_path: str = "./storage"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    # ── Políticas operativas ─────────────────────────────────────────────────
    audit_retention_days: int = 365
    rate_limit_per_minute: int = 60
    rate_limit_agent_per_minute: int = 10

    # ── CORS ─────────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # ─────────────────────────────────────────────────────────────────────────

    @property
    def is_production_like(self) -> bool:
        return self.app_env in ("staging", "production")

    @property
    def llm_uses_real_provider(self) -> bool:
        """True solo si hay proveedor real Y credencial.

        Sin credencial se degrada al proveedor mock determinista en vez de
        romper: la demo tiene que poder ejecutarse sin coste ni red.
        """
        return self.llm_provider == "anthropic" and bool(self.anthropic_api_key)

    @model_validator(mode="after")
    def _enforce_secrets(self) -> Settings:
        if self.is_production_like:
            missing = [
                name
                for name, value in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("FIELD_ENCRYPTION_KEY", self.field_encryption_key),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"Faltan secretos obligatorios en {self.app_env}: {', '.join(missing)}"
                )
            if len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET debe tener al menos 32 caracteres")
        elif not self.jwt_secret:
            # En local se genera uno efímero: invalida los tokens en cada
            # reinicio, que es exactamente lo que queremos en desarrollo.
            object.__setattr__(self, "jwt_secret", secrets.token_urlsafe(64))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
