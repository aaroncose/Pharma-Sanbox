"""Registro de auditoría: inmutable, aislado y correlacionable.

Tres propiedades, y cada una se comprueba atacándola.

**Inmutable.** No basta con que la API no ofrezca escritura. Estas pruebas
intentan el `UPDATE` y el `DELETE` directamente contra la base de datos, con el
rol de la aplicación, para comprobar que el trigger los rechaza. Un registro de
auditoría que solo es de solo lectura porque nadie escribió el endpoint es un
registro de auditoría hasta que alguien lo escriba.

**Aislado.** Un intento cruzado deja rastro en la organización que lo hizo —es
actividad suya y debe poder investigarla— y no en la que lo sufrió. Lo segundo
puede parecer una carencia; es lo correcto: ese registro pertenece a quien lo
generó, y enseñarlo diría quién intentó qué desde fuera.

**Correlacionable.** Un identificador de traza une la salida, sus pasos y sus
eventos. Sin eso, «¿por qué el sistema dijo esto?» solo se puede contestar
especulando.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.ratelimit import get_rate_limiter
from app.db.seed import DEMO_PASSWORD
from app.db.session import SessionFactory, TenantContext, apply_tenant_context
from app.main import app

LAURA = "laura.garcia@novapharma.demo"    # sales_rep — sin audit.read
MARIA = "maria.ruiz@novapharma.demo"      # compliance — lee y exporta
ANA = "ana.serra@novapharma.demo"         # auditor — lee y exporta
CARLOS = "carlos.vidal@novapharma.demo"   # org_admin — lee, NO exporta
SOFIA = "sofia.marin@biohealth.demo"      # comercial de la otra organización


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


# ─────────────────────────────────────────────────────────────────────────────
# Inmutabilidad
# ─────────────────────────────────────────────────────────────────────────────


def test_the_api_exposes_no_way_to_write_to_the_audit_log() -> None:
    """Ninguna ruta del registro acepta escritura.

    Se comprueba sobre las rutas declaradas, no probando URLs a mano: así, un
    endpoint de escritura añadido mañana rompe esta prueba aunque nadie se
    acuerde de venir aquí.
    """
    escrituras = [
        (route.path, sorted(route.methods))
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/v1/audit")
        and getattr(route, "methods", set()) - {"GET", "HEAD", "OPTIONS"}
    ]
    assert not escrituras, f"el registro de auditoría acepta escrituras: {escrituras}"


MUTACIONES = (
    "UPDATE audit_log SET outcome = 'success' WHERE id = :id",
    "DELETE FROM audit_log WHERE id = :id",
)


@pytest.mark.parametrize("sentencia", MUTACIONES, ids=["update", "delete"])
def test_the_application_role_has_no_privilege_to_mutate_the_audit_log(
    tenant_ids: dict[str, str], sentencia: str
) -> None:
    """Primera capa: el `GRANT` está revocado.

    El rol de la aplicación —el que usa toda la API— no tiene privilegio de
    `UPDATE` ni `DELETE` sobre la tabla. El intento se rechaza antes de llegar
    al trigger.
    """
    session = SessionFactory()
    session.begin()
    apply_tenant_context(
        session,
        TenantContext(tenant_id=tenant_ids["nph_01"], user_id=None, role="auditor"),
    )
    try:
        entry_id = session.execute(text("SELECT id FROM audit_log LIMIT 1")).scalar()
        assert entry_id, "no hay eventos que intentar modificar"

        with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
            session.execute(text(sentencia), {"id": entry_id})
            session.flush()

        assert "permission denied" in str(excinfo.value).lower()
    finally:
        session.rollback()
        session.close()


@pytest.mark.parametrize("sentencia", MUTACIONES, ids=["update", "delete"])
def test_even_the_owner_role_is_stopped_by_the_append_only_trigger(
    privileged_conn: Connection, sentencia: str
) -> None:
    """Segunda capa: el trigger, que sí tiene el privilegio, lo rechaza igual.

    Esta es la que importa. El primer control es un `GRANT`, y un `GRANT` se
    concede: basta un `ALTER` para deshacerlo, y a partir de ahí la
    inmutabilidad dependería de que nadie lo hiciera. El trigger rechaza la
    operación **incluso para el propietario de la tabla**, que es el rol con el
    que se ejecutan las migraciones y el sembrado.

    Se prueban las dos capas por separado a propósito: comprobar solo el
    resultado —«no se pudo modificar»— dejaría pasar el día en que una de ellas
    desaparezca y la otra siga tapando el hueco.
    """
    entry_id = privileged_conn.execute(
        text("SELECT id FROM audit_log LIMIT 1")
    ).scalar()
    assert entry_id, "no hay eventos que intentar modificar"

    transaccion = privileged_conn.begin_nested()
    try:
        with pytest.raises((ProgrammingError, DBAPIError)) as excinfo:
            privileged_conn.execute(text(sentencia), {"id": entry_id})

        mensaje = str(excinfo.value).lower()
        assert "append-only" in mensaje, (
            f"esperaba el rechazo del trigger, llegó: {mensaje[:200]}"
        )
    finally:
        transaccion.rollback()


# ─────────────────────────────────────────────────────────────────────────────
# Quién puede leer y quién puede exportar
# ─────────────────────────────────────────────────────────────────────────────


def test_a_sales_rep_cannot_read_the_audit_log(client: TestClient) -> None:
    """Quien genera la actividad no audita la actividad."""
    for path in ("/api/v1/audit", "/api/v1/audit/security", "/api/v1/audit/stats"):
        response = client.get(path, headers=auth(client, LAURA))
        assert response.status_code == 403, f"{path} debería estar denegado"


def test_reading_and_exporting_are_different_permissions(client: TestClient) -> None:
    """Exportar saca los datos del sistema; leer no.

    El administrador de organización puede consultar el registro y no puede
    llevárselo. Si fueran el mismo permiso, conceder visibilidad implicaría
    conceder una copia.
    """
    assert client.get("/api/v1/audit", headers=auth(client, CARLOS)).status_code == 200
    assert (
        client.get("/api/v1/audit/export", headers=auth(client, CARLOS)).status_code
        == 403
    )

    # Compliance y auditoría sí exportan.
    for email in (MARIA, ANA):
        response = client.get("/api/v1/audit/export", headers=auth(client, email))
        assert response.status_code == 200, f"{email} debería poder exportar"


# ─────────────────────────────────────────────────────────────────────────────
# La exportación se audita a sí misma
# ─────────────────────────────────────────────────────────────────────────────


def test_exporting_the_audit_log_is_itself_audited(
    client: TestClient, privileged_conn: Connection
) -> None:
    """El hueco clásico: el registro lo recoge todo menos los accesos al registro.

    Cuando después hay que responder a «¿quién se llevó una copia de la
    actividad comercial?», esa es justamente la pregunta sin respuesta.
    """
    before = privileged_conn.execute(
        text("SELECT count(*) FROM audit_log WHERE action = 'audit.log.exported'")
    ).scalar()

    response = client.get(
        "/api/v1/audit/export?since_hours=24", headers=auth(client, ANA)
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    # El fichero no debe quedarse en la caché de un proxy corporativo.
    assert response.headers["cache-control"] == "no-store"

    entry = privileged_conn.execute(
        text(
            "SELECT actor_role::text AS actor_role, detail, exposed_field_count "
            "  FROM audit_log WHERE action = 'audit.log.exported' "
            " ORDER BY occurred_at DESC LIMIT 1"
        )
    ).mappings().one()

    after = privileged_conn.execute(
        text("SELECT count(*) FROM audit_log WHERE action = 'audit.log.exported'")
    ).scalar()

    assert after == before + 1
    assert entry["actor_role"] == "auditor"
    # Cuántas filas salieron de verdad: convierte «se exportó» en una
    # afirmación con magnitud.
    assert entry["detail"]["rows"] == int(response.headers["X-Export-Rows"])
    assert entry["exposed_field_count"] > 0


def test_the_export_is_bounded_and_says_so(client: TestClient) -> None:
    """Una exportación sin tope es una copia completa disfrazada de consulta.

    El límite se declara en la respuesta en lugar de recortar en silencio: quien
    exporta tiene que saber si se llevó todo o solo un trozo.
    """
    response = client.get(
        "/api/v1/audit/export?since_hours=8760", headers=auth(client, MARIA)
    )
    assert response.status_code == 200
    assert response.headers["X-Export-Truncated"] in ("true", "false")

    lineas = response.text.strip().splitlines()
    assert lineas[0].startswith("occurred_at,trace_id,action,outcome")
    assert len(lineas) - 1 == int(response.headers["X-Export-Rows"])


def test_the_export_omits_the_free_form_detail_column(client: TestClient) -> None:
    """`detail` no viaja al CSV.

    Es un `jsonb` libre donde acaban motivos de compliance y extractos de
    documentos. Volcarlo en una hoja de cálculo que circula por correo es
    exactamente el tipo de fuga que el resto del sistema evita.
    """
    cabecera = client.get(
        "/api/v1/audit/export?since_hours=24", headers=auth(client, MARIA)
    ).text.splitlines()[0]

    assert "detail" not in cabecera
    assert "client_fingerprint" not in cabecera


# ─────────────────────────────────────────────────────────────────────────────
# Aislamiento
# ─────────────────────────────────────────────────────────────────────────────


def test_each_organization_sees_only_its_own_events(client: TestClient) -> None:
    nova = client.get("/api/v1/audit?limit=200", headers=auth(client, MARIA)).json()
    bio = client.get(
        "/api/v1/audit?limit=200", headers=auth(client, "compliance@biohealth.demo")
    ).json()

    assert nova["count"] > 0
    ids_nova = {e["id"] for e in nova["items"]}
    ids_bio = {e["id"] for e in bio["items"]}
    assert ids_nova.isdisjoint(ids_bio)


def test_a_cross_tenant_attempt_is_recorded_for_the_attacker_not_the_victim(
    client: TestClient, privileged_conn: Connection
) -> None:
    """El intento consta donde tiene que constar.

    Sofía (BioHealth) intenta alcanzar un recurso de NovaPharma. El evento es
    actividad de BioHealth y aparece en **su** registro: es su empleada y su
    incidente. No aparece en el de NovaPharma, porque enseñar quién intentó
    entrar desde fuera es información sobre un tercero.

    Internamente sí se guarda contra quién iba —`resource_tenant_id`—, que es lo
    que permite a la plataforma investigar sin que ninguna de las dos
    organizaciones vea los datos de la otra.
    """
    inventado = str(uuid.uuid4())
    denegado = client.get(
        f"/api/v1/interactions/{inventado}", headers=auth(client, SOFIA)
    )
    assert denegado.status_code == 403

    propio = client.get(
        "/api/v1/audit/security", headers=auth(client, "auditor@biohealth.demo")
    ).json()
    acciones = [e["action"] for e in propio["items"]]
    assert "access.cross_tenant.attempt" in acciones

    ajeno = client.get("/api/v1/audit/security", headers=auth(client, ANA)).json()
    assert inventado not in [e["resource_id"] for e in ajeno["items"]]

    # Y el campo que hace investigable el incidente sí está en la fila.
    fila = privileged_conn.execute(
        text(
            "SELECT exposed_field_count, resource_tenant_id, detail "
            "  FROM audit_log WHERE resource_id = :rid "
            " ORDER BY occurred_at DESC LIMIT 1"
        ),
        {"rid": inventado},
    ).mappings().one()

    # La afirmación comprobable: no salió ni un campo.
    assert fila["exposed_field_count"] == 0
    assert fila["detail"]["resource_exists"] is False


def test_the_security_view_reports_zero_leaked_rows(client: TestClient) -> None:
    """Si un intento denegado tuviera campos expuestos, la promesa estaría rota.

    Se enseña en la propia pantalla y no solo en una prueba que quizá nadie
    ejecuta hoy.
    """
    body = client.get(
        "/api/v1/audit/security?since_hours=8760", headers=auth(client, ANA)
    ).json()

    assert body["leaked_rows"] == 0, (
        "hay eventos denegados con campos expuestos: el aislamiento no se sostiene"
    )
    assert all(e["outcome"] != "success" for e in body["items"])


# ─────────────────────────────────────────────────────────────────────────────
# Correlación
# ─────────────────────────────────────────────────────────────────────────────


def test_every_request_gets_a_distinct_trace_id(client: TestClient) -> None:
    """Regresión: todas las trazas se llamaban `tr_unknown`.

    El middleware ponía el identificador en la cabecera de respuesta y la
    dependencia lo leía de la de petición, que ningún cliente envía. Ninguna
    entrada de auditoría se podía correlacionar con nada, y la segunda ejecución
    del agente chocaba contra la unicidad de `(trace_id, step)`.
    """
    trazas = {
        client.get("/api/v1/audit/stats", headers=auth(client, ANA)).headers[
            "X-Request-Id"
        ]
        for _ in range(3)
    }

    assert len(trazas) == 3, "los identificadores de traza se repiten"
    assert "tr_unknown" not in trazas


def test_a_client_supplied_trace_id_is_honoured(client: TestClient) -> None:
    """Un cliente que envíe su identificador puede enlazar su traza con la nuestra."""
    mio = f"tr_cliente_{uuid.uuid4().hex[:8]}"
    response = client.get(
        "/api/v1/audit/stats",
        headers={**auth(client, ANA), "X-Request-Id": mio},
    )
    assert response.headers["X-Request-Id"] == mio


@pytest.fixture
def traza_completa(tenant_ids: dict[str, str]) -> Iterator[str]:
    """Ejecuta el harness de verdad y devuelve su identificador de traza.

    Se ejecuta el agente en lugar de insertar filas a mano: lo que se comprueba
    es que la reconstrucción sirva sobre lo que el sistema produce realmente, no
    sobre un montaje que podría no parecerse. Con el proveedor determinista, así
    que no cuesta ni depende de la red.
    """
    from app.agent import provider as provider_module
    from app.agent.provider import MockProvider, reset_provider
    from app.agent.runner import get_runner
    from app.agent.schemas import ChatOutput
    from app.services import outputs
    from app.services.access import Principal

    reset_provider()
    provider_module._provider = MockProvider()

    session = SessionFactory()
    session.begin()
    apply_tenant_context(
        session,
        TenantContext(
            tenant_id=tenant_ids["nph_01"], user_id=None, role="sales_rep"
        ),
    )

    user_id = str(
        session.execute(
            text("SELECT id FROM users WHERE lower(email) = :e"), {"e": LAURA}
        ).scalar()
    )
    # El contexto se reaplica con el usuario ya conocido: la escritura de la
    # salida necesita `app.user_id` para las políticas de inserción.
    apply_tenant_context(
        session,
        TenantContext(
            tenant_id=tenant_ids["nph_01"], user_id=user_id, role="sales_rep"
        ),
    )

    trace_id = f"tr_audit_{uuid.uuid4().hex[:10]}"
    resultado = get_runner().run(
        session,
        task="chat",
        model_cls=ChatOutput,
        question="informacion de seguridad aprobada sobre CardioX",
        prompt_variables={
            "tenant_name": "NovaPharma",
            "product_name": "CardioX",
            "question": "informacion de seguridad aprobada sobre CardioX",
        },
        tenant_id=tenant_ids["nph_01"],
        trace_id=trace_id,
    )

    principal = Principal(
        user_id=user_id,
        tenant_id=tenant_ids["nph_01"],
        role="sales_rep",
        trace_id=trace_id,
    )
    outputs.persist_result(
        session,
        principal,
        resultado,
        kind="chat_answer",
        answer_text=resultado.output.answer if resultado.output else "",
    )
    session.commit()
    session.close()

    yield trace_id

    from app.db.session import create_privileged_engine

    with create_privileged_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM agent_traces WHERE trace_id = :t"), {"t": trace_id}
        )
        conn.execute(
            text("DELETE FROM agent_output_sources WHERE agent_output_id IN "
                 "(SELECT id FROM agent_outputs WHERE trace_id = :t)"),
            {"t": trace_id},
        )
        conn.execute(
            text("DELETE FROM review_items WHERE agent_output_id IN "
                 "(SELECT id FROM agent_outputs WHERE trace_id = :t)"),
            {"t": trace_id},
        )
        conn.execute(
            text("DELETE FROM agent_outputs WHERE trace_id = :t"), {"t": trace_id}
        )
    reset_provider()


def test_a_trace_reconstructs_the_whole_decision(
    client: TestClient, traza_completa: str
) -> None:
    """De un identificador a la cadena completa: pasos, eventos, salida y fuentes.

    Es la respuesta a «¿por qué dijo esto?». Sin correlación solo se puede mirar
    el resultado y especular: la recuperación no encontró la sección, o la
    encontró y el modelo no la citó, o la citó y el verificador la rechazó. Son
    tres fallos distintos con tres arreglos distintos, y desde fuera se parecen.
    """
    body = client.get(
        f"/api/v1/audit/trace/{traza_completa}", headers=auth(client, ANA)
    ).json()

    assert body["found"] is True
    assert body["steps"], "una traza sin pasos no explica nada"

    pasos = [s["step"] for s in body["steps"]]
    assert pasos == sorted(pasos), "los pasos deben venir en orden de ejecución"

    # Los tres puntos donde una respuesta puede torcerse tienen que estar
    # representados, o la traza no permite localizar el fallo.
    tipos = {s["step_type"] for s in body["steps"]}
    assert {"retrieval", "llm_call", "policy_check"} <= tipos, (
        f"faltan pasos que hacen diagnosticable el resultado: {sorted(tipos)}"
    )

    # Y la traza enlaza con la salida y con la auditoría bajo el mismo id.
    assert body["output"] is not None
    assert body["events"], "la traza debe correlacionar con eventos de auditoría"
    assert body["total_latency_ms"] >= 0
    assert isinstance(body["cites_changed_documents"], bool)


def test_an_unknown_trace_returns_empty_not_an_error(client: TestClient) -> None:
    """Un identificador de traza no es un recurso con propietario.

    RLS ya garantiza que solo se vean las filas de la organización, y los
    identificadores no son adivinables, así que el conjunto vacío es la
    respuesta correcta y no permite enumerar nada.
    """
    body = client.get(
        f"/api/v1/audit/trace/tr_{uuid.uuid4().hex[:12]}", headers=auth(client, ANA)
    ).json()

    assert body["found"] is False
    assert body["steps"] == []
    assert body["output"] is None


def test_stats_summarise_activity_without_leaking_content(
    client: TestClient,
) -> None:
    body = client.get(
        "/api/v1/audit/stats?since_hours=8760", headers=auth(client, MARIA)
    ).json()

    assert body["totals"]["events"] > 0
    assert "cost_eur" in body["totals"]
    assert isinstance(body["totals"]["cost_eur"], float), (
        "el coste debe llegar como número, no como cadena"
    )
    assert body["by_action"], "debería haber al menos una acción registrada"
