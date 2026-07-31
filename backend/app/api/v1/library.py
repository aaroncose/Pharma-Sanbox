"""Biblioteca documental y su ciclo de aprobación.

Este router es donde el argumento del proyecto se vuelve comprobable. El agente
promete citar solo material aprobado y vigente; aquí es donde se decide qué
significa «aprobado» y quién puede decirlo.

El endpoint `/search` merece una nota. No es una función de la aplicación: es
un instrumento. Devuelve lo que el agente vería para una consulta dada, con los
dos rangos —semántico y léxico— por separado y la similitud real. Sin él, la
única forma de saber por qué el agente respondió lo que respondió es leer la
traza *después*. Con él se puede preguntar antes.

Ninguna consulta de este fichero filtra por `tenant_id`. Lo aplica RLS.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.api.deps import CurrentPrincipal, TenantSession, rate_limit, require
from app.core.errors import ConflictError
from app.core.permissions import (
    DOCUMENT_APPROVE,
    DOCUMENT_CREATE,
    DOCUMENT_READ,
    DOCUMENT_WITHDRAW,
)
from app.schemas.library import (
    ApprovalRequest,
    DocumentCreate,
    DocumentUpdate,
    SearchRequest,
    WithdrawalRequest,
)
from app.services import audit, library, retrieval
from app.services.access import fetch_scoped_one
from app.services.audit import AuditEvent

router = APIRouter(prefix="/library", tags=["library"])

# Columnas que se devuelven en el detalle. Se enumeran en lugar de usar `*`
# para que añadir una columna a la tabla sea una decisión de exponerla, no un
# efecto secundario.
_DOCUMENT_COLUMNS = (
    "id, product_id, title, doc_type, status, version, confidentiality, body, "
    "approved_at, approved_by, expires_at, withdrawn_at, withdrawn_reason, "
    "created_by, created_at, updated_at"
)


@router.get(
    "/documents",
    dependencies=[Depends(require(DOCUMENT_READ)), Depends(rate_limit())],
)
def list_documents(
    session: TenantSession,
    status: Annotated[str | None, Query()] = None,
    product_id: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict[str, Any]:
    """Listado con filtros. Devuelve todos los estados, no solo los citables.

    Un compliance officer necesita ver los borradores y lo retirado; esa es su
    función. Lo que ninguna de estas filas implica es que el agente pueda
    citarlas: `citable` lo dice explícitamente por fila, calculado con la misma
    regla que usa el harness, para que la interfaz no tenga que reimplementarla
    y arriesgarse a discrepar.
    """
    rows = session.execute(
        text(
            "SELECT d.id, d.title, d.doc_type, d.status, d.version, "
            "       d.confidentiality, d.approved_at, d.expires_at, "
            "       d.withdrawn_at, d.withdrawn_reason, d.created_at, "
            "       p.name AS product_name, "
            "       (SELECT count(*) FROM document_chunks c "
            "         WHERE c.document_id = d.id) AS chunk_count, "
            "       (d.id IN (SELECT id FROM citable_documents)) AS citable "
            "  FROM documents d "
            "  LEFT JOIN products p ON p.id = d.product_id "
            " WHERE d.deleted_at IS NULL "
            "   AND (CAST(:status AS text) IS NULL OR d.status::text = :status) "
            "   AND (CAST(:product_id AS uuid) IS NULL "
            "        OR d.product_id = CAST(:product_id AS uuid)) "
            "   AND (CAST(:q AS text) IS NULL OR d.title ILIKE '%' || :q || '%') "
            " ORDER BY d.updated_at DESC LIMIT :limit"
        ),
        {"status": status, "product_id": product_id, "q": q, "limit": limit},
    ).mappings().all()

    return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.post(
    "/documents",
    status_code=201,
    dependencies=[Depends(require(DOCUMENT_CREATE)), Depends(rate_limit())],
)
def create_document(
    payload: DocumentCreate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Alta de documento. Nace en borrador, siempre.

    No hay forma de crear un documento ya aprobado, ni siquiera para un
    administrador. La aprobación es una transición con actor y fecha, no un
    valor inicial: si se pudiera fijar al crear, `approved_by` sería un campo
    que el creador se rellena a sí mismo.
    """
    document_id = str(uuid.uuid4())

    session.execute(
        text(
            "INSERT INTO documents "
            "  (id, tenant_id, product_id, title, doc_type, status, version, "
            "   confidentiality, body, expires_at, created_by) "
            "VALUES (CAST(:id AS uuid), CAST(:tenant_id AS uuid), "
            "        CAST(NULLIF(:product_id,'') AS uuid), :title, :doc_type, "
            "        'draft', :version, CAST(:confidentiality AS confidentiality_level), "
            "        :body, CAST(NULLIF(:expires_at,'') AS timestamptz), "
            "        CAST(:created_by AS uuid))"
        ),
        {
            "id": document_id,
            "tenant_id": principal.tenant_id,
            "product_id": payload.product_id or "",
            "title": payload.title,
            "doc_type": payload.doc_type,
            "version": payload.version,
            "confidentiality": payload.confidentiality,
            "body": payload.body,
            "expires_at": payload.expires_at or "",
            "created_by": principal.user_id,
        },
    )

    chunks = library.reindex(
        session,
        tenant_id=principal.tenant_id,
        document_id=document_id,
        body=payload.body,
    )

    return {"id": document_id, "status": "draft", "chunks_indexed": chunks}


