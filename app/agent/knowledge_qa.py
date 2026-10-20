"""Citation-preserving answers grounded in retrieved knowledge chunks."""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.reporting import ChatCompletionClient
from app.rag.service import KnowledgeService


@dataclass(frozen=True)
class KnowledgeAnswer:
    answer: str
    citations: list[str]


class KnowledgeAnswerService:
    """Answers only from retrieved knowledge; returns its source citations."""

    _SYSTEM_PROMPT = """You are an agricultural knowledge assistant.
Answer only from the supplied knowledge context. If the context is insufficient,
say so clearly. Do not invent facts. Reply in concise Chinese and cite source
identifiers in square brackets when making factual statements."""

    def __init__(self, knowledge_service: KnowledgeService, client: ChatCompletionClient) -> None:
        self._knowledge_service = knowledge_service
        self._client = client

    def answer(self, question: str, *, project_id: str = "default") -> KnowledgeAnswer:
        context, citations = self._knowledge_service.context_for(question, project_id=project_id)
        if not context:
            return KnowledgeAnswer("知识库中暂未检索到可用于回答该问题的资料。", [])
        answer = self._client.complete(
            system_prompt=self._SYSTEM_PROMPT,
            user_message=f"问题：{question}\n\n知识库证据：\n{context}",
        )
        return KnowledgeAnswer(answer, citations)
