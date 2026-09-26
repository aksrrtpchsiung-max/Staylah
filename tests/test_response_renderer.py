"""Falcon 用户回复模板与语气切换测试。"""

import unittest

from property_agent.requirements import FALCON_SCOPE_MESSAGE, ResponseRenderer


class ResponseRendererTests(unittest.TestCase):
    """验证语气变化不改写固定范围提示和结构化事实。"""

    def test_planner_failure_is_not_described_as_recommendation_failure(self) -> None:
        rendered = ResponseRenderer("warm").search_failed(
            [{"code": "MODEL_UNAVAILABLE", "source": "model", "message": "hidden"}]
        )

        self.assertIn("search planning service", rendered)
        self.assertNotIn("recommendation service", rendered)

    def test_scope_message_is_identical_for_every_tone(self) -> None:
        """产品指定的英文范围提示不得随 tone 改写。"""

        for tone in ("warm", "direct", "concise"):
            self.assertEqual(ResponseRenderer(tone).out_of_scope(), FALCON_SCOPE_MESSAGE)

    def test_confirmation_preserves_summary_for_every_tone(self) -> None:
        """所有语气必须逐字保留由 profile 生成的确认摘要。"""

        summary = "Rent; Monthly budget up to SGD 3000; Preferred location: near Clementi"
        for tone in ("warm", "direct", "concise"):
            self.assertIn(summary, ResponseRenderer(tone).confirmation(summary))

    def test_clarification_preserves_every_question(self) -> None:
        """温和模板不能删除完整性节点选择的问题。"""

        questions = [
            {"field": "budget", "text": "你的预算是多少？"},
            {"field": "scope", "text": "Would you prefer a whole unit or a private room?"},
        ]
        rendered = ResponseRenderer("warm").clarification(questions)
        self.assertIn("你的预算是多少？", rendered)
        self.assertIn("Would you prefer a whole unit or a private room?", rendered)

    def test_exhausted_search_budget_is_reported_as_no_match(self) -> None:
        rendered = ResponseRenderer("warm").run_finished("budget_exhausted")
        self.assertIn("completed the available search attempts", rendered)
        self.assertNotIn("could not complete", rendered)


if __name__ == "__main__":
    unittest.main()