@router.get(
    "/documents/{document_id}",
    dependencies=[Depends(require(DOCUMENT_READ)), Depends(rate_limit())],
)
def get_document(
    document_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    document = fetch_scoped_one(
        session,
        principal,
        table="documents",
        resource_id=document_id,
        columns=_DOCUMENT_COLUMNS,
        extra_where="deleted_at IS NULL",
        resource_type="document",
    )

    sections = session.execute(
        text(
            "SELECT ordinal, section, length(content) AS length "
            "  FROM document_chunks WHERE document_id = CAST(:id AS uuid) "
            " ORDER BY ordinal"
        ),
        {"id": document_id},
    ).mappings().all()

    citable = session.execute(
        text("SELECT EXISTS (SELECT 1 FROM citable_documents WHERE id = CAST(:id AS uuid))"),
        {"id": document_id},
    ).scalar()

    product_name = None
    if document.get("product_id"):
        product_name = session.execute(
            text("SELECT name FROM products WHERE id = :p"),
            {"p": document["product_id"]},
        ).scalar()

    return {
        **document,
        "product_name": product_name,
        "citable": bool(citable),
        "chunks": [dict(s) for s in sections],
    }


@router.patch(
    "/documents/{document_id}",
    dependencies=[Depends(require(DOCUMENT_CREATE)), Depends(rate_limit())],
)
def update_document(
    document_id: str,
    payload: DocumentUpdate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Edición. Solo sobre lo que aún no está publicado.

    Editar un documento aprobado se rechaza con 409 en lugar de volverlo a
    poner en borrador automáticamente. La razón es que ese documento puede
    estar ya citado en salidas entregadas: cambiar su cuerpo bajo los pies
    invalidaría citas existentes sin dejar rastro. La vía correcta es una
    versión nueva, que es una fila nueva y deja las citas antiguas intactas.
    """
    document = fetch_scoped_one(
        session,
        principal,
        table="documents",
        resource_id=document_id,
        columns="id, status, body, created_by",
        extra_where="deleted_at IS NULL",
        resource_type="document",
    )

    if document["status"] in ("approved", "withdrawn"):
        raise ConflictError(
            f"Un documento en estado '{document['status']}' no se edita. "
            "Cree una versión nueva.",
            details={"status": document["status"], "rule": "IMMUTABLE_ONCE_PUBLISHED"},
        )

    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return {"id": document_id, "updated": False}

    # La actualización parcial se expresa con `COALESCE` y no componiendo la
    # cláusula SET en Python. Cuesta cinco líneas más de SQL y elimina el único
    # punto del proyecto donde habría que razonar si una interpolación es
    # segura: un campo no enviado llega como NULL y `COALESCE` conserva el valor
    # actual. La consulta es estática, así que la pregunta no se plantea.
    session.execute(
        text(
            "UPDATE documents SET "
            "  title           = COALESCE(:title, title), "
            "  body            = COALESCE(:body, body), "
            "  version         = COALESCE(:version, version), "
            "  confidentiality = COALESCE("
            "        CAST(:confidentiality AS confidentiality_level), confidentiality), "
            "  expires_at      = COALESCE("
            "        CAST(:expires_at AS timestamptz), expires_at), "
            "  updated_at      = now() "
            " WHERE id = CAST(:id AS uuid)"
        ),
        {
            "id": document_id,
            "title": fields.get("title"),
            "body": fields.get("body"),
            "version": fields.get("version"),
            "confidentiality": fields.get("confidentiality"),
            "expires_at": fields.get("expires_at"),
        },
    )

    chunks = None
    if "body" in fields:
        chunks = library.reindex(
            session,
            tenant_id=principal.tenant_id,
            document_id=document_id,
            body=fields["body"],
        )

    return {"id": document_id, "updated": True, "chunks_indexed": chunks}


@router.post(
    "/documents/{document_id}/submit",
    dependencies=[Depends(require(DOCUMENT_CREATE)), Depends(rate_limit())],
)
def submit_for_review(
    document_id: str,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Borrador → pendiente de revisión."""
    document = fetch_scoped_one(
        session,
        principal,
        table="documents",
        resource_id=document_id,
        columns="id, status",
        extra_where="deleted_at IS NULL",
        resource_type="document",
    )

    if document["status"] != "draft":
        raise ConflictError(
            f"Solo un borrador se envía a revisión (estado actual: {document['status']})"
        )

    session.execute(
        text(
            "UPDATE documents SET status = 'pending_review', updated_at = now() "
            "WHERE id = CAST(:id AS uuid)"
        ),
        {"id": document_id},
    )
    return {"id": document_id, "status": "pending_review"}


@router.post(
    "/documents/{document_id}/approve",
    dependencies=[Depends(require(DOCUMENT_APPROVE)), Depends(rate_limit())],
)
def approve_document(
    document_id: str,
    payload: ApprovalRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Aprobación. El momento en que un documento pasa a ser citable.

    Dos controles independientes, y hacen falta los dos:

      · La restricción `documents_approved_needs_provenance` garantiza que
        exista aprobador y fecha. La base de datos no puede saber **quién** es
        el actor, solo ve el valor escrito.
      · `assert_can_approve` garantiza que ese aprobador no sea el autor.

    Con el primero solo, alguien podría aprobar su propio documento y la fila
    seguiría siendo válida.
    """
    document = fetch_scoped_one(
        session,
        principal,
        table="documents",
        resource_id=document_id,
        columns="id, title, status, version, created_by",
        extra_where="deleted_at IS NULL",
        resource_type="document",
    )

    library.assert_can_approve(document, principal.user_id)

    session.execute(
        text(
            "UPDATE documents SET status = 'approved', approved_at = now(), "
            "       approved_by = CAST(:approver AS uuid), updated_at = now() "
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": document_id, "approver": principal.user_id},
    )

    audit.record(
        session,
        AuditEvent(
            action=audit.DOCUMENT_APPROVED,
            outcome="success",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            resource_type="document",
            resource_id=document_id,
            resource_tenant_id=principal.tenant_id,
            decision_code="DOCUMENT_APPROVED",
            exposed_field_count=0,
            client_fingerprint=principal.fingerprint,
            detail={
                "title": document["title"],
                "version": document["version"],
                "previous_status": document["status"],
                "author": str(document["created_by"]),
                "note": payload.note,
            },
        ),
    )

    return {"id": document_id, "status": "approved", "citable": True}


@router.post(
    "/documents/{document_id}/withdraw",
    dependencies=[Depends(require(DOCUMENT_WITHDRAW)), Depends(rate_limit())],
)
def withdraw_document(
    document_id: str,
    payload: WithdrawalRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Retirada inmediata. Escenario 1 del Failure Lab.

    No borra ni desindexa. El documento sigue en la tabla y sus fragmentos
    siguen existiendo; lo que cambia es que deja de estar en
    `citable_documents`, y esa vista es la única puerta del agente a la
    biblioteca. El efecto es inmediato en la consulta siguiente sin ejecutar
    ningún proceso de limpieza — que es justo lo que hay que poder afirmar
    cuando el material retirado es una cifra de eficacia incorrecta.

    Las salidas que ya lo citaron conservan su copia congelada. Retirar no
    reescribe el pasado: hace que el pasado se pueda auditar.
    """
    document = fetch_scoped_one(
        session,
        principal,
        table="documents",
        resource_id=document_id,
        columns="id, title, status, version",
        extra_where="deleted_at IS NULL",
        resource_type="document",
    )

    library.assert_can_withdraw(document, payload.reason)

    session.execute(
        text(
            "UPDATE documents SET status = 'withdrawn', withdrawn_at = now(), "
            "       withdrawn_reason = :reason, updated_at = now() "
            " WHERE id = CAST(:id AS uuid)"
        ),
        {"id": document_id, "reason": payload.reason},
    )

    # Cuántas salidas ya entregadas citaron este documento. Es el dato que
    # convierte la retirada en una acción informada: retirar un documento que
    # nadie citó y retirar uno que sostiene doce briefings entregados son dos
    # situaciones muy distintas, y quien retira debe saber en cuál está.
    affected = session.execute(
        text(
            "SELECT count(DISTINCT agent_output_id) FROM agent_output_sources "
            " WHERE document_id = CAST(:id AS uuid)"
        ),
        {"id": document_id},
    ).scalar()

    audit.record(
        session,
        AuditEvent(
            action=audit.DOCUMENT_WITHDRAWN,
            outcome="success",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            resource_type="document",
            resource_id=document_id,
            resource_tenant_id=principal.tenant_id,
            decision_code="DOCUMENT_WITHDRAWN",
            exposed_field_count=0,
            client_fingerprint=principal.fingerprint,
            detail={
                "title": document["title"],
                "version": document["version"],
                "reason": payload.reason,
                "previously_cited_by_outputs": int(affected or 0),
            },
        ),
    )

    return {
        "id": document_id,
        "status": "withdrawn",
        "citable": False,
        "previously_cited_by_outputs": int(affected or 0),
    }


@router.post(
    "/search",
    dependencies=[Depends(require(DOCUMENT_READ)), Depends(rate_limit())],
)
def search_library(
    payload: SearchRequest,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> dict[str, Any]:
    """Lo que el agente vería para esta consulta. Ni más ni menos.

    Usa exactamente la misma función que el harness, no una parecida. Si usara
    una consulta propia, esta pantalla enseñaría algo que el agente no ve, y
    sería peor que no tenerla: daría confianza infundada.

    Se devuelven los dos rangos por separado además de la puntuación fusionada,
    porque son la respuesta a preguntas distintas. `semantic_rank` vacío y
    `lexical_rank` a 1 significa que el fragmento entró por coincidencia exacta
    de término —un código de estudio, un nombre de producto— que es
    precisamente el caso que la búsqueda vectorial sola se pierde.
    """
    chunks = retrieval.search(
        session,
        query=payload.query,
        product_id=payload.product_id,
        limit=payload.limit,
    )
    relevance = retrieval.relevance_of(chunks)

    audit.record(
        session,
        AuditEvent(
            action=audit.DOCUMENT_SEARCH,
            outcome="success",
            trace_id=principal.trace_id,
            tenant_id=principal.tenant_id,
            actor_user_id=principal.user_id,
            actor_role=principal.role,
            resource_type="document_search",
            exposed_field_count=len(chunks),
            client_fingerprint=principal.fingerprint,
            detail={
                "query_length": len(payload.query),
                "results": len(chunks),
                "relevance": relevance,
            },
        ),
    )

    return {
        "query": payload.query,
        "relevance": relevance,
        # El mismo umbral que aplica el harness. Se devuelve para que la
        # interfaz pueda enseñar «esto habría sido rechazado por evidencia
        # insuficiente» sin tener el número escrito a mano en el frontend.
        "min_relevance": 0.15,
        "would_answer": relevance >= 0.15,
        "results": [
            {
                "source_id": c.source_id,
                "document_id": c.document_id,
                "title": c.document_title,
                "version": c.document_version,
                "section": c.section,
                "excerpt": c.excerpt(300),
                "similarity": c.similarity,
                "semantic_rank": c.semantic_rank,
                "lexical_rank": c.lexical_rank,
                "fused_score": c.score,
            }
            for c in chunks
        ],
    }
