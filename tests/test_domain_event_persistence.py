import tempfile
import unittest
from pathlib import Path

from app.services.domain_events import DomainEvent, SqliteDomainEventPublisher


class DomainEventPersistenceTests(unittest.TestCase):
    def test_events_survive_publisher_restart_and_are_project_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "events.sqlite3"
            event = DomainEvent("AnalysisCompleted", "AnalysisRun", "job-1", "trial-a", "trace-1", metadata={"run_id": "run-1"})
            publisher = SqliteDomainEventPublisher(database)
            publisher.publish(event)
            publisher.publish(event)
            restarted = SqliteDomainEventPublisher(database)
            self.assertEqual(len(restarted.list_events(project_id="trial-a")), 1)
            self.assertEqual(restarted.list_events(project_id="trial-b"), [])
            self.assertEqual(restarted.list_events(trace_id="trace-1")[0]["event_type"], "AnalysisCompleted")


if __name__ == "__main__":
    unittest.main()
