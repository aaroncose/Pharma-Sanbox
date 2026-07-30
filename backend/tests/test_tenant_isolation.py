"""Aislamiento entre organizaciones.

Es el criterio de aceptación número uno del proyecto, así que estas pruebas
atacan la capa que de verdad lo sostiene: las políticas de PostgreSQL, no los
filtros de la aplicación. Todas se ejecutan con el rol `pharma_app`, el mismo
que usa la API.

Cada prueba está escrita como un intento de fuga, no como una comprobación de
que el camino feliz funciona. La pregunta que responden no es "¿puede Laura ver
sus datos?" sino "¿qué tendría que fallar para que Laura viera los de BioHealth?".
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.db.session import RlsMisconfiguredError, assert_rls_enforced, engine

# ─────────────────────────────────────────────────────────────────────────────
# Configuración del motor
# ─────────────────────────────────────────────────────────────────────────────


def test_app_role_cannot_bypass_rls() -> None:
    """El rol de la API no es superusuario ni tiene BYPASSRLS.

    Si esto falla, todas las demás pruebas de este fichero pasarían igualmente
    y no significarían nada: PostgreSQL habría dejado de aplicar las políticas.
    """
    info = assert_rls_enforced()
    assert info["is_superuser"] is False
    assert info["bypasses_rls"] is False


def test_startup_check_rejects_privileged_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """La salvaguarda de arranque detecta un rol privilegiado."""
    from app.db import session as session_module

    class FakeResult:
        def mappings(self):
            return self

        def one(self):
            return {"role_name": "postgres", "is_superuser": True, "bypasses_rls": False}

    class FakeConn:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> bool:
            return False

    monkeypatch.setattr(session_module.engine, "connect", lambda: FakeConn())
    with pytest.raises(RlsMisconfiguredError):
        session_module.assert_rls_enforced()


def test_no_tenant_table_lacks_rls() -> None:
    """Ninguna tabla con `tenant_id` quedó sin política.

    Cubre el fallo por omisión: añadir una tabla en una migración futura y
    olvidar su política no rompe nada visible, y expone datos entre clientes.
    """
    with engine.connect() as conn:
        gaps = conn.execute(text("SELECT * FROM assert_rls_coverage()")).all()
    assert gaps == [], f"Tablas sin RLS: {[g[0] for g in gaps]}"


# ─────────────────────────────────────────────────────────────────────────────
# Lectura
# ─────────────────────────────────────────────────────────────────────────────


def test_reads_are_scoped_to_own_tenant(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    nova_docs = novapharma_session.execute(
        text("SELECT count(*) FROM documents")
    ).scalar_one()
    bio_docs = biohealth_session.execute(
        text("SELECT count(*) FROM documents")
    ).scalar_one()

    assert nova_docs > 0 and bio_docs > 0
    # Hay 30 documentos en total; ninguna sesión los ve todos.
    assert nova_docs + bio_docs == 30


def test_cannot_read_other_tenant_document_by_id(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    """El ataque directo: conocer el identificador y pedirlo igualmente."""
    target_id = biohealth_session.execute(
        text("SELECT id FROM documents WHERE title LIKE 'Estudio DERMA-204%'")
    ).scalar_one()

    rows = novapharma_session.execute(
        text("SELECT id, title, body FROM documents WHERE id = :id"),
        {"id": target_id},
    ).all()

    # Cero filas, no una fila censurada. La aplicación traduce este vacío a
    # 403 ACCESS_DENIED_CROSS_TENANT sin revelar si el recurso existe.
    assert rows == []


def test_cannot_read_other_tenant_interactions(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    """El escenario literal del enunciado del proyecto."""
    target_id = biohealth_session.execute(
        text("SELECT id FROM interactions LIMIT 1")
    ).scalar_one()

    rows = novapharma_session.execute(
        text("SELECT * FROM interactions WHERE id = :id"), {"id": target_id}
    ).all()
    assert rows == []


def test_cannot_read_other_tenant_users(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    """Los hashes de contraseña de otro cliente no son alcanzables."""
    emails = novapharma_session.execute(
        text("SELECT email FROM users WHERE email LIKE '%biohealth%'")
    ).all()
    assert emails == []


def test_cannot_read_other_tenant_chunks(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    """La recuperación semántica tampoco cruza la frontera.

    Se ejecuta una búsqueda vectorial sin ningún filtro de tenant en el SQL:
    exactamente el fallo que cometería un desarrollador con prisa. RLS lo
    contiene igualmente.
    """
    bio_chunk = biohealth_session.execute(
        text(
            "SELECT embedding FROM document_chunks "
            " WHERE content LIKE '%DermaClear%' LIMIT 1"
        )
    ).scalar_one()

    leaked = novapharma_session.execute(
        text(
            "SELECT content FROM document_chunks "
            " ORDER BY embedding <=> CAST(:target AS vector) LIMIT 20"
        ),
        {"target": bio_chunk},
    ).all()

    assert leaked, "la consulta debería devolver fragmentos del propio tenant"
    for (content,) in leaked:
        assert "DermaClear" not in content
        assert "BioHealth" not in content


def test_anonymous_session_sees_nothing(anonymous_session: Session) -> None:
    """Sin tenant en la sesión no se ve nada, en lugar de verse todo.

    El modo de fallo que previene: un endpoint que olvide aplicar el contexto.
    Que el resultado sea "cero filas" y no "todas las filas" es lo que hace que
    ese olvido sea un error visible en vez de una fuga silenciosa.
    """
    for table in ("documents", "interactions", "users", "healthcare_professionals"):
        count = anonymous_session.execute(
            text(f"SELECT count(*) FROM {table}")  # noqa: S608 — nombre de tabla fijo
        ).scalar_one()
        assert count == 0, f"{table} expuso {count} filas sin contexto de tenant"


# ─────────────────────────────────────────────────────────────────────────────
# Escritura
# ─────────────────────────────────────────────────────────────────────────────


def test_cannot_insert_row_for_another_tenant(
    novapharma_session: Session, tenant_ids: dict[str, str]
) -> None:
    """La mitad WITH CHECK de la política.

    Sin ella, un usuario podría escribir filas marcadas con el tenant de otro
    cliente —contaminando sus datos, sus métricas o su cola de compliance— sin
    llegar a leer nada nunca. Es una fuga en el sentido contrario, y se pasa por
    alto con facilidad.
    """
    with pytest.raises(ProgrammingError) as excinfo:
        novapharma_session.execute(
            text(
                "INSERT INTO tasks (tenant_id, user_id, title) "
                "VALUES (:tenant_id, "
                "        (SELECT id FROM users LIMIT 1), 'inyectada')"
            ),
            {"tenant_id": tenant_ids["bhl_01"]},
        )
    assert "row-level security" in str(excinfo.value).lower()


def test_cannot_update_other_tenant_row(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    target_id = biohealth_session.execute(
        text("SELECT id FROM documents LIMIT 1")
    ).scalar_one()

    result = novapharma_session.execute(
        text("UPDATE documents SET title = 'modificado' WHERE id = :id"),
        {"id": target_id},
    )
    # No lanza excepción: sencillamente no encuentra la fila. El atacante no
    # obtiene ni siquiera la señal de que existe.
    assert result.rowcount == 0


def test_cannot_delete_other_tenant_row(
    novapharma_session: Session, biohealth_session: Session
) -> None:
    target_id = biohealth_session.execute(
        text("SELECT id FROM interactions LIMIT 1")
    ).scalar_one()

    result = novapharma_session.execute(
        text("DELETE FROM interactions WHERE id = :id"), {"id": target_id}
    )
    assert result.rowcount == 0

    # Y sigue existiendo para su dueño: el borrado no ocurrió en ningún sitio.
    still_there = biohealth_session.execute(
        text("SELECT count(*) FROM interactions WHERE id = :id"), {"id": target_id}
    ).scalar_one()
    assert still_there == 1


def test_composite_foreign_keys_block_cross_tenant_links(
    novapharma_session: Session, biohealth_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Las claves foráneas compuestas impiden coser datos de dos clientes.

    Aunque alguien lograse insertar con el tenant correcto, no puede apuntar a
    un recurso ajeno: la referencia es `(tenant_id, id)`, no solo `id`.
    """
    bio_hcp = biohealth_session.execute(
        text("SELECT id FROM healthcare_professionals LIMIT 1")
    ).scalar_one()

    # Es una violación de integridad referencial, no de política RLS: la fila
    # pasa el WITH CHECK porque su `tenant_id` es el correcto, y aun así el
    # motor la rechaza porque `(tenant_id, hcp_id)` no existe. Las dos defensas
    # son independientes y esa independencia es justamente el objetivo.
    with pytest.raises(IntegrityError) as excinfo:
        novapharma_session.execute(
            text(
                "INSERT INTO tasks (tenant_id, user_id, hcp_id, title) "
                "VALUES (:tenant_id, (SELECT id FROM users LIMIT 1), :hcp_id, 'cruzada')"
            ),
            {"tenant_id": tenant_ids["nph_01"], "hcp_id": bio_hcp},
        )
    assert "foreign key" in str(excinfo.value).lower()


