"""Tests for citation-preserving RAG answers without model requests."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.agent.knowledge_qa import KnowledgeAnswerService
from app.rag.service import KnowledgeService


class FakeClient:
    def complete(self, *, system_prompt: str, user_message: str) -> str:
        return "资料表明可结合采集条件解读。[citation]"


class KnowledgeAnswerServiceTests(unittest.TestCase):

    def test_answer_passes_project_scope_to_retrieval(self):
        captured = {}

        class Knowledge:
            def context_for(self, question, *, project_id="default"):
                captured["project_id"] = project_id
                return "上下文", ["doc:chunk"]

        class Client:
            def complete(self, *_args, **_kwargs):
                return "基于知识库的回答"

        KnowledgeAnswerService(Knowledge(), Client()).answer("问题", project_id="trial-a")
        self.assertEqual(captured["project_id"], "trial-a")
    def test_answer_contains_retrieval_citation(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            knowledge = KnowledgeService(Path(temporary_directory) / "knowledge.sqlite3")
            document_id, _ = knowledge.ingest(title="资料", content="油菜角果数量可以用于表型评估。")
            answer = KnowledgeAnswerService(knowledge, FakeClient()).answer("油菜角果数量有什么用？")

            self.assertIn("表明", answer.answer)
            self.assertEqual(len(answer.citations), 1)
            self.assertIn(document_id, answer.citations[0])


if __name__ == "__main__":
    unittest.main()
