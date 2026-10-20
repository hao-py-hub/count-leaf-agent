import unittest

from app.services.policy_engine import PhenotypingPolicy, PolicyEngine


class PolicyEngineTests(unittest.TestCase):
    def test_project_id_is_required_and_normalized(self) -> None:
        engine = PolicyEngine()
        self.assertEqual(engine.require_project_id("  trial-a  "), "trial-a")
        with self.assertRaisesRegex(ValueError, "project_id"):
            engine.require_project_id(" ")

    def test_budget_is_evaluated_from_versioned_policy(self) -> None:
        engine = PolicyEngine(PhenotypingPolicy(policy_version="trial-v2", max_daily_analysis_jobs=3))
        status = engine.check_budget(2)
        self.assertEqual(status["policy_version"], "trial-v2")
        self.assertEqual(status["remaining"], 1)
        self.assertTrue(status["allowed"])
        self.assertFalse(engine.check_budget(3)["allowed"])

    def test_review_rules_are_policy_controlled(self) -> None:
        engine = PolicyEngine()
        self.assertTrue(engine.review_required(quality_warning=True, model_conflict=False))
        self.assertTrue(engine.review_required(quality_warning=False, model_conflict=True))
        relaxed = PolicyEngine(PhenotypingPolicy(require_quality_confirmation=False, auto_review_on_model_conflict=False))
        self.assertFalse(relaxed.review_required(quality_warning=True, model_conflict=True))


if __name__ == "__main__":
    unittest.main()
