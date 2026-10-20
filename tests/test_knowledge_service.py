"""Local RAG retrieval tests without an embedding model or network access."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.rag.service import KnowledgeService


class KnowledgeServiceTests(unittest.TestCase):

    def test_search_is_scoped_to_project_id(self):
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3")
            first_id, _ = service.ingest(title="项目 A", content="角果拍摄规范", project_id="trial-a")
            second_id, _ = service.ingest(title="项目 B", content="角果统计口径", project_id="trial-b")
            self.assertEqual({hit.document_id for hit in service.search("角果", project_id="trial-a")}, {first_id})
            self.assertEqual({hit.document_id for hit in service.search("角果", project_id="trial-b")}, {second_id})
    def test_ingested_chinese_content_is_retrievable_with_citations(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3")
            document_id, chunk_count = service.ingest(
                title="油菜角果表型说明",
                content="角果数量用于评估油菜植株的结实表现。图像分析结果应结合采集条件进行解读。",
            )

            context, citations = service.context_for("角果数量")

            self.assertEqual(chunk_count, 1)
            self.assertEqual(len(citations), 1)
            self.assertIn(document_id, citations[0])
            self.assertIn("角果数量", context)

    def test_empty_query_returns_no_hits(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3")
            self.assertEqual(service.search("   "), [])

    def test_hybrid_mode_expands_domain_terms_without_external_dependencies(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(
                Path(temporary_directory) / "knowledge.sqlite3",
                retrieval_mode="hybrid",
            )
            self.assertIn("荚果数量", service._expanded_queries("角果数量"))

    def test_invalid_retrieval_mode_is_rejected(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            with self.assertRaises(ValueError):
                KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3", retrieval_mode="vector")

    def test_query_boilerplate_does_not_count_as_retrieval_evidence(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3")
            service.ingest(title="知识库说明", content="本知识库仅包含油菜拍摄规范。")

            self.assertEqual(service.search("请根据知识库推荐下季度的施肥配方"), [])

    def test_status_reports_retrieval_configuration_and_counts(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(
                Path(temporary_directory) / "knowledge.sqlite3",
                retrieval_mode="hybrid",
            )
            service.ingest(title="采样规范", content="成熟期记录角果数量。")

            status = service.status()

            self.assertEqual(status["backend"], "sqlite_fts5")
            self.assertEqual(status["retrieval_mode"], "hybrid")
            self.assertEqual(status["document_count"], 1)
            self.assertEqual(status["chunk_count"], 1)

    def test_query_cache_reuses_hits_and_ingest_invalidates_by_version(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            service = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3")
            service.ingest(title="采样规范", content="成熟期记录角果数量。")
            first = service.search("角果数量")
            second = service.search("角果数量")
            self.assertEqual(first, second)
            self.assertEqual(service.status()["query_cache_hits"], 1)
            self.assertEqual(service.status()["query_cache_misses"], 1)
            version = service.status()["knowledge_version"]
            service.ingest(title="新规范", content="标尺用于株高测量。")
            self.assertGreater(service.status()["knowledge_version"], version)
            service.search("角果数量")
            self.assertEqual(service.status()["query_cache_misses"], 2)


if __name__ == "__main__":
    unittest.main()