# ─────────────────────────────────────────────────────────────────────────────
# Auditoría
# ─────────────────────────────────────────────────────────────────────────────


def test_audit_log_is_append_only(novapharma_session: Session) -> None:
    """Un log que se puede editar no es un log de auditoría."""
    novapharma_session.execute(
        text(
            "INSERT INTO audit_log (tenant_id, trace_id, action, outcome) "
            "VALUES (current_setting('app.tenant_id')::uuid, 'tr_test', "
            "        'test.append_only', 'success')"
        )
    )
    entry_id = novapharma_session.execute(
        text("SELECT id FROM audit_log WHERE trace_id = 'tr_test'")
    ).scalar_one()

    with pytest.raises(ProgrammingError):
        novapharma_session.execute(
            text("UPDATE audit_log SET action = 'manipulado' WHERE id = :id"),
            {"id": entry_id},
        )


def test_audit_can_record_denied_cross_tenant_attempt(
    novapharma_session: Session, tenant_ids: dict[str, str]
) -> None:
    """Se puede registrar un intento contra otro tenant.

    La política de escritura de `audit_log` es deliberadamente permisiva: el
    sistema tiene que poder anotar quién intentó alcanzar qué, incluso cuando el
    actor no tiene derecho a ver nada de ese tenant. Esa es la fila más
    importante del log, y una política simétrica la habría impedido.
    """
    novapharma_session.execute(
        text(
            "INSERT INTO audit_log (tenant_id, trace_id, action, outcome, "
            "                       decision_code, resource_tenant_id, "
            "                       exposed_field_count) "
            "VALUES (current_setting('app.tenant_id')::uuid, 'tr_denied', "
            "        'access.cross_tenant.attempt', 'denied', "
            "        'ACCESS_DENIED_CROSS_TENANT', :other, 0)"
        ),
        {"other": tenant_ids["bhl_01"]},
    )
    exposed = novapharma_session.execute(
        text("SELECT exposed_field_count FROM audit_log WHERE trace_id = 'tr_denied'")
    ).scalar_one()
    assert exposed == 0


