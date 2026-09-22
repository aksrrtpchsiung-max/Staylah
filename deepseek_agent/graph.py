"""和 Quickstart 对照阅读：工具 → 模型 → 状态 → 节点 → 边 → 编译。"""

import os
from typing import Literal

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_deepseek import ChatDeepSeek
from langgraph.graph import END, START, MessagesState, StateGraph
from pydantic import ValidationError


# 1. 工具：真正的计算在本机 Python 中发生。
@tool
def add(a: float, b: float) -> float:
    """计算两个数的和。"""
    return a + b


@tool
def multiply(a: float, b: float) -> float:
    """计算两个数的乘积。"""
    return a * b


@tool
def divide(a: float, b: float) -> float:
    """计算 a 除以 b；b 不能为零。"""
    if b == 0:
        raise ValueError("除数不能为零，请更正输入。")
    return a / b


tools = [add, multiply, divide]
tools_by_name = {item.name: item for item in tools}

# 2. 模型对象不是图节点。bind_tools 只提供工具说明，不执行工具。
# 教学示例关闭深度思考，减少延迟和费用；模型仍会选择工具。
model = ChatDeepSeek(
    model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    api_base="https://api.deepseek.com",
    max_tokens=1024,
    timeout=45,
    max_retries=1,
    extra_body={"thinking": {"type": "disabled"}},
)
model_with_tools = model.bind_tools(tools)


# 3. MessagesState 自带 add_messages 更新规则，新增消息会合并到历史。
class AgentState(MessagesState):
    llm_calls: int


SYSTEM_PROMPT = """你是一个用于学习 LangGraph 的中文计算助手。
进行加法、乘法或除法时，必须调用相应工具，不要心算替代工具。
需要前一步结果的计算必须等工具返回后再继续。
工具出错时解释原因，不编造计算结果，不重复相同的失败请求。
收到普通问候或概念问题时可以直接回答。请简洁回答。"""
MAX_LLM_CALLS = 6
MAX_TOOL_CALLS_PER_MESSAGE = 8


# 每次用户请求先重置本轮计数；服务器会保留 thread 中的消息历史。
def start_turn(state: AgentState):
    return {"llm_calls": 0}


# 4. 模型节点读取状态，发起异步 API 请求，然后返回状态更新。
async def llm_call(state: AgentState):
    count = state.get("llm_calls", 0)
    if count >= MAX_LLM_CALLS:
        return {"messages": [AIMessage(content="本轮已达到模型调用上限，请简化问题后重试。") ]}
    response = await model_with_tools.ainvoke(
        [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    )
    return {"messages": [response], "llm_calls": count + 1}


# 5. 工具节点不调用模型，只执行上一条 AIMessage 中的工具请求。
async def tool_node(state: AgentState):
    results = []
    calls = state["messages"][-1].tool_calls
    for index, call in enumerate(calls):
        status = "success"
        if index >= MAX_TOOL_CALLS_PER_MESSAGE:
            observation = "已达到单条消息的工具调用上限，请减少计算步骤。"
            status = "error"
        elif call["name"] not in tools_by_name:
            observation = "未知工具，只能使用 add、multiply、divide。"
            status = "error"
        else:
            try:
                observation = await tools_by_name[call["name"]].ainvoke(call["args"])
            except (ValueError, ValidationError) as exc:
                observation = str(exc)
                status = "error"
        results.append(ToolMessage(
            content=str(observation),
            tool_call_id=call["id"],
            name=call["name"],
            status=status,
        ))
    return {"messages": results}


# 6. 条件边根据最新消息选择工具节点或结束。
def should_continue(state: AgentState) -> Literal["tool_node", "__end__"]:
    if state["messages"][-1].tool_calls:
        return "tool_node"
    return END


agent_builder = StateGraph(AgentState)
agent_builder.add_node("start_turn", start_turn)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_node("tool_node", tool_node)
agent_builder.add_edge(START, "start_turn")
agent_builder.add_edge("start_turn", "llm_call")
agent_builder.add_conditional_edges("llm_call", should_continue, ["tool_node", END])
agent_builder.add_edge("tool_node", "llm_call")

# Agent Server 自动管理 thread 的 checkpoint，不在这里配置内存 saver。
agent = agent_builder.compile()
