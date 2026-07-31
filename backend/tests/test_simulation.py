"""Simulador conversacional: práctica bajo las reglas reales.

La afirmación que sostiene el módulo es que **lo que dice el comercial se juzga
con el mismo motor de políticas que gobierna al agente en producción**. Si fuera
falsa, el simulador entrenaría para un examen que no existe, y estas pruebas
existen sobre todo para comprobar que sigue siendo cierta.

El resto son las asimetrías deliberadas: el simulador no ve la biblioteca y el
informe sí; la nota de comunicación la juzga el modelo y la de cumplimiento la
cuenta el código; la nota interna del simulador no llega nunca al comercial.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.agent import provider as provider_module
from app.agent.provider import MockProvider, reset_provider
from app.agent.schemas import ImprovableAnswer
from app.agent.tools.registry import TASK_TOOLSETS, resolve_allowlist
from app.core.ratelimit import get_rate_limiter
from app.db.seed import DEMO_PASSWORD
from app.db.session import (
    SessionFactory,
    TenantContext,
    apply_tenant_context,
    create_privileged_engine,
)
from app.main import app
from app.policies.engine import get_policy_engine
from app.services import simulation

LAURA = "laura.garcia@novapharma.demo"    # sales_rep — usa el simulador
MARIA = "maria.ruiz@novapharma.demo"      # compliance — no genera contenido
SOFIA = "sofia.marin@biohealth.demo"      # otra organización

CARDIOX = "7df40f3d-fe19-4971-9d94-9c03abb3e4a0"
NADAL = "09d5920a-f8f6-4b99-82e5-3f589416b6cf"

# La frase que el proyecto entero existe para detectar.
AFIRMACION_SIN_RESPALDO = (
    "CardioX reduce el riesgo cardiovascular un 37%, es lo mejor que hay."
)
RESPUESTA_PRUDENTE = (
    "El resumen aprobado del estudio CARDIO-101 describe una reducción media de "
    "presión arterial. No puedo hacer recomendaciones para pacientes concretos."
)


@pytest.fixture(scope="module")
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_rate_limits() -> None:
    get_rate_limiter()._client.flushdb()


@pytest.fixture(autouse=True)
def _deterministic_provider() -> Iterator[None]:
    """El proveedor simulado: sin red, sin coste y reproducible."""
    reset_provider()
    provider_module._provider = MockProvider()
    yield
    reset_provider()


def auth(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def sim(client: TestClient) -> Iterator[str]:
    response = client.post(
        "/api/v1/simulations",
        headers=auth(client, LAURA),
        json={
            "hcp_id": NADAL,
            "product_id": CARDIOX,
            "scenario": "Duda sobre la evidencia de eficacia",
            "objective": "Explicar la evidencia aprobada sin recomendaciones clínicas",
            "attitude": "escéptico",
        },
    )
    assert response.status_code == 201, response.text
    simulation_id = response.json()["id"]

    yield simulation_id

    with create_privileged_engine().begin() as conn:
        conn.execute(
            text("DELETE FROM simulations WHERE id = CAST(:id AS uuid)"),
            {"id": simulation_id},
        )


@pytest.fixture
def nova_session(tenant_ids: dict[str, str]) -> Iterator[Session]:
    session = SessionFactory()
    session.begin()
    apply_tenant_context(
        session,
        TenantContext(tenant_ids["nph_01"], None, "sales_rep"),
    )
    try:
        yield session
    finally:
        session.rollback()
        session.close()


# ─────────────────────────────────────────────────────────────────────────────
# La afirmación central: mismas reglas que en producción
# ─────────────────────────────────────────────────────────────────────────────


def test_the_rep_is_judged_by_the_production_policy_engine(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Lo que marca al agente marca a la persona, y por el mismo código.

    Se compara la evaluación del turno del comercial contra `evaluate_response`
    —la que gobierna la salida del agente— sobre el mismo texto. Si divergieran,
    el simulador estaría entrenando para un examen distinto del real.
    """
    del_agente = get_policy_engine().evaluate_response(
        nova_session,
        tenant_id=tenant_ids["nph_01"],
        answer=AFIRMACION_SIN_RESPALDO,
        source_count=0,
    )
    del_comercial = simulation.evaluate_rep_turn(
        nova_session,
        tenant_id=tenant_ids["nph_01"],
        utterance=AFIRMACION_SIN_RESPALDO,
    )

    assert del_agente.codes, "la política no se dispara: la prueba no vale nada"
    assert del_comercial.decision.codes == del_agente.codes
    assert del_comercial.flag in del_agente.codes


