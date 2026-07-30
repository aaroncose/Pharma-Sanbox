"""Autenticación, permisos y aislamiento a nivel HTTP.

La fase anterior demostró que PostgreSQL aísla. Estas pruebas comprueban que la
API traduce ese aislamiento al contrato correcto y que no introduce por arriba
las fugas que la base de datos evita por abajo: mensajes que distinguen casos,
códigos de estado que sirven de oráculo, o permisos comprobados solo en el
cliente.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.ratelimit import get_rate_limiter
from app.db.seed import DEMO_PASSWORD
from app.main import app

LAURA = "laura.garcia@novapharma.demo"        # comercial de NovaPharma
MARIA = "maria.ruiz@novapharma.demo"          # compliance de NovaPharma
ANA = "ana.serra@novapharma.demo"             # auditora de NovaPharma
SOFIA = "sofia.marin@biohealth.demo"          # comercial de BioHealth
RAUL = "raul.diaz@novapharma.demo"            # cuenta suspendida
PLATFORM = "admin@platform.demo"


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limits() -> None:
    """El limitador es real y persiste en Redis entre pruebas.

    Sin esto, el orden de ejecución determinaría qué pruebas pasan, que es
    exactamente el tipo de prueba inestable que no sirve para nada.
    """
    get_rate_limiter()._client.flushdb()


def login(client: TestClient, email: str, password: str = DEMO_PASSWORD):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password})


def token_for(client: TestClient, email: str) -> str:
    response = login(client, email)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# Autenticación
# ─────────────────────────────────────────────────────────────────────────────


def test_login_returns_token_and_permissions(client: TestClient) -> None:
    body = login(client, LAURA).json()

    assert body["token_type"] == "Bearer"
    assert body["user"]["tenant"]["slug"] == "nph_01"
    assert body["user"]["role"] == "sales_rep"
    # Los permisos viajan para que la interfaz pueda ocultar lo que no procede.
    assert "briefing.create" in body["user"]["permissions"]
    assert "document.approve" not in body["user"]["permissions"]


def test_login_response_is_not_cacheable(client: TestClient) -> None:
    """Un proxy no debe guardar una respuesta que contiene tokens."""
    assert login(client, LAURA).headers.get("cache-control") == "no-store"


@pytest.mark.parametrize(
    ("email", "password", "case"),
    [
        (LAURA, "ContraseñaIncorrecta1!", "contraseña incorrecta"),
        ("noexiste@novapharma.demo", DEMO_PASSWORD, "usuario inexistente"),
        (RAUL, DEMO_PASSWORD, "cuenta suspendida"),
    ],
)
def test_all_login_failures_are_indistinguishable(
    client: TestClient, email: str, password: str, case: str
) -> None:
    """Los tres fallos producen exactamente la misma respuesta.

    Si la cuenta suspendida devolviera un mensaje propio, sería un oráculo:
    permitiría descubrir qué correos existen y cuáles están activos sin
    conocer ninguna contraseña.
    """
    response = login(client, email, password)
    assert response.status_code == 401, case
    assert response.json() == {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "Credenciales no válidas",
    }, case


def test_login_failures_are_audited_with_the_real_reason(
    client: TestClient, privileged_conn: Connection
) -> None:
    """Hacia fuera no se distingue; en la auditoría sí."""
    login(client, RAUL, DEMO_PASSWORD)

    row = privileged_conn.execute(
        text(
            "SELECT action, outcome, detail FROM audit_log "
            " WHERE action IN ('auth.login.inactive_user', 'auth.login.failed') "
            " ORDER BY occurred_at DESC LIMIT 1"
        )
    ).mappings().first()

    assert row is not None
    assert row["outcome"] == "denied"
    assert row["detail"]["reason"] == "user_inactive"
    assert row["detail"]["account_exists"] is True


def test_missing_token_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "AUTHENTICATION_REQUIRED"


def test_malformed_token_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me", headers=auth("no.es.un.token"))
    assert response.status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client: TestClient) -> None:
    """Un token de refresco vive días; uno de acceso, minutos.

    Sin la comprobación del campo `type`, presentar el de refresco como
    autorización anularía el sentido de que el de acceso sea corto.
    """
    refresh = login(client, LAURA).json()["refresh_token"]
    response = client.get("/api/v1/auth/me", headers=auth(refresh))
    assert response.status_code == 401


def test_refresh_rotates_and_invalidates_the_previous_token(client: TestClient) -> None:
    """Reutilizar un token de refresco ya consumido falla.

    Es lo que hace visible el robo de un token: si el atacante lo usa antes,
    el usuario legítimo pierde la sesión; si lo usa después, no funciona.
    """
    refresh = login(client, LAURA).json()["refresh_token"]

    first = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert first.status_code == 200

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401


def test_login_is_rate_limited(client: TestClient) -> None:
    """El único endpoint sin identidad autenticada, y por tanto el objetivo."""
    codes = [
        login(client, LAURA, "ContraseñaIncorrecta1!").status_code for _ in range(12)
    ]
    assert 429 in codes, "el intento número 11 debería haberse cortado"

    limited = next(c for c in codes if c == 429)
    assert limited == 429


# ─────────────────────────────────────────────────────────────────────────────
# Aislamiento entre organizaciones, extremo a extremo
# ─────────────────────────────────────────────────────────────────────────────


def test_cross_tenant_interaction_is_denied(
    client: TestClient, privileged_conn: Connection
) -> None:
    """El escenario literal del enunciado del proyecto.

        Un usuario de NovaPharma intenta acceder al ID de una interacción
        perteneciente a BioHealth.
        → 403 Forbidden / ACCESS_DENIED_CROSS_TENANT
    """
    target_id = privileged_conn.execute(
        text(
            "SELECT i.id FROM interactions i "
            "  JOIN tenants t ON t.id = i.tenant_id "
            " WHERE t.slug = 'bhl_01' LIMIT 1"
        )
    ).scalar_one()

    response = client.get(
        f"/api/v1/interactions/{target_id}", headers=auth(token_for(client, LAURA))
    )

    assert response.status_code == 403
    assert response.json()["code"] == "ACCESS_DENIED_CROSS_TENANT"
    # Ni un solo campo del recurso ajeno aparece en el cuerpo.
    assert "summary" not in response.text
    assert "DermaClear" not in response.text


def test_denied_attempt_is_audited_with_attribution(
    client: TestClient, privileged_conn: Connection
) -> None:
    """El log debe decir contra qué organización iba el intento.

    Sin esa atribución el evento diría "acceso denegado a un identificador",
    que no es investigable. Se obtiene con `audit_resource_owner`, que solo
    devuelve el tenant propietario y ninguna otra columna.
    """
    target_id = privileged_conn.execute(
        text(
            "SELECT i.id FROM interactions i JOIN tenants t ON t.id = i.tenant_id "
            " WHERE t.slug = 'bhl_01' LIMIT 1"
        )
    ).scalar_one()

    client.get(
        f"/api/v1/interactions/{target_id}", headers=auth(token_for(client, LAURA))
    )

    row = privileged_conn.execute(
        text(
            "SELECT a.decision_code, a.exposed_field_count, a.policy_code, a.detail, "
            "       actor.slug AS actor_tenant, target.slug AS target_tenant "
            "  FROM audit_log a "
            "  JOIN tenants actor  ON actor.id  = a.tenant_id "
            "  JOIN tenants target ON target.id = a.resource_tenant_id "
            " WHERE a.action = 'access.cross_tenant.attempt' "
            " ORDER BY a.occurred_at DESC LIMIT 1"
        )
    ).mappings().first()

    assert row is not None
    assert row["decision_code"] == "ACCESS_DENIED_CROSS_TENANT"
    assert row["policy_code"] == "TENANT_ISOLATION"
    assert row["actor_tenant"] == "nph_01"
    assert row["target_tenant"] == "bhl_01"
    # Afirmación comprobable, no una suposición derivada del código de estado.
    assert row["exposed_field_count"] == 0
    assert row["detail"]["resource_exists"] is True


def test_nonexistent_id_is_indistinguishable_from_cross_tenant(
    client: TestClient,
) -> None:
    """Un 404 aquí permitiría enumerar identificadores ajenos válidos.

    El atacante no leería ningún campo, pero sabría qué identificadores
    existen en otras organizaciones. Eso ya es una fuga.
    """
    headers = auth(token_for(client, LAURA))
    fake = client.get(
        "/api/v1/interactions/00000000-0000-4000-8000-000000000000", headers=headers
    )
    assert fake.status_code == 403
    assert fake.json()["code"] == "ACCESS_DENIED_CROSS_TENANT"


def test_each_tenant_only_lists_its_own_interactions(client: TestClient) -> None:
    nova = client.get(
        "/api/v1/interactions?limit=100", headers=auth(token_for(client, LAURA))
    ).json()
    bio = client.get(
        "/api/v1/interactions?limit=100", headers=auth(token_for(client, SOFIA))
    ).json()

    assert nova["count"] == 30
    assert bio["count"] == 20

    nova_ids = {item["id"] for item in nova["items"]}
    bio_ids = {item["id"] for item in bio["items"]}
    assert nova_ids.isdisjoint(bio_ids)


# ─────────────────────────────────────────────────────────────────────────────
# Permisos
# ─────────────────────────────────────────────────────────────────────────────


def test_permissions_are_enforced_in_the_backend(client: TestClient) -> None:
    """El superadministrador de plataforma no lee datos comerciales.

    Es la comprobación de que el rol más privilegiado del sistema no es un
    comodín: administra organizaciones, no accede a su contenido.
    """
    response = client.get(
        "/api/v1/interactions", headers=auth(token_for(client, PLATFORM))
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"
    assert "interaction.read" in response.json()["details"]["required"]


def test_permission_denial_is_audited(
    client: TestClient, privileged_conn: Connection
) -> None:
    client.get("/api/v1/interactions", headers=auth(token_for(client, PLATFORM)))

    row = privileged_conn.execute(
        text(
            "SELECT actor_role, decision_code, detail FROM audit_log "
            " WHERE action = 'access.permission.denied' "
            " ORDER BY occurred_at DESC LIMIT 1"
        )
    ).mappings().first()

    assert row is not None
    assert row["actor_role"] == "platform_superadmin"
    assert row["decision_code"] == "PERMISSION_DENIED"
    assert "interaction.read" in row["detail"]["missing"]


@pytest.mark.parametrize(
    ("email", "expected_role"),
    [(LAURA, "sales_rep"), (MARIA, "compliance_officer"), (ANA, "auditor")],
)
def test_me_reports_the_role_of_the_session(
    client: TestClient, email: str, expected_role: str
) -> None:
    body = client.get(
        "/api/v1/auth/me", headers=auth(token_for(client, email))
    ).json()
    assert body["role"] == expected_role
    assert body["tenant"]["slug"] == "nph_01"


def test_auditor_cannot_generate_content() -> None:
    """Un rol de solo lectura que puede invocar al agente no es de solo lectura.

    Consumiría presupuesto, escribiría trazas y podría producir contenido que
    alguien tendría que revisar. Se comprueba sobre la matriz porque cubre
    todos los permisos de generación, no solo los que ya tienen endpoint.
    """
    from app.core.permissions import ROLE_PERMISSIONS

    auditor = ROLE_PERMISSIONS["auditor"]
    for forbidden in (
        "briefing.create", "chat.use", "simulation.use", "summary.create",
        "document.approve", "review.decide", "policy.manage", "user.manage",
    ):
        assert forbidden not in auditor, f"el auditor no debería tener {forbidden}"


def test_compliance_cannot_generate_commercial_content() -> None:
    """Quien revisa no debería ser también quien produce."""
    from app.core.permissions import ROLE_PERMISSIONS

    compliance = ROLE_PERMISSIONS["compliance_officer"]
    assert "review.decide" in compliance
    assert "briefing.create" not in compliance
    assert "summary.create" not in compliance


def test_org_admin_cannot_approve_documents() -> None:
    """Separación entre quien sube material y quien lo aprueba.

    Sin ella, una sola persona puede introducir contenido no validado en la
    biblioteca y dejarlo disponible para el agente.
    """
    from app.core.permissions import ROLE_PERMISSIONS

    admin = ROLE_PERMISSIONS["org_admin"]
    assert "document.create" in admin
    assert "document.approve" not in admin
