"""Validation tests for knowledge file ingestion."""

import unittest

from app.rag.file_loader import load_knowledge_text


class KnowledgeFileLoaderTests(unittest.TestCase):
    def test_loads_utf8_markdown_with_bom(self) -> None:
        title, content = load_knowledge_text("rapeseed.md", b"\xef\xbb\xbf# \xe6\xb2\xb9\xe8\x8f\x9c\n\xe8\xa7\x92\xe6\x9e\x9c\xe6\x95\xb0\xe9\x87\x8f")
        self.assertEqual(title, "rapeseed")
        self.assertIn("角果", content)

    def test_rejects_unsupported_file_extension(self) -> None:
        with self.assertRaises(ValueError):
            load_knowledge_text("report.pdf", b"content")


if __name__ == "__main__":
    unittest.main()
