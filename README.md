# falcon-show-me-you-agents

- `contracts_v0.py`：A、B、C 共享的 Python 接口契约。
- `build_contract_examples.py`：生成并验证接口示例。
- `CONTRACT_CHANGES_FOR_BC.md`：字段变更和迁移说明（2026/09/14）。
- `requirement_understanding/`：A 部分的 LLM 直接结构化需求模型和 LangGraph 节点。
- `tests/test_requirement_understanding.py`：结构化输出、来源核验和 checkpoint 测试。

## A 部分当前开发边界

当前实现以下 A-side 需求闭环：

```text
validate_input -> classify_turn_intent
   -> requirement_update/listing_request with new constraints
      -> understand_requirement(LLM) -> validate_patch -> detect_conflicts
      -> merge_profile -> assess_completeness -> clarify/confirm
   -> confirmation/cancellation -> handle_confirmation
   -> housing_question -> A-side housing web search -> answer without profile mutation
   -> listing_request without new constraints -> wait for confirmation or downstream handoff
```

`validate_input` 使用独立输入守卫判断消息是否合法且与新加坡住房相关；范围外消息固定
返回 Falcon 英文提示，不调用需求解析器、不修改 profile。需求解析器输出
`NormalizedRequirement`，确定性节点再把它转换、校验并合并为 `ConversationProfile`。
同一字段的新值直接替换旧 constraint，并生成新的 draft version。
住房知识、市场、政策、通勤或区域问题由 A 自有的受限网页搜索工具回答；该分支只读
`ConversationProfile`，不得创建 patch 或递增 version。查找、比较、排序、联系或确认具体房源
属于 `listing_request`，不能使用 A 的网页搜索工具，仍需进入确认后的房源检索与推荐流程。

只有用户确认且 repository 保存成功后，图才会生成 `RequirementRequest`：

```text
用户消息 -> ProfileChange -> draft ConversationProfile -> 用户确认
         -> RequirementRequest -> B.fulfill_requirements
```

`ConversationProfile` 只属于一个 conversation。A 负责需求提取、版本合并和用户确认；
B 负责把已确认的 `listing_constraints` 与 `derived_data_requirements` 转换为数据库、CLI、
地图或搜索工具调用。当前实现停在 `RequirementRequest`，尚未调用 B，也未处理 B 返回。
`prepare_query`、`build_search_plan` 和 `search` 是 B 的内部接口。

无法映射到现有 Listing 字段或派生类别的偏好保存在 `open_data_requirements`，并固定使用
`handling="best_effort"`。它们可以辅助 B 验证或排序，但不能阻塞核心条件检索。B 无法处理时
应记录到 `skipped_best_effort_requirement_ids`，继续返回已匹配房源，而不是报告“没有符合要求的房源”。

安装依赖并运行测试：

```bash
python3.11 -m pip install -r requirements.txt
python3.11 -m unittest discover -s tests -v
```

LangGraph 子图可按需注入 checkpointer：

```python
from langgraph.checkpoint.memory import InMemorySaver
from requirement_understanding import DeepSeekRequirementInterpreter, build_requirement_graph

graph = build_requirement_graph(
    interpreter=DeepSeekRequirementInterpreter(),
    checkpointer=InMemorySaver(),
)
result = graph.invoke(
    {
        "message_id": "msg-001",
        "user_id": "user-001",
        "conversation_id": "thread-001",
        "current_input": "整套租房，月租最多3500，淡滨尼，至少两个卧室。",
        "status": "new",
    },
    {"configurable": {"thread_id": "thread-001"}},
)
```

未注入 `profile_repository` 时使用的 `InMemoryProfileRepository` 只适合本地开发和测试；
生产环境必须注入真实数据库 repository。LangGraph checkpointer 保存活跃 workflow 状态，
repository 只在用户确认后保存 confirmed profile。

## 本地多轮 CLI 调试

在仓库根目录创建已被 Git 忽略的 `.env.local`：

