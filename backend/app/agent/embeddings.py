"""Embeddings para la búsqueda documental.

Decisión: el proveedor por defecto es local y determinista.

Anthropic no ofrece endpoint de embeddings, así que usar un modelo remoto
significaría meter un segundo proveedor (Voyage, OpenAI) con su credencial, su
coste por ejecución y su modo de fallo. Para un sandbox cuya prioridad es
demostrar control y trazabilidad, eso empeora el sistema: las evaluaciones
dejarían de ser reproducibles y la demo dejaría de funcionar sin red.

El embebedor local es un *hashing vectorizer* sobre unigramas y bigramas, con
ponderación sublineal de frecuencia y normalización L2. No captura sinonimia
—no es un modelo entrenado— y eso es exactamente lo que hay que reconocer:
por eso la recuperación es **híbrida**. El vector aporta tolerancia a la
reformulación; el índice léxico de PostgreSQL aporta las coincidencias exactas
de nombre de producto, código de estudio y sección, que en este dominio son las
que más pesan. Se fusionan por rango recíproco en `app/services/retrieval.py`.

`docs/limitations.md` documenta esto como limitación conocida, y la interfaz
`EmbeddingProvider` permite cambiar a un modelo real sin tocar nada más.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
import unicodedata
from abc import ABC, abstractmethod

EMBEDDING_DIM = 384

# Palabras vacías del castellano. Deliberadamente corta: eliminar demasiado
# perjudica a consultas cortas, que son la mayoría en este producto.
_STOPWORDS = frozenset(
    """
    a al algo alguna algunas alguno algunos ante antes como con contra cual
    cuando de del desde donde dos el ella ellas ello ellos en entre era eran es
    esa esas ese eso esos esta estaba estan estas este esto estos ha hasta hay
    la las le les lo los mas me mi mis mucho muy no nos o os otra otro para pero
    poco por porque que quien se ser si sin sobre son su sus también tanto te
    tiene tienen todo todos tu tus un una uno unos y ya
    """.split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")


def _normalize(text: str) -> str:
    """Minúsculas y sin diacríticos.

    'cardiología' y 'cardiologia' deben caer en el mismo token: en textos
    comerciales reales la acentuación es inconsistente.
    """
    lowered = text.lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def tokenize(text: str) -> list[str]:
    tokens = [t for t in _TOKEN_RE.findall(_normalize(text)) if t not in _STOPWORDS]
    # Los bigramas conservan algo de orden. Sin ellos, "no aprobado" y
    # "aprobado no" producirían vectores idénticos, que en un dominio con
    # negaciones relevantes es un fallo caro.
    bigrams = [f"{a}_{b}" for a, b in itertools.pairwise(tokens)]
    return tokens + bigrams


class EmbeddingProvider(ABC):
    """Contrato mínimo. Cambiar de proveedor no debe tocar nada más."""

    dimension: int = EMBEDDING_DIM
    name: str = "abstract"

    @abstractmethod
    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class HashingEmbeddingProvider(EmbeddingProvider):
    """Vectorizador por hashing. Determinista, sin red, sin coste.

    Cada token se proyecta a una dimensión mediante BLAKE2b truncado, con un
    signo también derivado del hash para que las colisiones se cancelen en
    media en lugar de acumularse.
    """

    name = "hashing-local-v1"

    def __init__(self, dimension: int = EMBEDDING_DIM) -> None:
        self.dimension = dimension

    def _bucket(self, token: str) -> tuple[int, float]:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        index = value % self.dimension
        sign = 1.0 if (value >> 63) & 1 else -1.0
        return index, sign

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        counts: dict[str, int] = {}
        for token in tokenize(text):
            counts[token] = counts.get(token, 0) + 1

        for token, count in counts.items():
            index, sign = self._bucket(token)
            # Ponderación sublineal: la décima aparición de un término no
            # informa diez veces más que la primera.
            vector[index] += sign * (1.0 + math.log(count))

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            # Texto sin tokens útiles. Se devuelve el vector nulo y el buscador
            # lo descarta: es preferible a inventar una dirección arbitraria que
            # produciría coincidencias falsas con cualquier consulta.
            return vector
        return [v / norm for v in vector]


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = HashingEmbeddingProvider()
    return _provider