# ─────────────────────────────────────────────────────────────────────────────
# Reglas documentales
# ─────────────────────────────────────────────────────────────────────────────


def test_citable_documents_excludes_unusable_states(novapharma_session: Session) -> None:
    """La vista es la única puerta por la que el agente ve documentación."""
    rows = novapharma_session.execute(
        text("SELECT status, withdrawn_at, expires_at FROM citable_documents")
    ).mappings().all()

    assert rows, "NovaPharma debería tener documentos citables"
    for row in rows:
        assert row["status"] == "approved"
        assert row["withdrawn_at"] is None


def test_withdrawn_document_is_not_citable(novapharma_session: Session) -> None:
    """El material retirado existe en la biblioteca pero no para el agente."""
    in_library = novapharma_session.execute(
        text("SELECT count(*) FROM documents "
             " WHERE title = 'Material comercial histórico CardioX'")
    ).scalar_one()
    citable = novapharma_session.execute(
        text("SELECT count(*) FROM citable_documents "
             " WHERE title = 'Material comercial histórico CardioX'")
    ).scalar_one()

    assert in_library == 1, "debe seguir visible en la biblioteca y en auditoría"
    assert citable == 0, "no debe ser alcanzable por el agente"


def test_expired_document_is_not_citable(novapharma_session: Session) -> None:
    """Caducar excluye igual que retirar, aunque el estado siga siendo aprobado."""
    row = novapharma_session.execute(
        text(
            "SELECT status, expires_at < now() AS caducado "
            "  FROM documents WHERE title = 'Campaña comercial CardioX 2025'"
        )
    ).mappings().one()
    assert row["status"] == "approved"
    assert row["caducado"] is True

    citable = novapharma_session.execute(
        text("SELECT count(*) FROM citable_documents "
             " WHERE title = 'Campaña comercial CardioX 2025'")
    ).scalar_one()
    assert citable == 0


def test_draft_document_is_not_citable(novapharma_session: Session) -> None:
    citable = novapharma_session.execute(
        text("SELECT count(*) FROM citable_documents WHERE status <> 'approved'")
    ).scalar_one()
    assert citable == 0


def test_approved_document_requires_provenance(novapharma_session: Session) -> None:
    """No puede existir un documento aprobado sin aprobador ni fecha."""
    orphans = novapharma_session.execute(
        text(
            "SELECT count(*) FROM documents "
            " WHERE status = 'approved' "
            "   AND (approved_by IS NULL OR approved_at IS NULL)"
        )
    ).scalar_one()
    assert orphans == 0