def test_a_prudent_answer_is_not_flagged(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """El control tiene que distinguir, no marcar todo.

    Un simulador que marca cualquier frase enseña a no hablar, que no es lo que
    se busca.
    """
    evaluacion = simulation.evaluate_rep_turn(
        nova_session,
        tenant_id=tenant_ids["nph_01"],
        utterance=RESPUESTA_PRUDENTE,
    )
    assert evaluacion.flag is None
    assert evaluacion.risk_level == "low"


def test_the_flag_is_recorded_on_the_turn_as_it_happens(
    client: TestClient, sim: str, privileged_conn: Connection
) -> None:
    """La marca se guarda en el momento, no se reconstruye al final.

    Es lo que permite enseñar el riesgo en vivo. Reconstruirla después la haría
    depender de que el modelo del informe se acuerde, y una infracción señalada
    tres turnos más tarde ya se convirtió en costumbre.
    """
    respuesta = client.post(
        f"/api/v1/simulations/{sim}/turns",
        headers=auth(client, LAURA),
        json={"utterance": AFIRMACION_SIN_RESPALDO},
    )
    assert respuesta.status_code == 200, respuesta.text

    cuerpo = respuesta.json()
    assert cuerpo["rep_turn"]["compliance_flag"] == "PRODUCT_CLAIM_REQUIRES_SOURCE"
    assert cuerpo["rep_turn"]["risk_level"] == "high"

    guardado = privileged_conn.execute(
        text(
            "SELECT compliance_flag FROM simulation_turns "
            " WHERE simulation_id = CAST(:s AS uuid) AND speaker = 'rep' "
            " ORDER BY ordinal LIMIT 1"
        ),
        {"s": sim},
    ).scalar()
    assert guardado == "PRODUCT_CLAIM_REQUIRES_SOURCE"


# ─────────────────────────────────────────────────────────────────────────────
# La asimetría con la biblioteca
# ─────────────────────────────────────────────────────────────────────────────


def test_the_simulator_gets_no_library_and_the_debrief_does() -> None:
    """La asimetría deliberada, en el único sitio donde se declara.

    El simulador sin biblioteca: un médico real no ha leído el material
    comercial aprobado, y si lo conociera haría preguntas antinaturalmente
    alineadas con las respuestas disponibles.

    El informe con biblioteca: «qué fuente deberías haber citado» no se puede
    responder sin saber cuál existía.
    """
    assert TASK_TOOLSETS["simulator"] == ()
    assert "search_documents" in TASK_TOOLSETS["simulation_debrief"]

    assert resolve_allowlist(task="simulator", role="sales_rep") == []
    herramientas = resolve_allowlist(task="simulation_debrief", role="sales_rep")
    assert [t.name for t in herramientas] == ["search_documents"]


def test_the_debrief_offers_sources_the_rep_could_have_used(
    client: TestClient, sim: str
) -> None:
    """Es lo que convierte el informe en enseñanza y no en una nota."""
    client.post(
        f"/api/v1/simulations/{sim}/turns",
        headers=auth(client, LAURA),
        json={"utterance": AFIRMACION_SIN_RESPALDO},
    )
    informe = client.post(
        f"/api/v1/simulations/{sim}/end", headers=auth(client, LAURA)
    ).json()

    assert informe["sources_you_could_have_used"], (
        "sin fuentes, el informe dice qué está mal y no cómo arreglarlo"
    )
    assert all(
        s["source_id"].startswith("doc:")
        for s in informe["sources_you_could_have_used"]
    )


# ─────────────────────────────────────────────────────────────────────────────
# La puntuación se cuenta, no se genera
# ─────────────────────────────────────────────────────────────────────────────


def test_the_compliance_score_is_reproducible_from_the_recorded_flags() -> None:
    """La nota de cumplimiento se puede reconstruir desde la base de datos.

    Pedirle una única cifra al modelo produciría un número que parece preciso,
    no es reproducible entre ejecuciones y mezcla un juicio cualitativo con algo
    que se cuenta. El comercial al que se le dice «62» tiene derecho a saber de
    dónde sale.
    """
    turnos = [
        {"ordinal": 1, "speaker": "hcp", "compliance_flag": None, "risk_level": None},
        {
            "ordinal": 2,
            "speaker": "rep",
            "compliance_flag": "PRODUCT_CLAIM_REQUIRES_SOURCE",
            "risk_level": "high",
        },
        {"ordinal": 3, "speaker": "hcp", "compliance_flag": None, "risk_level": None},
        {"ordinal": 4, "speaker": "rep", "compliance_flag": None, "risk_level": None},
    ]

    nota = simulation.compliance_score(turnos, handled_out_of_bounds_well=False)

    assert nota.score == 75  # 100 - 25 por una infracción grave
    assert nota.flagged_turns == 1
    assert nota.total_rep_turns == 2
    assert nota.penalties == [
        {
            "turn_ordinal": 2,
            "policy_code": "PRODUCT_CLAIM_REQUIRES_SOURCE",
            "severity": "high",
            "penalty": 25,
        }
    ]

    # Dos veces sobre la misma entrada: el mismo número.
    assert (
        simulation.compliance_score(turnos, handled_out_of_bounds_well=False).score
        == nota.score
    )


def test_recognising_a_limit_is_rewarded() -> None:
    """Sin la bonificación, la nota óptima se saca hablando poco.

    Reconocer que algo no se puede responder es la habilidad que este
    entrenamiento busca; si no puntuara, el incentivo sería el silencio.
    """
    turnos = [{"ordinal": 1, "speaker": "rep", "compliance_flag": None}]

    callado = simulation.compliance_score(turnos, handled_out_of_bounds_well=False)
    reconociendo = simulation.compliance_score(
        turnos, handled_out_of_bounds_well=True
    )

    assert reconociendo.score >= callado.score
    assert reconociendo.bonus_applied == simulation.OUT_OF_BOUNDS_BONUS


def test_compliance_weighs_more_than_communication() -> None:
    """Expresarse con torpeza y afirmar algo no aprobado no son lo mismo.

    Lo primero pierde una oportunidad comercial; lo segundo crea un problema
    regulatorio. La nota no debería sugerir que son errores del mismo orden.
    """
    limpio = simulation.compliance_score(
        [{"ordinal": 1, "speaker": "rep", "compliance_flag": None}],
        handled_out_of_bounds_well=False,
    )
    infractor = simulation.compliance_score(
        [
            {
                "ordinal": 1,
                "speaker": "rep",
                "compliance_flag": "NO_CLINICAL_RECOMMENDATION",
                "risk_level": "critical",
            }
        ],
        handled_out_of_bounds_well=False,
    )

    torpe_pero_limpio = simulation.overall_score(30, limpio)
    elocuente_pero_infractor = simulation.overall_score(95, infractor)

    assert torpe_pero_limpio > elocuente_pero_infractor, (
        "hablar bien no debería compensar afirmar algo no aprobado"
    )


def test_no_amount_of_eloquence_passes_a_critical_violation() -> None:
    """El cumplimiento limita, no pondera. Es la corrección de un fallo mío.

    El primer diseño era una media ponderada 60/40 a favor del cumplimiento. La
    prueba anterior demostró que la aritmética no sostenía el argumento: una
    infracción crítica restaba 24 puntos de la nota final y el margen entre
    hablar mal y hablar muy bien valía 26, así que existía una cantidad de
    elocuencia que compensaba una infracción regulatoria.

    Mientras las dos notas se sumen, esa cantidad siempre existe. Por eso el
    cumplimiento pasó a ser un techo: la comunicación decide la nota dentro de
    lo que el cumplimiento permite.
    """
    critico = simulation.compliance_score(
        [
            {
                "ordinal": 1,
                "speaker": "rep",
                "compliance_flag": "NO_CLINICAL_RECOMMENDATION",
                "risk_level": "critical",
            }
        ],
        handled_out_of_bounds_well=False,
    )

    # Ni con la comunicación perfecta.
    assert simulation.overall_score(100, critico) <= simulation.SEVERITY_CAP["critical"]
    assert simulation.applicable_cap(critico) == simulation.SEVERITY_CAP["critical"]

    # Y una simulación limpia no tiene techo que la limite.
    limpia = simulation.compliance_score(
        [{"ordinal": 1, "speaker": "rep", "compliance_flag": None}],
        handled_out_of_bounds_well=False,
    )
    assert simulation.applicable_cap(limpia) is None
    assert simulation.overall_score(100, limpia) == 100


def test_naming_the_study_counts_as_attribution(
    nova_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Un comercial cita nombrando el estudio, no con identificadores.

    El primer diseño pasaba `source_count=0` siempre, y marcaba igual la
    afirmación inventada y la respuesta modélica. Un entrenamiento así enseña a
    no mencionar evidencia.

    La atribución se comprueba contra `citable_documents`: nombrar un estudio
    que no existe no cuenta, porque sería premiar justo la invención que el
    sistema persigue.
    """
    reconocidas = simulation.count_verbal_sources(
        nova_session,
        utterance=(
            "Según el resumen aprobado del estudio CARDIO-101, "
            "la reducción fue de 12,4 mmHg"
        ),
    )
    assert reconocidas, "no reconoce una cita verbal legítima"

    inventadas = simulation.count_verbal_sources(
        nova_session,
        utterance="Según el estudio INVENTADO-999, la reducción fue del 37%",
    )
    assert inventadas == [], "nombrar un estudio inexistente no puede contar como cita"


# ─────────────────────────────────────────────────────────────────────────────
# El informe no puede señalar turnos ajenos
# ─────────────────────────────────────────────────────────────────────────────


def test_feedback_anchored_to_someone_elses_turn_is_unanchored() -> None:
    """Regresión de un fallo del modelo real.

    Al informar sobre una simulación, señaló como mejorable el turno 5, que era
    del médico. La observación de fondo era válida —el comercial dejó una
    pregunta sin responder— pero anclada a una frase que el comercial nunca
    dijo. Una interfaz que muestra «tu respuesta del turno 5» sobre la línea del
    interlocutor invalida el informe entero a ojos de quien lo lee.

    Se anula el ancla y se conserva el contenido: descartarlo perdería feedback
    correcto.
    """
    turnos = [
        {"ordinal": 1, "speaker": "hcp"},
        {"ordinal": 2, "speaker": "rep"},
        {"ordinal": 3, "speaker": "hcp"},
        {"ordinal": 4, "speaker": "rep"},
        {"ordinal": 5, "speaker": "hcp"},
    ]
    respuestas = [
        ImprovableAnswer(
            turn_ordinal=2, what_was_said="...", why="...", suggested_rewrite="..."
        ),
        ImprovableAnswer(
            turn_ordinal=5,  # del médico
            what_was_said="...",
            why="dejaste la pregunta sin cerrar",
            suggested_rewrite="...",
        ),
    ]

    corregidas, cuantas = simulation.anchor_improvable_answers(respuestas, turnos)

    assert cuantas == 1
    assert corregidas[0].turn_ordinal == 2, "un ancla válida no se toca"
    assert corregidas[1].turn_ordinal is None, "el ancla falsa se anula"
    # El contenido sobrevive: era feedback correcto mal atribuido.
    assert corregidas[1].why == "dejaste la pregunta sin cerrar"


def test_every_improvable_answer_points_at_a_rep_turn(
    client: TestClient, sim: str
) -> None:
    """La propiedad, comprobada de extremo a extremo."""
    headers = auth(client, LAURA)
    for frase in (AFIRMACION_SIN_RESPALDO, RESPUESTA_PRUDENTE):
        client.post(
            f"/api/v1/simulations/{sim}/turns", headers=headers, json={"utterance": frase}
        )

    informe = client.post(f"/api/v1/simulations/{sim}/end", headers=headers).json()
    transcripcion = client.get(
        f"/api/v1/simulations/{sim}", headers=headers
    ).json()
    validos = {t["ordinal"] for t in transcripcion["turns"] if t["speaker"] == "rep"}

    for mejora in informe["improvable_answers"]:
        assert mejora["turn_ordinal"] is None or mejora["turn_ordinal"] in validos, (
            f"el informe señala el turno {mejora['turn_ordinal']}, que no es del comercial"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Lo que el comercial no debe ver
# ─────────────────────────────────────────────────────────────────────────────


def test_the_simulators_internal_note_never_reaches_the_rep(
    client: TestClient, sim: str, privileged_conn: Connection
) -> None:
    """`internal_note` es por qué el médico preguntó eso.

    Enseñárselo al comercial convertiría el entrenamiento en un examen con las
    respuestas al final. Ni se guarda ni se devuelve.
    """
    respuesta = client.post(
        f"/api/v1/simulations/{sim}/turns",
        headers=auth(client, LAURA),
        json={"utterance": RESPUESTA_PRUDENTE},
    ).json()

    assert "internal_note" not in respuesta["hcp_turn"]

    transcripcion = client.get(
        f"/api/v1/simulations/{sim}", headers=auth(client, LAURA)
    ).json()
    assert all("internal_note" not in t for t in transcripcion["turns"])

    contenidos = privileged_conn.execute(
        text(
            "SELECT content FROM simulation_turns "
            " WHERE simulation_id = CAST(:s AS uuid) AND speaker = 'hcp'"
        ),
        {"s": sim},
    ).scalars().all()
    assert all("internal_note" not in c for c in contenidos)


# ─────────────────────────────────────────────────────────────────────────────
# Ciclo de vida y aislamiento
# ─────────────────────────────────────────────────────────────────────────────


def test_the_hcp_speaks_first(client: TestClient, sim: str) -> None:
    """En una visita real el comercial entra a un despacho donde ya hay alguien.

    Empezar con la pantalla en blanco esperando al comercial entrena una
    situación que no ocurre.
    """
    transcripcion = client.get(
        f"/api/v1/simulations/{sim}", headers=auth(client, LAURA)
    ).json()

    assert transcripcion["turns"][0]["speaker"] == "hcp"
    assert transcripcion["turns"][0]["ordinal"] == 1


def test_an_ended_simulation_accepts_no_more_turns(
    client: TestClient, sim: str
) -> None:
    headers = auth(client, LAURA)
    client.post(
        f"/api/v1/simulations/{sim}/turns", headers=headers,
        json={"utterance": RESPUESTA_PRUDENTE},
    )
    assert client.post(f"/api/v1/simulations/{sim}/end", headers=headers).status_code == 200

    tarde = client.post(
        f"/api/v1/simulations/{sim}/turns", headers=headers,
        json={"utterance": "Una cosa más"},
    )
    assert tarde.status_code == 409

    # Y el informe no se regenera: la nota de una práctica no se reescribe.
    assert client.post(f"/api/v1/simulations/{sim}/end", headers=headers).status_code == 409


def test_compliance_officer_cannot_run_a_simulation(client: TestClient) -> None:
    """Quien revisa no genera contenido, tampoco practicando."""
    response = client.post(
        "/api/v1/simulations",
        headers=auth(client, MARIA),
        json={
            "hcp_id": NADAL,
            "product_id": CARDIOX,
            "scenario": "Escenario de prueba",
            "objective": "Objetivo de prueba",
        },
    )
    assert response.status_code == 403


def test_a_simulation_of_another_organization_is_denied_identically(
    client: TestClient, sim: str
) -> None:
    real = client.get(f"/api/v1/simulations/{sim}", headers=auth(client, SOFIA))
    inventada = client.get(
        f"/api/v1/simulations/{uuid.uuid4()}", headers=auth(client, SOFIA)
    )

    assert real.status_code == inventada.status_code == 403
    assert real.json() == inventada.json()
