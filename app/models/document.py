"""Modèles ``Document`` et ``DocumentChunk`` (tables ``documents`` et
``document_chunks``).

Exigences :
- 11.3 : ``documents.checksum`` **UNIQUE** — pas de réindexation d'un checksum connu.
- 11.5 : chaque chunk conserve ses métadonnées (source_id, document_id, page,
  section, publication_date) dans une colonne ``metadata`` jsonb.
- 11.6 : ``document_chunks.embedding`` de type ``VECTOR(dim)``, ``dim`` configurable
  via ``settings.embedding_dim`` (défaut 1536). L'index HNSW ``vector_cosine_ops``
  est créé dans la migration Alembic.

Remarque : ``metadata`` est un nom réservé sur ``DeclarativeBase``. La colonne SQL
reste nommée ``metadata`` mais est mappée sur des attributs Python non réservés
(``meta`` pour ``Document``, ``chunk_metadata`` pour ``DocumentChunk``) via
``mapped_column("metadata", ...)``.

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.config import settings
from app.core.database import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.source import Source


class Document(CreatedAtMixin, Base):
    """Document ingéré rattaché à une Source ; ``checksum`` UNIQUE (Exigence 11.3)."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    checksum: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )
    # Colonne SQL ``metadata`` mappée sur l'attribut Python ``meta`` (nom réservé).
    meta: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="documents",
        lazy="selectin",
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="DocumentChunk.chunk_index",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Document id={self.id} source_id={self.source_id} checksum={self.checksum!r}>"


class DocumentChunk(CreatedAtMixin, Base):
    """Fragment vectorisé d'un Document (Exigences 11.5, 11.6).

    ``embedding`` est un vecteur pgvector de dimension ``settings.embedding_dim``
    (défaut 1536). ``chunk_metadata`` (colonne SQL ``metadata``) conserve
    source_id, document_id, page, section, publication_date.
    """

    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding: Mapped[Any | None] = mapped_column(
        Vector(settings.embedding_dim),
        nullable=True,
    )
    # Colonne SQL ``metadata`` mappée sur ``chunk_metadata`` (nom réservé).
    chunk_metadata: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,
        nullable=True,
    )

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="chunks",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<DocumentChunk id={self.id} document_id={self.document_id} "
            f"index={self.chunk_index}>"
        )
