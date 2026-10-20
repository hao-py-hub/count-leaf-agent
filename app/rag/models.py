"""Typed records for traceable local knowledge retrieval."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeChunk:
    document_id: str
    chunk_id: str
    title: str
    content: str
    ordinal: int


@dataclass(frozen=True)
class RetrievalHit:
    document_id: str
    chunk_id: str
    title: str
    content: str
    score: float
