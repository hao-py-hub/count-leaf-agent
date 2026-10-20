"""HTTP contracts for confirmed, privacy-safe history exports."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from app.history import HistoryStore
from app.main import app


class HistoryExportRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.store = HistoryStore(Path(self.directory.name) / "history.sqlite3")
        self.store.start("export-run", "N0-a.jpg", "C:/private/a.jpg")
        self.store.complete("export-run", {"plants": [{"label": "主干", "pod_count": 5}]}, [])
        self.original_store = app.state.history_store
        app.state.history_store = self.store
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.state.history_store = self.original_store
        self.client.close()
        self.directory.cleanup()

    def test_export_requires_confirmation_and_reports_scope(self) -> None:
        response = self.client.post("/api/v2/exports/history", json={"query": "N0"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "confirmation_required")
        self.assertEqual(response.json()["record_count"], 1)

    def test_confirmed_export_contains_metadata_without_local_path(self) -> None:
        response = self.client.post(
            "/api/v2/exports/history", json={"query": "N0", "confirm": True}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("scope_query,N0", response.text)
        self.assertIn("export-run", response.text)
        self.assertNotIn("C:/private", response.text)

    def test_confirmed_xlsx_export_is_downloadable(self) -> None:
        response = self.client.post("/api/v2/exports/history", json={"confirm": True, "format": "xlsx"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("spreadsheetml", response.headers["content-type"])
        self.assertTrue(response.content.startswith(b"PK"))

    def test_export_is_limited_to_requested_project(self) -> None:
        self.store.start("other-run", "N0-other.jpg", "C:/private/other.jpg", project_id="trial-b")
        self.store.complete("other-run", {"plants": [{"label": "主干", "pod_count": 9}]}, [])

        response = self.client.post(
            "/api/v2/exports/history",
            json={"confirm": True, "project_id": "default"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("export-run", response.text)
        self.assertNotIn("other-run", response.text)


if __name__ == "__main__":
    unittest.main()
