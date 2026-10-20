"""Tests for enterprise dependency composition without external services."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.bootstrap import PROJECT_ROOT, build_enterprise_dependencies, resolve_database_url
from app.config import cors_allowed_origins
from fastapi.testclient import TestClient
from app.main import app


class BootstrapTests(unittest.TestCase):
    def test_cors_defaults_to_local_origins(self) -> None:
        self.assertEqual(cors_allowed_origins(""), ["http://localhost:8501", "http://127.0.0.1:8501"])

    def test_cors_rejects_wildcard_and_trims_configured_origins(self) -> None:
        self.assertEqual(cors_allowed_origins(" https://portal.example.com/ , https://agent.example.com "), ["https://portal.example.com", "https://agent.example.com"])
        with self.assertRaisesRegex(ValueError, "不能包含"):
            cors_allowed_origins("*")

    def test_readiness_endpoint_does_not_expose_credentials(self) -> None:
        response = TestClient(app).get("/api/ready")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["status"], {"ready", "not_ready"})
        self.assertIn("database", payload["checks"])
        self.assertNotIn("api_key", payload)
        self.assertNotIn("LLM_API_KEY", str(payload))

    def test_runtime_endpoint_exposes_non_secret_versions(self) -> None:
        response = TestClient(app).get("/api/v2/agent/runtime")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["algorithm"], "stalk")
        self.assertTrue(payload["algorithm_version"])
        self.assertNotIn("api_key", str(payload).lower())

    def test_base_security_headers_are_present(self) -> None:
        response = TestClient(app).get("/api/health")
        self.assertIn("charset=utf-8", response.headers["content-type"])
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertEqual(response.headers["referrer-policy"], "same-origin")

    def test_builds_sqlite_dependencies_from_explicit_url(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "bootstrap.sqlite3"
            dependencies = build_enterprise_dependencies(f"sqlite:///{database_path.as_posix()}")

            self.assertIsNotNone(dependencies.assets)
            self.assertIsNotNone(dependencies.analysis_jobs)
            self.assertTrue(database_path.exists())
            dependencies.database.engine.dispose()

    def test_relative_sqlite_url_is_stable_outside_project_directory(self) -> None:
        resolved = resolve_database_url("sqlite:///./data/agent.sqlite3")

        self.assertEqual(resolved, f"sqlite:///{(PROJECT_ROOT / 'data' / 'agent.sqlite3').as_posix()}")


if __name__ == "__main__":
    unittest.main()
