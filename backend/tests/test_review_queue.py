"""Cola de revisión humana.

Lo que se comprueba aquí no es que la cola funcione, sino que **no se pueda
usar mal**: que no se apruebe sin motivo, que una decisión no se reescriba, que
quien produce no valide, y que corregir no destruya el original.

El caso que más me interesa es el de la aprobación. Es el desenlace que parece
no aportar nada —el contenido estaba bien— y es el único que permite relajar los
umbrales del harness. Sin registrarlo, un sistema con supervisión humana solo
puede endurecerse: las correcciones dejan rastro y los falsos positivos no, así
que la métrica mejora mientras el producto bloquea cada vez más contenido
legítimo, hasta que quien lo sufre encuentra la manera de saltárselo.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.permissions import (
    BRIEFING_CREATE,
    CHAT_USE,
    REVIEW_DECIDE,
    SUMMARY_CREATE,
    has_permission,
)
from app.core.ratelimit import get_rate_limiter
from app.db.seed import DEMO_PASSWORD
from app.db.session import create_privileged_engine
from app.main import app

LAURA = "laura.garcia@novapharma.demo"     # sales_rep  — origina el contenido
MARIA = "maria.ruiz@novapharma.demo"       # compliance — decide
CARLOS = "carlos.vidal@novapharma.demo"    # org_admin  — ni lee ni decide
ANA = "ana.serra@novapharma.demo"          # auditor    — lee, no decide
DIEGO = "compliance@biohealth.demo"        # compliance de la otra organización

ORIGINAL = (
    "CardioX reduce el riesgo cardiovascular un 37% según el estudio CARDIO-101 "
    "y es especialmente adecuado en pacientes diabéticos con insuficiencia renal."
)
CORREGIDO = (
    "El estudio CARDIO-101 describe una reducción media de presión arterial de "
    "12,4 mmHg frente a 4,1 mmHg en el grupo control. No hay datos aprobados "
    "sobre uso en pacientes con insuficiencia renal."
)
MOTIVO = "Afirmación de eficacia sin respaldo en la documentación aprobada vigente"


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


def _make_item(
    conn: Connection,
    *,
    tenant_slug: str,
    author_email: str,
    priority: str = "high",
    content: str = ORIGINAL,
) -> str:
    """Crea una salida del agente y su entrada en la cola.

    Se escribe con el rol propietario y no invocando al agente: lo que estas
    pruebas ejercitan es la cola, y hacerla depender de una generación real la
    volvería lenta, cara y no determinista. Que el harness cree la entrada
    cuando corresponde se comprueba en `test_agent_harness`.
    """
    tenant_id = conn.execute(
        text("SELECT id FROM tenants WHERE slug = :s"), {"s": tenant_slug}
    ).scalar()
    author_id = conn.execute(
        text("SELECT id FROM users WHERE lower(email) = :e"), {"e": author_email}
    ).scalar()

    output_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO agent_outputs "
            "  (id, tenant_id, kind, user_id, payload, answer_text, confidence, "
            "   risk, requires_human_review, trace_id, prompt_name, "
            "   prompt_version, model, provider) "
            "VALUES (:id, :tenant_id, 'chat_answer', :user_id, "
            "        CAST(:payload AS jsonb), :answer, 35, 'high', true, "
            "        :trace_id, 'chat', 'v1.0', 'claude-sonnet-5', 'anthropic')"
        ),
        {
            "id": output_id,
            "tenant_id": tenant_id,
            "user_id": author_id,
            "payload": '{"answer": "sintetico"}',
            "answer": content,
            "trace_id": f"tr_test_{uuid.uuid4().hex[:8]}",
        },
    )

    item_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO review_items "
            "  (id, tenant_id, subject_type, agent_output_id, requested_by, "
            "   reason, policy_code, priority, status, original_content) "
            "VALUES (:id, :tenant_id, 'chat_answer', :output_id, :user_id, "
            "        :reason, 'NO_UNAPPROVED_CLAIMS', CAST(:priority AS task_priority), "
            "        'pending', :content)"
        ),
        {
            "id": item_id,
            "tenant_id": tenant_id,
            "output_id": output_id,
            "user_id": author_id,
            "reason": "El verificador encontró 2 afirmaciones sin respaldo documental",
            "priority": priority,
            "content": content,
        },
    )
    return item_id


@pytest.fixture
def item() -> Iterator[str]:
    engine = create_privileged_engine()
    with engine.begin() as conn:
        item_id = _make_item(conn, tenant_slug="nph_01", author_email=LAURA)
    yield item_id
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM review_items WHERE id = CAST(:id AS uuid)"),
            {"id": item_id},
        )
        conn.execute(text("DELETE FROM agent_outputs WHERE prompt_name = 'chat'"))


# ─────────────────────────────────────────────────────────────────────────────
# El motivo escrito
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rationale", ["ok", "vale", "revisado", "   " * 10, "correcto"]
)
def test_a_decision_without_a_real_rationale_is_rejected(
    client: TestClient, item: str, rationale: str
) -> None:
    """Sin motivo no hay decisión. Es lo que separa la supervisión del botón.

    «Vale» y «revisado» caben en el mínimo de la restricción de base de datos y
    no explican nada. Seis meses después, quien audite por qué se dejó pasar un
    contenido necesita una frase, no una marca de verificación.
    """
    response = client.post(
        f"/api/v1/review/{item}/approve",
        headers=auth(client, MARIA),
        json={"rationale": rationale},
    )
    assert response.status_code == 422, response.text


def test_a_custom_validator_failure_is_a_422_and_not_a_500(
    client: TestClient, item: str
) -> None:
    """Regresión de un fallo que afectaba a toda la aplicación.

    Cuando un `field_validator` propio lanza `ValueError`, Pydantic mete el
    objeto de la excepción en `ctx`, y el manejador global lo serializaba tal
    cual: no es JSON, así que devolvía 500. Es decir, **cualquier** validación
    propia del proyecto convertía un error del cliente en un fallo del
    servidor, ocultando además cuál era el campo mal.
    """
    response = client.post(
        f"/api/v1/review/{item}/approve",
        headers=auth(client, MARIA),
        json={"rationale": " " * 40},
    )

    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "VALIDATION_FAILED"

    error = body["details"]["errors"][0]
    assert error["field"] == "rationale"
    assert error["message"], "el cliente tiene que saber qué está mal"


def test_the_validation_error_does_not_echo_what_was_submitted(
    client: TestClient, item: str
) -> None:
    """El detalle no devuelve el valor rechazado.

    `exc.errors()` incluye `input`. Reflejarlo mete en la respuesta —y en
    cualquier registro que la recoja— el contenido que se acaba de rechazar, que
    en otros endpoints es el cuerpo de un documento o unas notas de visita. El
    cliente ya sabe lo que mandó.
    """
    secreto = "TEXTO-QUE-NO-DEBE-VOLVER-" + uuid.uuid4().hex
    response = client.post(
        f"/api/v1/review/{item}/edit",
        headers=auth(client, MARIA),
        json={"rationale": "corto", "edited_content": secreto},
    )

    assert response.status_code == 422
    assert secreto not in response.text


def test_the_rationale_is_stored_verbatim_in_the_audit_log(
    client: TestClient, item: str, privileged_conn: Connection
) -> None:
    """El motivo es el dato por el que se consulta este registro."""
    client.post(
        f"/api/v1/review/{item}/reject",
        headers=auth(client, MARIA),
        json={"rationale": MOTIVO},
    )

    detail = privileged_conn.execute(
        text(
            "SELECT detail FROM audit_log "
            " WHERE action = 'compliance.review.rejected' AND resource_id = :id "
            " ORDER BY occurred_at DESC LIMIT 1"
        ),
        {"id": item},
    ).scalar()

    assert detail is not None, "una decisión sin rastro en auditoría no es auditable"
    assert detail["rationale"] == MOTIVO


# ─────────────────────────────────────────────────────────────────────────────
# Una decisión es definitiva
# ─────────────────────────────────────────────────────────────────────────────


def test_a_decided_item_cannot_be_decided_again(
    client: TestClient, item: str
) -> None:
    """`decided_by` es quién se hizo responsable. No puede reasignarse después."""
    first = client.post(
        f"/api/v1/review/{item}/approve",
        headers=auth(client, MARIA),
        json={"rationale": "Contenido correcto y respaldado por la ficha vigente"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        f"/api/v1/review/{item}/reject",
        headers=auth(client, MARIA),
        json={"rationale": MOTIVO},
    )
    assert second.status_code == 409
    assert second.json()["details"]["rule"] == "REVIEW_DECISION_IS_FINAL"


def test_deciding_twice_leaves_exactly_one_decision(
    client: TestClient, item: str, privileged_conn: Connection
) -> None:
    """El segundo intento no debe dejar rastro de decisión ni ejemplo."""
    client.post(
        f"/api/v1/review/{item}/approve",
        headers=auth(client, MARIA),
        json={"rationale": "Contenido correcto y respaldado por la ficha vigente"},
    )
    client.post(
        f"/api/v1/review/{item}/reject",
        headers=auth(client, MARIA),
        json={"rationale": MOTIVO},
    )

    row = privileged_conn.execute(
        text(
            "SELECT status, decision_rationale FROM review_items "
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": item},
    ).mappings().one()
    assert row["status"] == "approved"

    examples = privileged_conn.execute(
        text(
            "SELECT count(*) FROM feedback_examples "
            " WHERE review_item_id = CAST(:id AS uuid)"
        ),
        {"id": item},
    ).scalar()
    assert examples == 1


# ─────────────────────────────────────────────────────────────────────────────
# Separación entre quien produce y quien valida
# ─────────────────────────────────────────────────────────────────────────────


def test_compliance_structurally_cannot_generate_what_it_reviews() -> None:
    """La separación no depende de una comprobación: no existe el permiso.

    Es más fuerte que validarlo en tiempo de ejecución. Un control que se
    comprueba puede olvidarse en un endpoint nuevo; un permiso que no se tiene
    no se olvida.
    """
    for permission in (BRIEFING_CREATE, CHAT_USE, SUMMARY_CREATE):
        assert not has_permission("compliance_officer", permission), (
            f"compliance no debería poder generar contenido ({permission})"
        )
    assert has_permission("compliance_officer", REVIEW_DECIDE)

    # Y al revés: quien genera no decide.
    assert not has_permission("sales_rep", REVIEW_DECIDE)


def test_the_author_cannot_decide_on_their_own_content(
    client: TestClient, privileged_conn: Connection
) -> None:
    """Defensa en profundidad sobre la matriz de permisos.

    Hoy es inalcanzable porque compliance no genera contenido. Está aquí porque
    esa imposibilidad vive en una matriz que se edita, y el día que alguien
    conceda `chat.use` a compliance para una demostración, esto es lo que impide
    que la separación desaparezca sin que nadie lo note.
    """
    with create_privileged_engine().begin() as conn:
        item_id = _make_item(conn, tenant_slug="nph_01", author_email=MARIA)

    try:
        response = client.post(
            f"/api/v1/review/{item_id}/approve",
            headers=auth(client, MARIA),
            json={"rationale": "Me apruebo a mí misma el contenido generado"},
        )
        assert response.status_code == 409
        assert response.json()["details"]["rule"] == "SEPARATION_OF_DUTIES"
    finally:
        with create_privileged_engine().begin() as conn:
            conn.execute(
                text("DELETE FROM review_items WHERE id = CAST(:id AS uuid)"),
                {"id": item_id},
            )
            conn.execute(text("DELETE FROM agent_outputs WHERE prompt_name = 'chat'"))


def test_sales_rep_cannot_decide_and_auditor_can_only_read(
    client: TestClient, item: str
) -> None:
    for email in (LAURA, ANA, CARLOS):
        response = client.post(
            f"/api/v1/review/{item}/approve",
            headers=auth(client, email),
            json={"rationale": "Intento de decisión por un rol sin competencia"},
        )
        assert response.status_code == 403, f"{email} no debería poder decidir"

    # El auditor sí lee la cola: su función es comprobar que se revisa.
    assert client.get("/api/v1/review", headers=auth(client, ANA)).status_code == 200
    # El administrador de organización, no: la cola contiene contenido comercial.
    assert client.get("/api/v1/review", headers=auth(client, CARLOS)).status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# Corregir no destruye el original
# ─────────────────────────────────────────────────────────────────────────────


def test_editing_preserves_the_original_and_produces_an_evaluation_case(
    client: TestClient, item: str, privileged_conn: Connection
) -> None:
    """El par (lo que dijo, lo que debía decir) es un caso de prueba.

    Si la edición sobrescribiera el original, cada corrección destruiría
    justamente el dato que la hace útil.
    """
    response = client.post(
        f"/api/v1/review/{item}/edit",
        headers=auth(client, MARIA),
        json={
            "rationale": MOTIVO,
            "edited_content": CORREGIDO,
            "expected_behaviour": (
                "No debe afirmar reducción de riesgo en porcentaje: la fuente "
                "solo reporta mmHg."
            ),
        },
    )
    assert response.status_code == 200, response.text

    row = privileged_conn.execute(
        text(
            "SELECT status, original_content, edited_content "
            "  FROM review_items WHERE id = CAST(:id AS uuid)"
        ),
        {"id": item},
    ).mappings().one()

    assert row["status"] == "edited"
    assert row["original_content"] == ORIGINAL, "el original no debe modificarse"
    assert row["edited_content"] == CORREGIDO

    example = privileged_conn.execute(
        text(
            "SELECT original_answer, corrected_answer, expected_behaviour, "
            "       promoted_to_eval "
            "  FROM feedback_examples WHERE review_item_id = CAST(:id AS uuid)"
        ),
        {"id": item},
    ).mappings().one()

    assert example["original_answer"] == ORIGINAL
    assert example["corrected_answer"] == CORREGIDO
    assert "mmHg" in example["expected_behaviour"]


# ─────────────────────────────────────────────────────────────────────────────
# El ciclo de realimentación
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("approve", {"rationale": "Contenido correcto, respaldado por la ficha vigente"}),
        ("reject", {"rationale": MOTIVO}),
        ("edit", {"rationale": MOTIVO, "edited_content": CORREGIDO}),
        (
            "request-regeneration",
            {"rationale": MOTIVO, "guidance": "Reformular sin la cifra porcentual"},
        ),
    ],
)
def test_every_decision_produces_a_feedback_example(
    client: TestClient,
    item: str,
    privileged_conn: Connection,
    action: str,
    body: dict,
) -> None:
    """También las aprobaciones. Es la mitad que suele faltar.

    Una aprobación sin cambios de algo que el harness marcó es un falso
    positivo. Si solo se recogieran las correcciones, los umbrales solo podrían
    apretarse y nadie tendría el dato para aflojarlos.
    """
    response = client.post(
        f"/api/v1/review/{item}/{action}", headers=auth(client, MARIA), json=body
    )
    assert response.status_code == 200, response.text

    example = privileged_conn.execute(
        text(
            "SELECT corrected_answer, expected_behaviour, promoted_to_eval "
            "  FROM feedback_examples WHERE review_item_id = CAST(:id AS uuid)"
        ),
        {"id": item},
    ).mappings().one()

    assert example["expected_behaviour"], "un ejemplo sin expectativa no evalúa nada"
    # Nada entra en la suite de evaluación por el mero hecho de existir.
    assert example["promoted_to_eval"] is False


def test_an_approval_records_a_false_positive_not_a_correction(
    client: TestClient, item: str, privileged_conn: Connection
) -> None:
    """Un `corrected_answer` igual al original engañaría a la evaluación.

    La suite leería «la respuesta esperada es esta» sobre un texto que en otros
    casos se rechaza. Se deja nulo y la expectativa dice lo que de verdad ocurrió.
    """
    client.post(
        f"/api/v1/review/{item}/approve",
        headers=auth(client, MARIA),
        json={"rationale": "Contenido correcto, respaldado por la ficha vigente"},
    )

    example = privileged_conn.execute(
        text(
            "SELECT corrected_answer, expected_behaviour FROM feedback_examples "
            " WHERE review_item_id = CAST(:id AS uuid)"
        ),
        {"id": item},
    ).mappings().one()

    assert example["corrected_answer"] is None
    assert "falso positivo" in example["expected_behaviour"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Cola y aislamiento
# ─────────────────────────────────────────────────────────────────────────────


def test_queue_is_ordered_by_priority_not_by_arrival(
    client: TestClient, privileged_conn: Connection
) -> None:
    """Una cola ordenada solo por llegada es una bandeja de entrada.

    Lo urgente esperaría detrás de lo trivial que llegó antes, que es justo lo
    que no puede pasar con contenido bloqueado por política.
    """
    engine = create_privileged_engine()
    with engine.begin() as conn:
        # El de prioridad baja se crea primero: si el orden fuera por llegada,
        # saldría delante.
        low = _make_item(
            conn, tenant_slug="nph_01", author_email=LAURA, priority="low"
        )
        high = _make_item(
            conn, tenant_slug="nph_01", author_email=LAURA, priority="high"
        )

    try:
        items = client.get(
            "/api/v1/review?limit=100", headers=auth(client, MARIA)
        ).json()["items"]
        order = [i["id"] for i in items]
        assert order.index(high) < order.index(low)
    finally:
        with engine.begin() as conn:
            conn.execute(
                text("DELETE FROM review_items WHERE id = ANY(CAST(:ids AS uuid[]))"),
                {"ids": [low, high]},
            )
            conn.execute(text("DELETE FROM agent_outputs WHERE prompt_name = 'chat'"))


def test_queue_reports_how_long_things_have_been_waiting(
    client: TestClient, item: str
) -> None:
    """El número que responde a la única pregunta de gestión que importa."""
    body = client.get("/api/v1/review", headers=auth(client, MARIA)).json()
    entry = next(i for i in body["items"] if i["id"] == item)
    assert entry["waiting_hours"] is not None
    assert entry["waiting_hours"] >= 0
    assert "pending" in body["totals"]


def test_review_item_of_another_organization_is_denied_identically(
    client: TestClient, item: str
) -> None:
    """Los identificadores de la cola ajena son información comercial.

    Cuánto contenido problemático genera un competidor se deduce del tamaño de
    su cola, sin leer un solo campo. Por eso el recurso ajeno y el inexistente
    devuelven exactamente la misma respuesta.
    """
    real = client.get(f"/api/v1/review/{item}", headers=auth(client, DIEGO))
    invented = client.get(f"/api/v1/review/{uuid.uuid4()}", headers=auth(client, DIEGO))

    assert real.status_code == invented.status_code == 403
    assert real.json() == invented.json()


def test_deciding_on_another_organizations_item_is_denied_and_changes_nothing(
    client: TestClient, item: str, privileged_conn: Connection
) -> None:
    response = client.post(
        f"/api/v1/review/{item}/approve",
        headers=auth(client, DIEGO),
        json={"rationale": "Intento de decisión desde otra organización"},
    )
    assert response.status_code == 403

    status = privileged_conn.execute(
        text("SELECT status FROM review_items WHERE id = CAST(:id AS uuid)"),
        {"id": item},
    ).scalar()
    assert status == "pending", "el intento denegado no puede haber cambiado nada"


def test_item_detail_carries_everything_needed_to_decide(
    client: TestClient, item: str
) -> None:
    """Una revisión que obliga a abrir tres pestañas se hace en diagonal."""
    body = client.get(f"/api/v1/review/{item}", headers=auth(client, MARIA)).json()

    assert body["original_content"] == ORIGINAL
    assert body["reason"]
    assert body["policy_code"] == "NO_UNAPPROVED_CLAIMS"
    assert body["agent_output"] is not None
    assert body["agent_output"]["model"] == "claude-sonnet-5"
    assert body["agent_output"]["trace_id"]
