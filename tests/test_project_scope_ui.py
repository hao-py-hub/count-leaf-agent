import unittest
from pathlib import Path


class ProjectScopeUiTests(unittest.TestCase):
    def test_assistant_sends_project_scope_for_single_and_batch(self) -> None:
        page = (Path(__file__).parents[1] / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="assistantProjectId"', page)
        self.assertIn("project_id, confirm_submission", page)
        self.assertIn("{ asset_ids, project_id, confirm: true }", page)
        self.assertIn("/api/v2/jobs?limit=20&project_id=", page)
        self.assertIn("/api/v2/batches?limit=20&project_id=", page)
        self.assertIn("/api/v2/reviews?limit=50&project_id=${projectId}", page)
        self.assertIn("message, project_id: $('assistantProjectId').value.trim() || 'default'", page)


if __name__ == "__main__":
    unittest.main()
