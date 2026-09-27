"""Replay observed PropertyGuru evidence through the B aggregation boundary."""
import copy
import json
from pathlib import Path
from time import monotonic
import unittest

from property_agent.search.aggregation.requirements import build_fulfillment


class RecordedLiveSearchTests(unittest.TestCase):
    def test_original_live_facts_coverage_and_issues_are_preserved(self):
        root = Path(__file__).parent / "fixtures/refactor"
        case = json.loads((root / "live_search.json").read_text())
        expected = json.loads((root / "live_fulfillment_expected.json").read_text())
        before = copy.deepcopy(case)
        self.assertEqual(len(case["search_result"]["data"]["items"]), 12)
        self.assertTrue(all(item["source_mode"] == "live"
                            for item in case["search_result"]["data"]["items"]))
        actual = build_fulfillment(case["request"], case["search_result"],
            ctx=case["ctx"], started=monotonic())
        actual["meta"]["duration_ms"] = 0
        self.assertEqual(actual, expected)
        self.assertEqual(case, before)


if __name__ == "__main__":
    unittest.main()
