"""Contratos de entrada de la biblioteca documental.

Los límites de longitud no son decorativos. `body` acota el tamaño de lo que se
indexa: sin tope, una subida grande se convierte en cientos de fragmentos y en
una factura de embeddings que nadie autorizó. `reason` exige un mínimo real
porque un motivo de retirada de tres caracteres no es un motivo.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

DocType = Literal["ficha_producto", "faq", "estudio", "politica", "material", "seguridad"]
Confidentiality = Literal["public", "internal", "restricted"]


class DocumentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    doc_type: DocType
    body: str = Field(min_length=20, max_length=200_000)
    version: str = Field(default="v1.0", max_length=20)
    confidentiality: Confidentiality = "internal"
    product_id: str | None = None
    # Fecha de caducidad opcional. La vista `citable_documents` la aplica sola:
    # un documento caducado deja de ser citable sin que nadie ejecute nada.
    expires_at: str | None = None

    @field_validator("title", "body")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar en blanco")
        return value.strip()


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=300)
    body: str | None = Field(default=None, min_length=20, max_length=200_000)
    version: str | None = Field(default=None, max_length=20)
    confidentiality: Confidentiality | None = None
    expires_at: str | None = None


class ApprovalRequest(BaseModel):
    # Nota opcional del aprobador. No se exige, a diferencia del motivo de
    # retirada: aprobar es el estado esperado de un documento correcto, retirar
    # siempre responde a algo que salió mal y eso sí hay que poder reconstruirlo.
    note: str = Field(default="", max_length=2000)


class WithdrawalRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    product_id: str | None = None
    limit: int = Field(default=8, ge=1, le=20)
