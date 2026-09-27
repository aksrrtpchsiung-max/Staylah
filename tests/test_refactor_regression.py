"""Compare current behavior against the pre-refactor application."""
import json
from pathlib import Path
import unittest

from tests.refactor_scenarios import capture


class RefactorRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_decision_and_evaluation_match_original_behavior(self):
        expected = json.loads((Path(__file__).parent / "fixtures/refactor/behavior.json").read_text())
        actual = await capture()
        for scenario, snapshot in expected.items():
            with self.subTest(scenario=scenario):
                self.assertEqual(actual[scenario], snapshot)


if __name__ == "__main__":
    unittest.main()
