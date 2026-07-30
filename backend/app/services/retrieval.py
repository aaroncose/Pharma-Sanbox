"""Recuperación documental híbrida.

Combina búsqueda semántica (pgvector) y léxica (`tsvector`) y fusiona los dos
rankings por rango recíproco.

Por qué híbrida y no solo vectorial. El embebedor local no es un modelo
entrenado: capta reformulación, no sinonimia. En este dominio las consultas que
más importan contienen justamente los términos que un vector aproxima mal —
«CARDIO-101», «sección 4.8», «CardioX»— y para esos la búsqueda léxica de
PostgreSQL es exacta. Al revés, «¿qué se sabe de la tolerabilidad?» no comparte
ninguna palabra con «los eventos adversos descritos son leves» y ahí el vector
sí ayuda. Cada método cubre el punto ciego del otro.

Por qué fusión por rango recíproco y no suma de puntuaciones: las distancias
coseno y las puntuaciones `ts_rank` no son comparables ni tienen la misma
escala, y normalizarlas exige calibración que se desajusta con el corpus. RRF
solo usa la posición en cada lista, así que no necesita que las puntuaciones
sean comparables.

**Ninguna consulta filtra por tenant.** Lo hace RLS. La vista
`citable_documents` aplica además la regla documental —aprobado, vigente, no
retirado— de modo que aquí no hay forma de recuperar material que el agente no
pueda citar, ni siquiera equivocándose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.embeddings import get_embedding_provider
from app.core.logging import get_logger

log = get_logger("retrieval")

# Constante de la fusión por rango recíproco. 60 es el valor del artículo
# original y funciona bien sin ajuste: amortigua la diferencia entre las
# primeras posiciones para que un primer puesto en una lista no aplaste a un
# segundo puesto en la otra.
RRF_K = 60


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    document_version: str
    document_status: str
    section: str | None
    content: str
    approved_at: str | None
    # Posición en cada ranking, para poder explicar por qué se recuperó.
    semantic_rank: int | None = None
    lexical_rank: int | None = None
    # Similitud coseno real en 0..1. Es lo que permite decir "no hay nada
    # relevante"; el rango por sí solo nunca lo permite.
    similarity: float = 0.0
    score: float = 0.0

    @property
    def source_id(self) -> str:
        return f"doc:{self.document_id}"

    def excerpt(self, limit: int = 400) -> str:
        return self.content if len(self.content) <= limit else self.content[:limit] + "..."


_SEMANTIC_SQL = text(
    """
    SELECT c.id AS chunk_id, c.document_id, c.section, c.content,
           d.title, d.version, d.status, d.approved_at,
           -- La distancia real, no solo el orden. Sin esto no hay forma de
           -- distinguir "el mejor de ocho buenos" de "el mejor de ocho malos".
           (c.embedding <=> CAST(:embedding AS vector)) AS distance
      FROM document_chunks c
      JOIN citable_documents d ON d.id = c.document_id
     WHERE (CAST(:product_id AS uuid) IS NULL OR d.product_id = CAST(:product_id AS uuid))
       AND c.embedding IS NOT NULL
     ORDER BY distance
     LIMIT :limit
    """
)

# Se construye la consulta léxica con OR entre términos, no con AND.
#
# `plainto_tsquery` une los términos con AND: «estudio CARDIO-101 resultados»
# solo recupera fragmentos que contengan las tres palabras. Medido sobre el
# corpus de demostración, la mitad léxica no devolvía nada en 3 de 4 consultas
# reales, con lo que la búsqueda «híbrida» era vectorial con pasos extra.
#
# Con OR, cada término aporta, y `ts_rank` se encarga de ordenar: un fragmento
# que contiene los tres puntúa por encima de uno que contiene uno. Es la
# semántica correcta para recuperación, donde interesa el recall y el ranking
# decide, no un filtro estricto.
_LEXICAL_SQL = text(
    """
    SELECT c.id AS chunk_id, c.document_id, c.section, c.content,
           d.title, d.version, d.status, d.approved_at,
           ts_rank(c.content_tsv, q) AS rank
      FROM document_chunks c
      JOIN citable_documents d ON d.id = c.document_id,
           to_tsquery('spanish', :tsquery) AS q
     WHERE (CAST(:product_id AS uuid) IS NULL OR d.product_id = CAST(:product_id AS uuid))
       AND c.content_tsv @@ q
     ORDER BY rank DESC
     LIMIT :limit
    """
)


def _build_tsquery(session: Session, query: str) -> str | None:
    """Convierte texto libre en una consulta `tsquery` con OR entre lexemas.

    Se delega la extracción de lexemas al propio PostgreSQL (`to_tsvector` con
    la configuración española) en lugar de trocear en Python. Así la
    normalización —acentos, plurales, palabras vacías— es exactamente la misma
    que se aplicó al indexar. Trocear a mano produciría términos que el índice
    no contiene.
    """
    lexemes = session.execute(
        text("SELECT unnest(string_to_array(to_tsvector('spanish', :q)::text, ' '))"),
        {"q": query},
    ).scalars().all()

    terms = []
    for entry in lexemes:
        # Cada entrada tiene la forma 'lexema':posiciones
        lexeme = entry.split(":", 1)[0].strip("'")
        if lexeme:
            terms.append(lexeme)

    return " | ".join(terms) if terms else None


def _row_to_chunk(row: Any, **ranks: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=str(row["chunk_id"]),
        document_id=str(row["document_id"]),
        document_title=row["title"],
        document_version=row["version"],
        document_status=row["status"],
        section=row["section"],
        content=row["content"],
        approved_at=row["approved_at"].isoformat() if row["approved_at"] else None,
        similarity=(
            max(0.0, 1.0 - float(row["distance"])) if "distance" in row.keys() else 0.0
        ),
        **ranks,
    )


def search(
    session: Session,
    *,
    query: str,
    product_id: str | None = None,
    limit: int = 8,
    pool: int = 25,
) -> list[RetrievedChunk]:
    """Búsqueda híbrida sobre documentación citable.

    `pool` es cuántos candidatos pide cada método antes de fusionar. Debe ser
    bastante mayor que `limit`: un fragmento que sale décimo en semántica y
    tercero en léxica solo puede ganar si ambas listas llegan lo bastante
    hondas.
    """
    embedder = get_embedding_provider()
    embedding = "[" + ",".join(f"{v:.6f}" for v in embedder.embed(query)) + "]"

    semantic = session.execute(
        _SEMANTIC_SQL,
        {"embedding": embedding, "product_id": product_id, "limit": pool},
    ).mappings().all()

    tsquery = _build_tsquery(session, query)
    lexical = (
        session.execute(
            _LEXICAL_SQL,
            {"tsquery": tsquery, "product_id": product_id, "limit": pool},
        ).mappings().all()
        if tsquery
        else []
    )

    # Fusión por rango recíproco: cada lista aporta 1/(k + posición).
    scores: dict[str, float] = {}
    chunks: dict[str, RetrievedChunk] = {}

    for position, row in enumerate(semantic, start=1):
        chunk = _row_to_chunk(row, semantic_rank=position)
        chunks[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + position)

    for position, row in enumerate(lexical, start=1):
        chunk = _row_to_chunk(row, lexical_rank=position)
        if chunk.chunk_id in chunks:
            chunks[chunk.chunk_id].lexical_rank = position
            chunk = chunks[chunk.chunk_id]
        else:
            chunks[chunk.chunk_id] = chunk
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (RRF_K + position)

    ranked = sorted(chunks.values(), key=lambda c: scores[c.chunk_id], reverse=True)
    for chunk in ranked:
        chunk.score = round(scores[chunk.chunk_id], 6)

    result = ranked[:limit]
    log.info(
        "retrieval",
        query_length=len(query),
        semantic_hits=len(semantic),
        lexical_hits=len(lexical),
        fused=len(ranked),
        returned=len(result),
    )
    return result


def relevance_of(chunks: list[RetrievedChunk]) -> float:
    """Relevancia agregada del conjunto recuperado.

    Alimenta la política `INSUFFICIENT_EVIDENCE_MUST_ADMIT`: por debajo del
    umbral el agente declara que no dispone de información en lugar de
    responder con lo poco que haya salido. Es la diferencia entre «no hay
    material aprobado sobre esto» y una respuesta construida sobre tres
    fragmentos irrelevantes.
    """
    if not chunks:
        return 0.0

    # Se mide sobre la **similitud coseno real**, nunca sobre la posición en el
    # ranking.
    #
    # Fallo encontrado por `test_insufficient_evidence_blocks_...`: la búsqueda
    # vectorial devuelve siempre los k primeros, por irrelevantes que sean, así
    # que una métrica basada en el rango daba relevancia alta a una consulta de
    # texto sin sentido. Es el modo de fallo característico de RAG —el almacén
    # vectorial nunca dice "no sé"— y convertía la política de evidencia
    # insuficiente en decorativa.
    best_similarity = max((c.similarity for c in chunks), default=0.0)

    # Una coincidencia léxica es evidencia independiente de que la consulta
    # comparte vocabulario con el corpus, y compensa que el embebedor local no
    # capte sinonimia.
    lexical_support = sum(1 for c in chunks if c.lexical_rank is not None)
    lexical_bonus = min(0.15, 0.05 * lexical_support)

    return round(min(1.0, best_similarity + lexical_bonus), 4)


def format_for_prompt(chunks: list[RetrievedChunk]) -> str:
    """Serializa los fragmentos para el prompt.

    Cada fragmento va delimitado y etiquetado con su identificador, versión y
    fecha de aprobación. Los delimitadores no son decoración: son lo que
    permite que el modelo distinga el material a consultar de las
    instrucciones, que es la defensa real contra la inyección de prompt.
    """
    if not chunks:
        return "(no se ha recuperado ningún documento aprobado y vigente)"

    blocks: list[str] = []
    for chunk in chunks:
        header = (
            f"[{chunk.source_id}] {chunk.document_title} {chunk.document_version}"
            f" · aprobado {chunk.approved_at or 'sin fecha'}"
        )
        if chunk.section:
            header += f" · sección: {chunk.section}"
        blocks.append(
            f'<fragmento id="{chunk.source_id}">\n'
            f"{header}\n---\n{chunk.content}\n</fragmento>"
        )
    return "\n\n".join(blocks)
