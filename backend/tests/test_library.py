"""Biblioteca documental: ciclo de aprobación y efecto sobre lo que el agente ve.

Estas pruebas están escritas como intentos de romper la promesa del sistema —
«el agente solo cita material aprobado y vigente»— y no como recorridos felices
del formulario de alta.

La prueba central es la de los cuatro ojos. La matriz de permisos, por sí sola,
**permite** que María apruebe lo que ella misma escribió: `compliance_officer`
tiene `document.create` y `document.approve` a la vez, y tenerlos los dos es
correcto —compliance redacta políticas internas—. Lo que no puede es firmar su
propio trabajo. Ese es un control que ninguna matriz de roles expresa, porque no
depende del rol sino de la relación entre dos filas.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.ratelimit import get_rate_limiter
from app.db.seed import DEMO_PASSWORD
from app.main import app

LAURA = "laura.garcia@novapharma.demo"     # sales_rep  — sin document.create
MARIA = "maria.ruiz@novapharma.demo"       # compliance — crea y aprueba
CARLOS = "carlos.vidal@novapharma.demo"    # org_admin  — crea, NO aprueba
ANA = "ana.serra@novapharma.demo"          # auditor    — solo lectura
DIEGO = "compliance@biohealth.demo"        # compliance de la otra organización


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limits() -> None:
    get_rate_limiter()._client.flushdb()


def token_for(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(client: TestClient, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_for(client, email)}"}


BODY = (
    "§ 1. Objeto\n"
    "Documento sintético creado por la suite de pruebas.\n\n"
    "§ 2. Posologia\n"
    "La pauta descrita aquí es ficticia y no corresponde a ningún medicamento real.\n"
)


@pytest.fixture
def draft(client: TestClient) -> Iterator[dict]:
    """Crea un borrador con Carlos (org_admin) y lo limpia al terminar.

    Lo crea el administrador y no compliance para que el escenario por defecto
    sea el válido: autor y aprobador distintos. La prueba de los cuatro ojos
    construye el suyo propio a propósito.
    """
    response = client.post(
        "/api/v1/library/documents",
        headers=auth(client, CARLOS),
        json={
            "title": f"Prueba sintética {uuid.uuid4().hex[:8]}",
            "doc_type": "material",
            "body": BODY,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    yield created

    from app.db.session import create_privileged_engine

    engine = create_privileged_engine()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM documents WHERE id = CAST(:id AS uuid)"),
            {"id": created["id"]},
        )


# ─────────────────────────────────────────────────────────────────────────────
# Alta e indexación
# ─────────────────────────────────────────────────────────────────────────────


def test_document_is_born_as_draft_and_is_never_citable(
    client: TestClient, draft: dict
) -> None:
    """No existe forma de crear un documento ya aprobado.

    Si `status` fuera un campo de entrada, `approved_by` sería un valor que el
    propio autor se rellena, y la aprobación dejaría de significar nada.
    """
    assert draft["status"] == "draft"

    detail = client.get(
        f"/api/v1/library/documents/{draft['id']}", headers=auth(client, CARLOS)
    ).json()

    assert detail["status"] == "draft"
    assert detail["citable"] is False
    assert detail["approved_at"] is None
    assert detail["approved_by"] is None


def test_draft_is_indexed_but_not_retrievable_by_the_agent(
    client: TestClient, draft: dict, privileged_conn: Connection
) -> None:
    """Se indexa todo y se filtra al leer.

    Los fragmentos del borrador existen —la indexación no es el control de
    acceso—, pero la búsqueda que usa el agente no los alcanza porque pasa por
    `citable_documents`. Si la indexación fuera el control, un fallo en el flujo
    de aprobación dejaría material no aprobado disponible en silencio.
    """
    assert draft["chunks_indexed"] == 2

    stored = privileged_conn.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = CAST(:id AS uuid)"),
        {"id": draft["id"]},
    ).scalar()
    assert stored == 2, "el borrador debe estar indexado"

    hits = client.post(
        "/api/v1/library/search",
        headers=auth(client, CARLOS),
        json={"query": "documento sintetico creado por la suite de pruebas"},
    ).json()

    assert draft["id"] not in [r["document_id"] for r in hits["results"]], (
        "un borrador no puede aparecer en la búsqueda que alimenta al agente"
    )


def test_sales_rep_cannot_create_documents(client: TestClient) -> None:
    """Quien usa el agente no alimenta su propia fuente de verdad."""
    response = client.post(
        "/api/v1/library/documents",
        headers=auth(client, LAURA),
        json={"title": "Intento de comercial", "doc_type": "material", "body": BODY},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


# ─────────────────────────────────────────────────────────────────────────────
# Aprobación: separación de funciones
# ─────────────────────────────────────────────────────────────────────────────


def test_author_cannot_approve_their_own_document(client: TestClient) -> None:
    """Regla de los cuatro ojos. El control que la matriz de roles no expresa.

    María tiene los dos permisos y aun así no puede firmar lo suyo. El rechazo
    no es 403 —sus permisos son correctos— sino 422: la petición es inválida por
    la relación entre el actor y el recurso, no por falta de autorización.
    """
    created = client.post(
        "/api/v1/library/documents",
        headers=auth(client, MARIA),
        json={
            "title": f"Documento propio {uuid.uuid4().hex[:8]}",
            "doc_type": "politica",
            "body": BODY,
        },
    ).json()

    try:
        response = client.post(
            f"/api/v1/library/documents/{created['id']}/approve",
            headers=auth(client, MARIA),
            json={"note": "revisado por mí misma"},
        )

        assert response.status_code == 422, response.text
        assert response.json()["details"]["rule"] == "SEPARATION_OF_DUTIES"

        # Y el documento sigue sin ser citable: el rechazo no dejó estado a medias.
        detail = client.get(
            f"/api/v1/library/documents/{created['id']}", headers=auth(client, MARIA)
        ).json()
        assert detail["status"] == "draft"
        assert detail["citable"] is False
    finally:
        from app.db.session import create_privileged_engine

        with create_privileged_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM documents WHERE id = CAST(:id AS uuid)"),
                {"id": created["id"]},
            )


def test_org_admin_cannot_approve_anything(client: TestClient, draft: dict) -> None:
    """El administrador sube material; no decide que sea válido."""
    response = client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, CARLOS),
        json={},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "PERMISSION_DENIED"


def test_approval_makes_the_document_citable_and_records_provenance(
    client: TestClient, draft: dict, privileged_conn: Connection
) -> None:
    """Aprobar es la transición que abre la puerta al agente."""
    response = client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA),
        json={"note": "conforme"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["citable"] is True

    row = privileged_conn.execute(
        text(
            "SELECT status, approved_at, approved_by FROM documents "
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": draft["id"]},
    ).mappings().one()

    assert row["status"] == "approved"
    # La restricción de la tabla exige que existan los dos. Se comprueba aquí
    # porque es la afirmación que sostiene toda la trazabilidad: no hay ningún
    # documento aprobado del que no se sepa quién y cuándo.
    assert row["approved_at"] is not None
    assert row["approved_by"] is not None

    hits = client.post(
        "/api/v1/library/search",
        headers=auth(client, CARLOS),
        json={"query": "documento sintetico creado por la suite de pruebas"},
    ).json()
    assert draft["id"] in [r["document_id"] for r in hits["results"]]


def test_approving_twice_is_a_conflict_not_a_silent_success(
    client: TestClient, draft: dict
) -> None:
    """Una segunda aprobación reescribiría el aprobador original.

    Devolver 200 sería peor que un error: cambiaría quién consta como
    responsable de la validación sin que nadie lo pidiera.
    """
    client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA), json={},
    )
    second = client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA), json={},
    )
    assert second.status_code == 409


def test_approved_document_cannot_be_edited(client: TestClient, draft: dict) -> None:
    """Editar bajo los pies invalidaría citas ya entregadas."""
    client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA), json={},
    )
    response = client.patch(
        f"/api/v1/library/documents/{draft['id']}",
        headers=auth(client, CARLOS),
        json={"body": BODY + "\n§ 3. Añadido posterior\nTexto nuevo sin aprobar.\n"},
    )
    assert response.status_code == 409
    assert response.json()["details"]["rule"] == "IMMUTABLE_ONCE_PUBLISHED"


# ─────────────────────────────────────────────────────────────────────────────
# Retirada — escenario 1 del Failure Lab
# ─────────────────────────────────────────────────────────────────────────────


def test_withdrawal_takes_effect_on_the_next_query_without_any_cleanup(
    client: TestClient, draft: dict, privileged_conn: Connection
) -> None:
    """Retirar quita el documento del alcance del agente de inmediato.

    No se ejecuta ningún proceso de reindexación ni de limpieza. Los fragmentos
    siguen en la tabla; lo que cambia es que la vista `citable_documents` deja de
    incluirlos. Es lo que permite afirmar que la retirada de una cifra de
    eficacia incorrecta tiene efecto ya, no cuando pase un lote nocturno.
    """
    client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA), json={},
    )
    query = {"query": "documento sintetico creado por la suite de pruebas"}

    def found() -> bool:
        hits = client.post(
            "/api/v1/library/search", headers=auth(client, CARLOS), json=query
        ).json()
        return draft["id"] in [r["document_id"] for r in hits["results"]]

    assert found()

    response = client.post(
        f"/api/v1/library/documents/{draft['id']}/withdraw",
        headers=auth(client, MARIA),
        json={"reason": "Cifra de eficacia incorrecta detectada en revisión posterior"},
    )
    assert response.status_code == 200, response.text

    assert not found(), "la retirada debe surtir efecto en la consulta siguiente"

    # Los fragmentos NO se han borrado: la retirada no destruye evidencia.
    remaining = privileged_conn.execute(
        text("SELECT count(*) FROM document_chunks WHERE document_id = CAST(:id AS uuid)"),
        {"id": draft["id"]},
    ).scalar()
    assert remaining == 2


def test_withdrawal_without_a_real_reason_is_rejected(
    client: TestClient, draft: dict
) -> None:
    """Un motivo de tres caracteres no es un motivo."""
    client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA), json={},
    )
    response = client.post(
        f"/api/v1/library/documents/{draft['id']}/withdraw",
        headers=auth(client, MARIA),
        json={"reason": "no"},
    )
    assert response.status_code == 422


def test_withdrawal_is_audited_with_the_reason_and_the_blast_radius(
    client: TestClient, draft: dict, privileged_conn: Connection
) -> None:
    """La auditoría debe permitir reconstruir por qué se retiró y a qué afectó."""
    client.post(
        f"/api/v1/library/documents/{draft['id']}/approve",
        headers=auth(client, MARIA), json={},
    )
    reason = "Retirado por discrepancia con la ficha técnica autorizada"
    client.post(
        f"/api/v1/library/documents/{draft['id']}/withdraw",
        headers=auth(client, MARIA), json={"reason": reason},
    )

    entry = privileged_conn.execute(
        text(
            "SELECT detail FROM audit_log "
            # `resource_id` es `text` y no `uuid` a propósito: el log audita
            # también recursos que no se identifican con un UUID (una consulta
            # de búsqueda, un intento de herramienta). Se compara como texto.
            " WHERE action = 'document.withdrawn' AND resource_id = :id "
            " ORDER BY occurred_at DESC LIMIT 1"
        ),
        {"id": draft["id"]},
    ).scalar()

    assert entry is not None, "una retirada sin rastro en auditoría no es auditable"
    assert entry["reason"] == reason
    assert "previously_cited_by_outputs" in entry


# ─────────────────────────────────────────────────────────────────────────────
# Aislamiento y lectura
# ─────────────────────────────────────────────────────────────────────────────


def test_document_of_another_organization_is_denied_identically(
    client: TestClient, draft: dict
) -> None:
    """Mismo 403 exista o no. Distinguirlos permitiría enumerar identificadores."""
    real = client.get(
        f"/api/v1/library/documents/{draft['id']}", headers=auth(client, DIEGO)
    )
    invented = client.get(
        f"/api/v1/library/documents/{uuid.uuid4()}", headers=auth(client, DIEGO)
    )

    assert real.status_code == invented.status_code == 403
    assert real.json() == invented.json(), (
        "las dos respuestas deben ser idénticas byte a byte"
    )


def test_auditor_can_read_the_library_but_cannot_change_it(
    client: TestClient, draft: dict
) -> None:
    listing = client.get("/api/v1/library/documents", headers=auth(client, ANA))
    assert listing.status_code == 200

    for method, path, body in (
        ("post", "/api/v1/library/documents",
         {"title": "Auditor escribiendo", "doc_type": "material", "body": BODY}),
        ("post", f"/api/v1/library/documents/{draft['id']}/approve", {}),
        ("post", f"/api/v1/library/documents/{draft['id']}/withdraw",
         {"reason": "motivo suficientemente largo"}),
    ):
        response = getattr(client, method)(path, headers=auth(client, ANA), json=body)
        assert response.status_code == 403, f"{path} debería estar denegado"


def test_search_reports_when_the_evidence_would_be_insufficient(
    client: TestClient,
) -> None:
    """El instrumento tiene que enseñar también el caso en que no hay nada.

    Es la mitad que suele faltar: una búsqueda que siempre devuelve resultados
    parece que siempre encuentra algo. Aquí la relevancia real deja claro que no.
    """
    gibberish = client.post(
        "/api/v1/library/search",
        headers=auth(client, CARLOS),
        json={"query": "zzzz qqqq wwww xxxx"},
    ).json()

    legitimate = client.post(
        "/api/v1/library/search",
        headers=auth(client, CARLOS),
        json={"query": "informacion de seguridad aprobada sobre CardioX"},
    ).json()

    assert gibberish["would_answer"] is False
    assert legitimate["would_answer"] is True
    assert gibberish["relevance"] < legitimate["relevance"]


def test_search_shows_both_rankings_so_hybrid_retrieval_is_verifiable(
    client: TestClient,
) -> None:
    """Sin los dos rangos, «la búsqueda es híbrida» es una afirmación sin prueba.

    Se busca un término exacto —un código de estudio— que es justo lo que el
    embebedor local aproxima mal y la búsqueda léxica acierta.
    """
    results = client.post(
        "/api/v1/library/search",
        headers=auth(client, CARLOS),
        json={"query": "estudio CARDIO-101"},
    ).json()["results"]

    assert results, "debería haber material sobre el estudio"
    assert any(r["lexical_rank"] is not None for r in results), (
        "ningún resultado entró por la vía léxica: la búsqueda no es híbrida"
    )
