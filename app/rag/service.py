"""SQLite FTS5 knowledge ingestion and retrieval service."""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

from app.rag.models import KnowledgeChunk, RetrievalHit


class KnowledgeService:
    """Stores text documents and retrieves cited chunks for Agent prompts."""

    _QUERY_EXPANSIONS = {
        "角果": ("荚果", "结荚"),
        "荚果": ("角果", "结荚"),
        "结荚": ("角果", "荚果"),
        "主枝": ("主分枝",),
        "分枝": ("侧枝", "分蘖"),
        "株高": ("植株高度",),
    }
    _QUERY_BOILERPLATE_PHRASES = {
        "请根", "根据", "据知", "知识", "识库", "库推", "推荐", "请问", "问一", "一下",
    }
    _QUERY_BOILERPLATE_PREFIXES = (
        "请根据知识库推荐",
        "根据知识库推荐",
        "请根据知识库",
        "根据知识库",
        "请问",
    )

    def __init__(
        self,
        database_path: Path,
        *,
        chunk_size: int = 500,
        chunk_overlap: int = 80,
        retrieval_mode: str = "keyword",
    ) -> None:
        if retrieval_mode not in {"keyword", "hybrid"}:
            raise ValueError("retrieval_mode 只能是 keyword 或 hybrid")
        self._database_path = database_path
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._retrieval_mode = retrieval_mode
        self._query_cache: dict[tuple[str, str, int, str, int], list[RetrievalHit]] = {}
        self._query_cache_hits = 0
        self._query_cache_misses = 0
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def ingest(self, *, title: str, content: str, project_id: str = "default") -> tuple[str, int]:
        """Create a document and its searchable chunks."""
        normalized_title = title.strip()
        normalized_content = content.strip()
        if not normalized_title or not normalized_content:
            raise ValueError("标题和正文不能为空")
        project_id = self._require_project_id(project_id)

        document_id = str(uuid4())
        chunks = self._chunk(document_id, normalized_title, normalized_content)
        with self._connect() as connection:
            connection.execute("INSERT INTO knowledge_documents(id, title, project_id) VALUES (?, ?, ?)", (document_id, normalized_title, project_id))
            connection.executemany(
                "INSERT INTO knowledge_chunks(document_id, chunk_id, title, content, ordinal) VALUES (?, ?, ?, ?, ?)",
                [(chunk.document_id, chunk.chunk_id, chunk.title, chunk.content, chunk.ordinal) for chunk in chunks],
            )
        self._query_cache.clear()
        return document_id, len(chunks)

    def search(self, query: str, *, limit: int = 5, project_id: str = "default") -> list[RetrievalHit]:
        """Retrieve the best matching chunks, including stable citation identifiers."""
        if limit < 1:
            return []
        project_id = self._require_project_id(project_id)
        knowledge_version = self._knowledge_version(project_id)
        normalized_query = " ".join(query.lower().split())
        cache_key = (project_id, normalized_query, limit, self._retrieval_mode, knowledge_version)
        cached = self._query_cache.get(cache_key)
        if cached is not None:
            self._query_cache_hits += 1
            return list(cached)
        self._query_cache_misses += 1
        queries = [query]
        if self._retrieval_mode == "hybrid":
            queries.extend(self._expanded_queries(query))
        hits_by_id: dict[str, RetrievalHit] = {}
        for candidate in queries:
            for hit in self._search_keyword(candidate, limit=limit, project_id=project_id):
                previous = hits_by_id.get(hit.chunk_id)
                if previous is None or hit.score < previous.score:
                    hits_by_id[hit.chunk_id] = hit
        ranked_hits = sorted(
            hits_by_id.values(),
            key=lambda hit: (-self._phrase_overlap_score(queries, hit), hit.score),
        )
        results = [hit for hit in ranked_hits if self._phrase_overlap_score(queries, hit) > 0][:limit]
        self._query_cache[cache_key] = list(results)
        return results

    def _search_keyword(self, query: str, *, limit: int, project_id: str) -> list[RetrievalHit]:
        terms = self._fts_query(query)
        if not terms:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT document_id, chunk_id, title, content, bm25(knowledge_chunks) AS score
                   FROM knowledge_chunks WHERE knowledge_chunks MATCH ? AND document_id IN (SELECT id FROM knowledge_documents WHERE project_id = ?)
                   ORDER BY score LIMIT ?""",
                (terms, project_id, limit),
            ).fetchall()
            if not rows:
                phrases = re.findall(r"[\u4e00-\u9fff]{2,}", query)
                fragments = [phrase[index : index + 2] for phrase in phrases for index in range(len(phrase) - 1)]
                fragments = list(dict.fromkeys(fragments))
                conditions = " OR ".join("content LIKE ? OR title LIKE ?" for _ in fragments)
                if conditions:
                    parameters = [value for fragment in fragments for value in (f"%{fragment}%", f"%{fragment}%")]
                    rows = connection.execute(
                        f"""SELECT document_id, chunk_id, title, content, 0.0 AS score
                           FROM knowledge_chunks WHERE ({conditions}) AND document_id IN (SELECT id FROM knowledge_documents WHERE project_id = ?) ORDER BY ordinal LIMIT ?""",
                        (*parameters, project_id, limit),
                    ).fetchall()
        return [RetrievalHit(*row) for row in rows]

    def _expanded_queries(self, query: str) -> list[str]:
        expansions: list[str] = []
        for source, replacements in self._QUERY_EXPANSIONS.items():
            if source in query:
                expansions.extend(query.replace(source, replacement) for replacement in replacements)
        return list(dict.fromkeys(expansions))

    @staticmethod
    def _phrase_overlap_score(queries: list[str], hit: RetrievalHit) -> int:
        """用连续词组重排并过滤与问题没有直接证据重叠的候选。"""
        phrases: set[str] = set()
        for query in queries:
            normalized_query = query.lower()
            for prefix in KnowledgeService._QUERY_BOILERPLATE_PREFIXES:
                if normalized_query.startswith(prefix):
                    normalized_query = normalized_query.removeprefix(prefix)
                    break
            for segment in re.findall(r"[\u4e00-\u9fff]+", normalized_query):
                phrases.update(
                    phrase
                    for length in (2, 3)
                    for index in range(len(segment) - length + 1)
                    if (phrase := segment[index : index + length]) not in KnowledgeService._QUERY_BOILERPLATE_PHRASES
                )
            phrases.update(re.findall(r"[A-Za-z0-9_]{2,}", normalized_query))
        score = 0
        title = hit.title.lower()
        content = hit.content.lower()
        for phrase in phrases:
            if phrase in title:
                score += 3
            elif phrase in content:
                score += 1
        return score

    def context_for(self, query: str, *, limit: int = 5, project_id: str = "default") -> tuple[str, list[str]]:
        """Return prompt-ready context and the exact citations that support it."""
        hits = self.search(query, limit=limit, project_id=project_id)
        citations = [f"{hit.document_id}:{hit.chunk_id}" for hit in hits]
        context = "\n\n".join(
            f"[来源 {citation}｜{hit.title}]\n{hit.content}"
            for citation, hit in zip(citations, hits, strict=True)
        )
        return context, citations

    def status(self, project_id: str = "default") -> dict[str, int | str]:
        """Return non-sensitive retrieval metadata for local diagnostics."""
        with self._connect() as connection:
            project_id = self._require_project_id(project_id)
            document_count = connection.execute("SELECT COUNT(*) FROM knowledge_documents WHERE project_id = ?", (project_id,)).fetchone()[0]
            chunk_count = connection.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id IN (SELECT id FROM knowledge_documents WHERE project_id = ?)", (project_id,)).fetchone()[0]
        return {
            "backend": "sqlite_fts5",
            "retrieval_mode": self._retrieval_mode,
            "document_count": document_count,
            "chunk_count": chunk_count,
            "chunk_size": self._chunk_size,
            "chunk_overlap": self._chunk_overlap,
            "knowledge_version": self._knowledge_version(project_id),
            "query_cache_hits": self._query_cache_hits,
            "query_cache_misses": self._query_cache_misses,
            "query_cache_entries": len(self._query_cache),
        }

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS knowledge_documents(id TEXT PRIMARY KEY, title TEXT NOT NULL, project_id TEXT NOT NULL DEFAULT 'default')")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(knowledge_documents)")}
            if "project_id" not in columns:
                connection.execute("ALTER TABLE knowledge_documents ADD COLUMN project_id TEXT NOT NULL DEFAULT 'default'")
            connection.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks USING fts5(
                    document_id UNINDEXED, chunk_id UNINDEXED, title, content, ordinal UNINDEXED
                )"""
            )

    def _knowledge_version(self, project_id: str) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM knowledge_documents WHERE project_id = ?", (project_id,)).fetchone()[0])

    @staticmethod
    def _require_project_id(project_id: str) -> str:
        normalized = project_id.strip()
        if not normalized or len(normalized) > 128:
            raise ValueError("project_id 无效")
        return normalized

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _chunk(self, document_id: str, title: str, content: str) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        step = self._chunk_size - self._chunk_overlap
        for ordinal, start in enumerate(range(0, len(content), step)):
            segment = content[start : start + self._chunk_size].strip()
            if not segment:
                continue
            chunks.append(KnowledgeChunk(document_id, str(uuid4()), title, segment, ordinal))
            if start + self._chunk_size >= len(content):
                break
        return chunks

    @staticmethod
    def _fts_query(query: str) -> str:
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", query.lower())
        search_terms: list[str] = []
        for token in tokens:
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                search_terms.extend(token)
            else:
                search_terms.append(token)
        return " OR ".join(f'"{term}"' for term in search_terms if term)
