from types import SimpleNamespace
import unittest

from fastapi.testclient import TestClient

from app.main import app


class DomainSampleApiTests(unittest.TestCase):
    def test_domain_samples_are_project_scoped(self):
        original = app.state.phenotype_domain_projection
        app.state.phenotype_domain_projection = SimpleNamespace(list_samples=lambda project_id, limit: [{"sample_id": project_id}] if project_id == "trial-a" else [])
        try:
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/v2/agent/domain-samples?project_id=trial-a").json()["items"], [{"sample_id": "trial-a"}])
                self.assertEqual(client.get("/api/v2/agent/domain-samples?project_id=trial-b").json()["items"], [])
        finally:
            app.state.phenotype_domain_projection = original


if __name__ == "__main__":
    unittest.main()
