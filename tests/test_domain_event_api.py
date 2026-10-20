import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.services.domain_events import DomainEvent


class DomainEventApiTests(unittest.TestCase):
    def test_domain_events_are_filtered_by_project_and_trace(self) -> None:
        publisher = app.state.domain_event_publisher
        trace_id = uuid4()
        publisher.publish(DomainEvent("AnalysisCompleted", "AnalysisRun", "job-api", "api-trial", str(trace_id)))
        publisher.publish(DomainEvent("AnalysisCompleted", "AnalysisRun", "job-other", "other-trial", str(trace_id)))
        response = TestClient(app).get(f"/api/v2/agent/domain-events?project_id=api-trial&trace_id={trace_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["events"]), 1)
        self.assertEqual(response.json()["events"][0]["project_id"], "api-trial")

    def test_domain_events_reject_invalid_limit(self) -> None:
        response = TestClient(app).get("/api/v2/agent/domain-events?limit=0")
        self.assertEqual(response.status_code, 400)
