import os
import unittest
from unittest.mock import patch

from property_agent.clarification.deepseek import DeepSeekClarificationAdapter
from property_agent.clarification.models import AnswerInterpretation, QuestionPolish
from property_agent.clarification.service import ClarificationAgent
from property_agent.clarification.stubs import ScriptedClarificationAdapter


def question(*, proposal_count: int = 1) -> dict:
    proposals = [
        {
            "proposal_id": f"proposal-{index}",
            "field": "hard_constraints.max_price",
            "old_value": 3500,
            "proposed_value": 3500 + index,
            "reason": "扩大匹配范围",
            "evidence_listing_keys": [],
            "requires_user_confirmation": True,
        }
        for index in range(1, proposal_count + 1)
    ]
    return {
        "question_id": "run-1:state-1:relax-test",
        "text": "是否从 3500 调整到 3501？",
        "reason_code": "insufficient_candidates",
        "proposals": proposals,
        "allowed_actions": ["answer", "accept_proposal", "decline", "cancel"],
        "base_profile_version": 1,
        "state_version": 1,
    }


class ClarificationAgentTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self, adapter=None):
        adapter = adapter or ScriptedClarificationAdapter()
        return ClarificationAgent(polisher=adapter, interpreter=adapter)

    async def test_polish_keeps_only_text_and_requires_values(self):
        adapter = ScriptedClarificationAdapter(
            polished_texts=["可以把预算从 3500 调整到 3501 吗？"]
        )
        original = question()
        polished = await self.make_agent(adapter).prepare_question(original)
        self.assertEqual(polished["text"], "可以把预算从 3500 调整到 3501 吗？")
        self.assertEqual(polished["proposals"], original["proposals"])
        self.assertEqual(polished["question_id"], original["question_id"])

    async def test_semantically_incomplete_polish_falls_back(self):
        adapter = ScriptedClarificationAdapter(polished_texts=["要不要调整预算？"])
        original = question()
        self.assertEqual(
            await self.make_agent(adapter).prepare_question(original),
            original,
        )

    async def test_explicit_single_proposal_acceptance_is_allowed(self):
        adapter = ScriptedClarificationAdapter(
            interpretations=[
                AnswerInterpretation(
                    action="accept_proposal", proposal_id="proposal-1"
                )
            ]
        )
        parsed = await self.make_agent(adapter).parse_answer(
            text="好的，我接受提高预算",
            question=question(),
            client_message_id="msg-1",
        )
        self.assertEqual(parsed["action"], "accept_proposal")
        self.assertEqual(parsed["proposal_id"], "proposal-1")

    async def test_model_cannot_turn_ambiguous_text_into_acceptance(self):
        adapter = ScriptedClarificationAdapter(
            interpretations=[
                AnswerInterpretation(
                    action="accept_proposal", proposal_id="proposal-1"
                )
            ]
        )
        parsed = await self.make_agent(adapter).parse_answer(
            text="我再想想",
            question=question(),
            client_message_id="msg-2",
        )
        self.assertEqual(parsed["action"], "answer")

    async def test_accept_all_multiple_proposals_hands_off(self):
        adapter = ScriptedClarificationAdapter(
            interpretations=[
                AnswerInterpretation(
                    action="accept_proposal", proposal_id="proposal-1"
                )
            ]
        )
        parsed = await self.make_agent(adapter).parse_answer(
            text="好的，全部接受",
            question=question(proposal_count=2),
            client_message_id="msg-3",
        )
        self.assertEqual(parsed["action"], "answer")

    async def test_explicit_cancel_and_decline_are_distinct(self):
        agent = self.make_agent()
        cancelled = await agent.parse_answer(
            text="取消找房任务", question=question(), client_message_id="msg-4"
        )
        declined = await agent.parse_answer(
            text="不接受调整，保持原样",
            question=question(),
            client_message_id="msg-5",
        )
        self.assertEqual(cancelled["action"], "cancel")
        self.assertEqual(declined["action"], "decline")

    async def test_forged_proposal_id_is_rejected(self):
        adapter = ScriptedClarificationAdapter(
            interpretations=[
                AnswerInterpretation(action="accept_proposal", proposal_id="forged")
            ]
        )
        parsed = await self.make_agent(adapter).parse_answer(
            text="好的，我接受提高预算",
            question=question(),
            client_message_id="msg-forged",
        )
        self.assertEqual(parsed["action"], "answer")
        self.assertNotIn("proposal_id", parsed)

    async def test_model_cannot_cancel_or_decline_without_explicit_words(self):
        adapter = ScriptedClarificationAdapter(
            interpretations=[
                AnswerInterpretation(action="cancel"),
                AnswerInterpretation(action="decline"),
            ]
        )
        agent = self.make_agent(adapter)
        cancelled = await agent.parse_answer(
            text="我想换个地方", question=question(), client_message_id="msg-fake-cancel"
        )
        declined = await agent.parse_answer(
            text="再看看别的", question=question(), client_message_id="msg-fake-decline"
        )
        self.assertEqual(cancelled["action"], "answer")
        self.assertEqual(declined["action"], "answer")

    async def test_empty_and_failed_model_calls_fall_back(self):
        class Boom:
            async def polish(self, question):
                raise RuntimeError("network")

            async def interpret(self, text, question):
                raise RuntimeError("network")

        original = question()
        agent = ClarificationAgent(polisher=Boom(), interpreter=Boom())
        self.assertEqual(await agent.prepare_question(original), original)
        empty = await agent.parse_answer(
            text="   ", question=original, client_message_id="msg-empty"
        )
        accepted = await agent.parse_answer(
            text="好的，我接受提高预算",
            question=original,
            client_message_id="msg-fallback",
        )
        self.assertEqual(empty["action"], "answer")
        self.assertEqual(accepted["action"], "accept_proposal")
        self.assertEqual(accepted["proposal_id"], "proposal-1")


class DeepSeekAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_output_and_budget(self):
        class FakeStructured:
            def __init__(self, payload):
                self.payload = payload

            async def ainvoke(self, _messages):
                return self.payload

        class FakeModel:
            def __init__(self):
                self.calls = []

            def with_structured_output(self, schema):
                self.calls.append(schema)
                if schema is QuestionPolish:
                    return FakeStructured({"text": "可以把预算从 3500 调整到 3501 吗？"})
                return FakeStructured(
                    {"action": "accept_proposal", "proposal_id": "proposal-1"}
                )

        adapter = DeepSeekClarificationAdapter(model=FakeModel(), max_calls=1)
        text = await adapter.polish(question())
        self.assertIn("3500", text)
        with self.assertRaises(RuntimeError):
            await adapter.interpret("好的", question())

    def test_from_env_requires_key(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DEEPSEEK_API_KEY", None)
            with self.assertRaises(RuntimeError):
                DeepSeekClarificationAdapter.from_env()


if __name__ == "__main__":
    unittest.main()
