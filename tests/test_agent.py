"""无需网络或真实密钥：验证失败工具回传、调用上限及多轮计数。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("DEEPSEEK_API_KEY", "test-placeholder-not-a-real-key")
os.environ["LANGSMITH_TRACING"] = "false"

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from deepseek_agent import graph


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_tools_preserve_request_ids(self):
        calls = [
            {"name": "divide", "args": {"a": 7, "b": 0}, "id": "zero"},
            {"name": "delete_files", "args": {}, "id": "unknown"},
            {"name": "add", "args": {"a": 3, "b": 4}, "id": "valid"},
        ]
        result = await graph.tool_node({"messages": [AIMessage(content="", tool_calls=calls)]})
        self.assertEqual([m.tool_call_id for m in result["messages"]], ["zero", "unknown", "valid"])
        self.assertEqual([m.status for m in result["messages"]], ["error", "error", "success"])
        self.assertEqual(result["messages"][-1].content, "7.0")

    async def test_endless_tool_requests_stop_and_new_turn_resets_budget(self):
        class EndlessModel:
            count = 0

            async def ainvoke(self, messages):
                self.count += 1
                return AIMessage(content="", tool_calls=[{
                    "name": "add", "args": {"a": 1, "b": 1}, "id": f"call-{self.count}"
                }])

        fake = EndlessModel()
        agent = graph.agent_builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "budget-test"}, "recursion_limit": 20}
        with patch.object(graph, "model_with_tools", fake):
            first = await agent.ainvoke({"messages": [HumanMessage(content="计算") ]}, config)
            self.assertEqual(fake.count, graph.MAX_LLM_CALLS)
            self.assertIn("上限", first["messages"][-1].content)
            requests = [c["id"] for m in first["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
            responses = [m.tool_call_id for m in first["messages"] if isinstance(m, ToolMessage)]
            self.assertEqual(requests, responses)
            second = await agent.ainvoke({"messages": [HumanMessage(content="再计算") ]}, config)
            self.assertEqual(fake.count, 2 * graph.MAX_LLM_CALLS)
            self.assertEqual(second["llm_calls"], graph.MAX_LLM_CALLS)


if __name__ == "__main__":
    unittest.main()