```text
DEEPSEEK_API_KEY=你的本地密钥
```

首次使用时创建项目内虚拟环境并安装依赖：

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
```

启动同一 `thread_id` 的交互调试：

```bash
./.venv/bin/python -m requirement_understanding.cli --thread demo-001 --tone warm --trace
```

CLI 自动加载 `.env.local`，支持：

```text
/state       完整 checkpoint state
/profile     当前 ConversationProfile
/patch       proposed_patch 与 validated_patch
/answer      最近一次 HousingQuestionAnswer 和来源
/request     已确认后准备交给 B 的 RequirementRequest
/trace on    展示每轮经过的节点
/tone warm   切换 warm / direct / concise 回复模板
/reset       清除当前 CLI 进程中的 checkpoint 和本地 repository
/quit        退出
```

CLI 使用 `InMemorySaver` 和 `InMemoryProfileRepository`，因此退出进程后调试状态会消失。
语气切换只改变用户可见模板，不修改 profile、patch 或 RequirementRequest。

本地调用 DeepSeek V4 Flash 时，只从环境变量读取密钥：

```python
from requirement_understanding import DeepSeekRequirementInterpreter, build_requirement_graph

graph = build_requirement_graph(interpreter=DeepSeekRequirementInterpreter())
```

运行前在终端设置 `DEEPSEEK_API_KEY`。不要把真实密钥写入代码、fixture、命令历史或
`.env` 后提交；仓库已经忽略 `.env` 和 `.env.local`。官方模型 ID 为
`deepseek-v4-flash`。

运行契约示例检查：

```bash
python3.11 build_contract_examples.py
```

## C 模块：Bedrock LLM 评估流程

`part_c.py` 的流程是：

```text
screen（硬条件、代码）
  → retrieve（Claude：关键词／语义相关度）
  → evaluate（Claude：选房、判断候选是否足够、建议下一步）
  → review（Claude 独立复核 + 代码核查）
  → decide_next（执行 evaluate 建议，但保留安全边界）
```

三个 LLM 步骤都使用 Amazon Bedrock 的 Claude Sonnet 4.5；模型 ID 固定为：

```text
global.anthropic.claude-sonnet-4-5-20250929-v1:0
```

安装 Python SDK，并把 Bedrock API Key 放在环境变量中（不要提交到 Git）：

```bash
python3.13 -m pip install boto3
export AWS_BEARER_TOKEN_BEDROCK="你的 Bedrock API Key"
export BEDROCK_REGION="ap-southeast-1"
```

设置好 API Key 后，`retrieve()`、`evaluate()`、`review()` 会自动使用 Bedrock。应用也可以在
启动时显式注入：

```python
from part_c import (
    configure_bedrock_keyword_matcher,
    configure_bedrock_evaluation_review_model,
)

configure_bedrock_keyword_matcher()
configure_bedrock_evaluation_review_model()
```

`evaluate()` 的输出新增了：

- `assessment.next_action`：`publish`、`research`、`ask_user` 或 `finish`。
- `assessment.next_reason_code`：该路线的原因。

编排层调用 `decide_next()` 前，应把这两个值复制到 `DecisionState` 的
`evaluation_next_action` 与 `evaluation_next_reason_code`。`decide_next()` 会优先采用这一建议；
但若 review 未通过、次数用尽、用户取消或超时，它会拒绝执行不安全的建议。

没有 API Key、网络失败或模型返回格式不符合约定时，`retrieve()` 与 `evaluate()` 会回退到
可解释的本地逻辑并返回 `status="partial"`；对应 issue 分别是 `RETRIEVAL_DEGRADED` 和
`MODEL_UNAVAILABLE`。`review()` 必须完成独立模型审查，否则返回 `status="error"`、
`MODEL_UNAVAILABLE`，不能伪造 `passed=true`。无论何时，`screen` 的硬条件和事实证据检查都不会
被 LLM 覆盖。
