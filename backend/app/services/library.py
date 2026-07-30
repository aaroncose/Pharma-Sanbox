"""Biblioteca documental: troceado, indexación y ciclo de aprobación.

Tres decisiones que sostienen el resto del sistema.

**Se indexa todo, se filtra al leer.** Los fragmentos de un borrador se
calculan e insertan igual que los de un documento aprobado. Podría parecer más
seguro indexar solo lo aprobado, pero sería peor: convertiría la indexación en
el control de acceso, y entonces cualquier fallo en el flujo de aprobación
—un `UPDATE` que no dispara la reindexación, una excepción a mitad— dejaría
material no aprobado disponible sin que nada lo advirtiera. El control vive en
la vista `citable_documents`, que se aplica en **toda** lectura del agente. Un
punto único, en el lado de la lectura, que no se puede rodear.

**El autor no aprueba su propio documento.** Es la regla de los cuatro ojos.
No es burocracia: sin ella, «aprobado» solo significa que alguien pulsó un
botón, y todo el argumento del sistema —que el agente solo cita material
aprobado— se apoya en una firma que el propio interesado se puso.

**La retirada tiene efecto inmediato y exige motivo.** Es el escenario 1 del
Failure Lab. Retirar no borra: el documento sigue existiendo, deja de ser
citable en la siguiente consulta, y las salidas que ya lo citaron conservan la
foto de cómo estaba cuando lo citaron.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.embeddings import get_embedding_provider
from app.core.errors import ConflictError, ValidationFailedError
from app.core.logging import get_logger

log = get_logger("library")


def chunk_document(body: str) -> list[tuple[str | None, str]]:
    """Divide el documento por secciones marcadas con `§`.

    Se trocea por estructura y no por número de caracteres a propósito: en este
    corpus las secciones son unidades semánticas reales («§ 4.8. Seguridad»), y
    conservarlas permite citar la sección exacta en la interfaz. Un troceado por
    ventana fija partiría una sección de seguridad por la mitad y produciría
    citas que no se sostienen solas.
    """
    parts = re.split(r"\n(?=§)", body.strip())
    chunks: list[tuple[str | None, str]] = []
    for part in parts:
        content = part.strip()
        if not content:
            continue
        header = content.splitlines()[0]
        section = header.lstrip("§ ").strip() if header.startswith("§") else None
        chunks.append((section, content))
    return chunks


def reindex(session: Session, *, tenant_id: str, document_id: str, body: str) -> int:
    """Recalcula los fragmentos de un documento. Devuelve cuántos escribió.

    Borra y reinserta en lugar de intentar una actualización diferencial. Con
    documentos de este tamaño la diferencia de coste es irrelevante, y la
    alternativa deja la puerta abierta a que un fragmento viejo sobreviva a una
    edición y siga siendo recuperable: exactamente el fallo que el sistema
    promete no tener.
    """
    session.execute(
        text("DELETE FROM document_chunks WHERE document_id = CAST(:id AS uuid)"),
        {"id": document_id},
    )

    pieces = chunk_document(body)
    if not pieces:
        return 0

    embedder = get_embedding_provider()
    vectors = embedder.embed_batch([content for _, content in pieces])

    for ordinal, ((section, content), vector) in enumerate(
        zip(pieces, vectors, strict=True), start=1
    ):
        session.execute(
            text(
                "INSERT INTO document_chunks "
                "  (tenant_id, document_id, ordinal, section, content, embedding) "
                "VALUES (CAST(:tenant_id AS uuid), CAST(:document_id AS uuid), "
                "        :ordinal, :section, :content, CAST(:embedding AS vector))"
            ),
            {
                "tenant_id": tenant_id,
                "document_id": document_id,
                "ordinal": ordinal,
                "section": section,
                "content": content,
                "embedding": "[" + ",".join(f"{v:.6f}" for v in vector) + "]",
            },
        )

    log.info("document_reindexed", document_id=document_id, chunks=len(pieces))
    return len(pieces)


def assert_can_approve(document: dict[str, Any], approver_user_id: str) -> None:
    """Regla de los cuatro ojos y transiciones válidas.

    Se comprueba aquí, sobre la fila ya leída, y no con una restricción de base
    de datos, porque la base de datos no conoce la identidad del actor: solo ve
    el valor que se escribe en `approved_by`. La restricción de la tabla
    garantiza que *haya* aprobador; esta función garantiza que **no sea el
    autor**. Son dos controles distintos y hacen falta los dos.
    """
    if document["status"] == "approved":
        raise ConflictError("El documento ya está aprobado")

    if document["status"] == "withdrawn":
        raise ConflictError(
            "Un documento retirado no se puede aprobar. Cree una versión nueva."
        )

    created_by = str(document.get("created_by") or "")
    if created_by and created_by == approver_user_id:
        raise ValidationFailedError(
            "El autor de un documento no puede aprobarlo",
            details={
                "rule": "SEPARATION_OF_DUTIES",
                "explanation": (
                    "La aprobación debe hacerla una persona distinta de quien "
                    "redactó el contenido."
                ),
            },
        )


def assert_can_withdraw(document: dict[str, Any], reason: str) -> None:
    """Retirar exige motivo escrito y solo aplica a lo que estaba publicado."""
    if document["status"] == "withdrawn":
        raise ConflictError("El documento ya está retirado")

    if len(reason.strip()) < 10:
        raise ValidationFailedError(
            "La retirada de un documento requiere un motivo de al menos 10 caracteres",
            details={"rule": "WITHDRAWAL_NEEDS_REASON"},
        )
