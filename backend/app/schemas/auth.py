"""Contratos de entrada y salida de autenticación."""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    # El límite superior evita que una contraseña arbitrariamente larga
    # convierta el hashing con Argon2 en un vector de denegación de servicio:
    # el coste es proporcional a la entrada y la verificación es cara a
    # propósito.
    password: str = Field(min_length=8, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=4096)


class TenantInfo(BaseModel):
    id: str
    slug: str
    name: str


class UserInfo(BaseModel):
    id: str
    email: EmailStr
    full_name: str
    role: str
    permissions: list[str]
    tenant: TenantInfo


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"  # noqa: S105 — nombre del esquema, no un secreto
    expires_in: int
    user: UserInfo
