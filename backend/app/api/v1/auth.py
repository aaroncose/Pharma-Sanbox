"""Inicio y cierre de sesión.

Propiedades que se defienden aquí:

**La respuesta a un fallo de autenticación es siempre la misma.** Correo
inexistente, contraseña incorrecta, cuenta suspendida y organización desactivada
producen el mismo cuerpo y el mismo código. Internamente se distinguen y quedan
en la auditoría con acciones distintas, porque el equipo de seguridad sí
necesita saberlo.

**El coste temporal también es el mismo.** `verify_password` verifica contra un
hash señuelo cuando el usuario no existe. Sin eso, el tiempo de respuesta
delataría qué correos están dados de alta.

**El límite de tasa del login se aplica por dirección y por correo.** Es el
único endpoint sin identidad autenticada, así que es el objetivo natural de un
ataque de fuerza bruta o de relleno de credenciales.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, UnscopedSession, get_trace_id
from app.config import settings
from app.core.errors import AuthenticationError
from app.core.permissions import permissions_for
from app.core.ratelimit import get_rate_limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.core.token_store import get_token_store
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TenantInfo,
    TokenResponse,
    UserInfo,
)
from app.services import audit
from app.services.audit import AuditEvent, fingerprint_client

router = APIRouter(prefix="/auth", tags=["auth"])

# Intentos por minuto antes de cortar. Bajo a propósito: un humano que teclea
# mal su contraseña no llega a diez intentos en un minuto.
LOGIN_ATTEMPTS_PER_MINUTE = 10


def _build_token_response(
    *, user_id: str, tenant_id: str, role: str, user_row: dict
) -> TokenResponse:
    access, _ = create_access_token(user_id=user_id, tenant_id=tenant_id, role=role)
    refresh, _ = create_refresh_token(user_id=user_id, tenant_id=tenant_id, role=role)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_minutes * 60,
        user=UserInfo(
            id=user_id,
            email=user_row["email"],
            full_name=user_row["full_name"],
            role=role,
            # Los permisos viajan al cliente para que la interfaz pueda ocultar
            # lo que no procede. Es una comodidad de presentación, no un
            # control: cada endpoint vuelve a comprobarlos en el servidor.
            permissions=sorted(permissions_for(role)),
            tenant=TenantInfo(
                id=tenant_id,
                slug=user_row["tenant_slug"],
                name=user_row["tenant_name"],
            ),
        ),
    )


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: UnscopedSession,
) -> TokenResponse:
    trace_id = get_trace_id(request)
    client_ip = request.client.host if request.client else None
    fingerprint = fingerprint_client(client_ip, request.headers.get("user-agent"))

    # Dos cubos: uno por origen y otro por correo. El primero frena la fuerza
    # bruta desde una máquina; el segundo, el relleno de credenciales
    # distribuido contra una cuenta concreta.
    limiter = get_rate_limiter()
    email_key = hashlib.sha256(payload.email.lower().encode()).hexdigest()[:24]
    limiter.check(client_ip or "unknown", bucket="login_ip", limit=LOGIN_ATTEMPTS_PER_MINUTE)
    limiter.check(email_key, bucket="login_email", limit=LOGIN_ATTEMPTS_PER_MINUTE)

    row = session.execute(
        text("SELECT * FROM auth_lookup_user(:email)"), {"email": payload.email}
    ).mappings().first()

    user = dict(row) if row else None
    password_ok = verify_password(payload.password, user["password_hash"] if user else None)

    # Un único punto de salida para todos los fallos: mismo código, mismo
    # mensaje, mismo tiempo aproximado.
    failure_reason: str | None = None
    if user is None or not password_ok:
        failure_reason = "bad_credentials"
    elif user["status"] != "active":
        failure_reason = "user_inactive"
    elif user["tenant_status"] != "active":
        failure_reason = "tenant_suspended"

    if failure_reason:
        audit.record(
            session,
            AuditEvent(
                action=(
                    audit.AUTH_LOGIN_INACTIVE
                    if failure_reason != "bad_credentials"
                    else audit.AUTH_LOGIN_FAILED
                ),
                outcome="denied",
                trace_id=trace_id,
                tenant_id=str(user["tenant_id"]) if user else None,
                actor_user_id=str(user["id"]) if user else None,
                actor_role=user["role"] if user else None,
                decision_code="AUTHENTICATION_REQUIRED",
                exposed_field_count=0,
                client_fingerprint=fingerprint,
                # El motivo real queda aquí, no en la respuesta.
                detail={"reason": failure_reason, "account_exists": user is not None},
            ),
        )
        session.commit()
        raise AuthenticationError("Credenciales no válidas")

    assert user is not None  # garantizado por las ramas anteriores

    user_id = str(user["id"])
    tenant_id = str(user["tenant_id"])

    # Si los parámetros de Argon2 se endurecen, la contraseña se recalcula al
    # entrar. Es el único momento en que está disponible en claro.
    if needs_rehash(user["password_hash"]):
        session.execute(
            text("SELECT auth_touch_last_login(CAST(:id AS uuid))"), {"id": user_id}
        )
        session.execute(
            text("UPDATE users SET password_hash = :h WHERE id = CAST(:id AS uuid)"),
            {"h": hash_password(payload.password), "id": user_id},
        )

    session.execute(
        text("SELECT auth_touch_last_login(CAST(:id AS uuid))"), {"id": user_id}
    )

    audit.record(
        session,
        AuditEvent(
            action=audit.AUTH_LOGIN_SUCCESS,
            outcome="success",
            trace_id=trace_id,
            tenant_id=tenant_id,
            actor_user_id=user_id,
            actor_role=user["role"],
            client_fingerprint=fingerprint,
        ),
    )
    session.commit()

    # Impide que un proxy intermedio cachee una respuesta con tokens dentro.
    response.headers["Cache-Control"] = "no-store"
    return _build_token_response(
        user_id=user_id, tenant_id=tenant_id, role=user["role"], user_row=user
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    response: Response,
    session: UnscopedSession,
) -> TokenResponse:
    """Renueva la sesión.

    A diferencia de la validación del token de acceso, aquí sí se consulta el
    estado real del usuario. Es el punto en el que una cuenta desactivada o un
    cambio de rol surten efecto: como máximo, la vida del token de acceso.
    """
    claims = decode_token(payload.refresh_token, expected_type="refresh")

    store = get_token_store()
    store.assert_active(claims.jti)

    # Revocación masiva: al desactivar un usuario o cambiar su rol se guarda una
    # fecha de corte. Todo token emitido antes de ese instante deja de poder
    # renovarse, aunque su `jti` concreto nunca se haya revocado.
    revoked_after = store.user_revoked_after(claims.user_id)
    if revoked_after is not None and claims.issued_at < revoked_after:
        raise AuthenticationError("La sesión ha sido revocada")

    # Vía acotada, igual que en el login: la renovación ocurre antes de conocer
    # el tenant, porque el tenant se deriva del usuario que se está validando.
    # Una consulta directa a `users` aquí devuelve cero filas por RLS y rechaza
    # sesiones legítimas.
    row = session.execute(
        text("SELECT * FROM auth_lookup_user_by_id(CAST(:id AS uuid))"),
        {"id": claims.user_id},
    ).mappings().first()

    if row is None or row["status"] != "active" or row["tenant_status"] != "active":
        raise AuthenticationError("La sesión ya no es válida")

    # Rotación: el token usado se revoca al emitirse el nuevo. Si un token de
    # refresco robado se usa después del legítimo, falla; si se usa antes, el
    # usuario legítimo pierde la sesión y el incidente se hace visible.
    store.revoke(claims.jti, expires_at=claims.expires_at)

    audit.record(
        session,
        AuditEvent(
            action=audit.AUTH_TOKEN_REFRESHED,
            outcome="success",
            trace_id=get_trace_id(request),
            tenant_id=str(row["tenant_id"]),
            actor_user_id=str(row["id"]),
            actor_role=row["role"],
        ),
    )
    session.commit()

    response.headers["Cache-Control"] = "no-store"
    return _build_token_response(
        user_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        role=row["role"],
        user_row=dict(row),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: RefreshRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> None:
    """Cierra la sesión revocando la cadena de refresco.

    El token de acceso sigue siendo válido hasta que expire: no se puede
    invalidar sin consultar un almacén en cada petición. Es el compromiso
    aceptado y documentado del diseño.
    """
    claims = decode_token(payload.refresh_token, expected_type="refresh")
    if claims.user_id != principal.user_id:
        # Revocar la sesión de otra persona sería una denegación de servicio
        # dirigida.
        raise AuthenticationError("El token no pertenece a esta sesión")

    get_token_store().revoke(claims.jti, expires_at=claims.expires_at)

    audit.record(
        session,
        AuditEvent(
            action=audit.AUTH_LOGOUT,
            outcome="success",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
        ),
    )


@router.get("/me", response_model=UserInfo)
def me(principal: CurrentPrincipal, session: TenantSession) -> UserInfo:
    """Perfil de la sesión actual.

    La consulta no lleva filtro de tenant: RLS ya limita `users` al del token.
    """
    row = session.execute(
        text(
            "SELECT u.id, u.email, u.full_name, u.role, "
            "       t.id AS tenant_id, t.slug, t.name "
            "  FROM users u JOIN tenants t ON t.id = u.tenant_id "
            " WHERE u.id = CAST(:id AS uuid)"
        ),
        {"id": principal.user_id},
    ).mappings().first()

    if row is None:
        raise AuthenticationError("La sesión ya no es válida")

    return UserInfo(
        id=str(row["id"]),
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        permissions=sorted(permissions_for(row["role"])),
        tenant=TenantInfo(
            id=str(row["tenant_id"]), slug=row["slug"], name=row["name"]
        ),
    )
