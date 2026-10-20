"""Tests for report route job lookup boundaries."""

from types import SimpleNamespace
import unittest
from uuid import uuid4

from app.api.v2.reports import _get_job


class MissingJobService:
    def get(self, job_id):
        raise KeyError("missing")


class ReportRouteHelperTests(unittest.TestCase):
    def test_missing_job_is_normalized_to_none(self) -> None:
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(analysis_job_service=MissingJobService())))
        self.assertIsNone(_get_job(request, uuid4()))


if __name__ == "__main__":
    unittest.main()
