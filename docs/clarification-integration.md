# 追问子图集成接口

本模块不实现 onboarding、search、evaluate 或 review。它消费其他模块给出的结构化
结果，只负责生成追问、等待用户、理解回答并交回运行控制层。

产品运行控制层是 `property_agent.orchestration.ConversationOrchestrator`。
它负责：用户消息进 A、确认后调用 B、`needs_clarification` 回到 A、
C 的 interrupt 恢复，以及 `next_run_request` 开新一轮搜索。

## 上游输入

调用方沿用冻结 contract：

- `RunContext`：必须提供稳定的 `user_id`、`run_id`、`conversation_id`。
- `ConversationProfile`：本 run 的冻结需求版本，必须是 `status="confirmed"`。
- `AttemptOutcome`：由 `BSearchRunner.run_initial` / `run_attempt` 从 B 的履约结果转换。
- `EvaluationModule`、`SearchRunner`：实现
  `property_agent.decision.boundaries` 中的 Protocol。默认评估模块是
  `PartCEvaluationModule`，直接调用 `part_c.evaluate` / `part_c.review`；
  默认搜索是 `BSearchRunner`。

decision 图与模块 C 使用同一份 `ConversationProfile`。旧夹具在
`tests.support.load_profile()` 加载时转换成确认档案，不再经过独立适配器。

业务路由尽量听 C 的 `part_c.decide_next`。`prepare_decision` 把
`assessment.next_action` 写入 `evaluation_next_action`，再交给 C 执行。
取消、档案过期、来源失败、截止时间和 review/repair 等安全门仍由 C 先处理；
尚未搜索的准备阶段由 A 本地处理，因为 C 假定已经评过一轮。

`ask_user` 文案按本轮合格套数区分：没有候选时说明没有符合硬条件的房源；
已有若干套时说明数量偏少，而不是没有。

不要把数据库连接、DeepSeek key 或模型对象写入 `RunContext`/`DState`。
模型栈配置见仓库根目录 `runtime.toml`。

上游如需读写聊天历史，使用 `property_agent.persistence.boundaries.ChatRepository`
（实现为 `SqlChatRepository`）。追问子图不会调用 `onboard` / `prepare_query` /
`build_search_plan`。

## 组装和运行

推荐入口：

```python
from property_agent.orchestration import postgres_conversation_runtime

async with postgres_conversation_runtime() as orchestrator:
    result = await orchestrator.handle_message(
        "整套租房，月租最多3500，淡滨尼，至少两个卧室。",
        conversation_id="conversation-001",
        user_id="user-001",
        client_message_id="client-001",
    )
```

只装配 decision 图时：

```python
engine = build_engine()
sessions = build_session_factory(engine)
deps = build_postgres_deps(sessions=sessions)

SqlRunRepository(sessions).prepare_run(ctx=ctx, profile=profile)
config = thread_config(ctx["run_id"])

async with postgres_decision_graph(deps) as graph:
    result = await graph.ainvoke(
        initial_state(ctx=ctx, profile=profile, outcome=outcome),
        config,
    )
```

`build_postgres_deps` 默认接入仓库根目录的 `part_c.evaluate` 和 `part_c.review`，
以及 `BSearchRunner`。需要真实模型评审时，在 `.env` 中设置
`DEEPSEEK_API_KEY`；API URL 和模型 ID 以 `runtime.toml` 的 `[deepseek]` 为准。
没有 DeepSeek Key 时，C 默认使用确定性 evaluate/review fallback，并以 `partial` 状态保留
`MODEL_UNAVAILABLE` 说明；测试仍可通过 `module_c=` 覆盖默认模块。

## 持久化字段

PostgreSQL 使用 `conversation_profiles` 保存画像。`ConversationProfile` 的顶层字段
分别落在同名列中，包括 `conversation_id`、`confirmed_version`、`status`、
`listing_constraints`、三类需求列表和确认时间；数据库不再保存旧版
`user_profiles.body`。其他业务表也使用合同字段名：`conversations.conversation_id`、
`messages.message_id/text`、`agent_runs.run_id`、`run_questions.question` 和
`recommendations.recommendation`。嵌套结构继续使用 JSONB。

如果结果包含 `__interrupt__`，把其中的 `pending_question` 发给用户。恢复时只需提交
自然语言与客户端幂等 ID，服务端会从 checkpoint 中取得当前问题和版本：

```python
Command(resume={
    "client_message_id": "stable-client-message-id",
    "text": "不接受调整，保持原条件",
})
```

恢复必须使用同一个 `run_id`/thread。长期聊天仍用独立的 `conversation_id`。

## onboarding 交接

普通补充或修改需求不会在 decision 子图中解析硬条件。子图返回：

```python
next_run_request = {
    "reason_code": "user_message",
    "profile_id": "...",
    "profile_version": 1,
    "superseded_run_id": "...",
    "accepted_proposal_id": None,
    "source_message_id": "...",
}
```

运行控制层（`ConversationOrchestrator`）用同一条用户原文再调用 A graph。
用户确认后会开新的 B→C run。如需不同 envelope，可替换 `OnboardingHandoff`，
无需修改 decision 节点。

## 安全与恢复约束

- DeepSeek 只润色 `PendingQuestion.text`，其他字段由程序生成并保持不变。
- 只有单一提案、用户明确同意、模型返回当前 proposal ID 时才自动接受。
- 多提案“全部接受”、含糊表达和需求修改统一交给 onboarding。
- 相同 `client_message_id` 和相同内容可安全重放；同 ID 不同内容会被拒绝。
- checkpoint 与业务写入不是同一事务。问题、回答、profile mutation 和推荐均有稳定
  幂等键，节点重放不能产生重复业务记录。
- `AsyncPostgresSaver` 连接必须覆盖 graph 调用的完整生命周期，不能从 context manager
  返回 graph 后关闭连接再继续使用。
