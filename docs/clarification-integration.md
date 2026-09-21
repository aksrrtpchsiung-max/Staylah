# 追问子图集成接口

本模块不实现 onboarding、search、evaluate 或 review。它消费其他模块给出的结构化
结果，只负责生成追问、等待用户、理解回答并交回运行控制层。

## 上游输入

调用方沿用冻结 contract：

- `RunContext`：必须提供稳定的 `user_id`、`run_id`、`conversation_id`。
- `UserProfile`：本 run 的冻结需求版本。
- `AttemptOutcome`：搜索团队交付的一次尝试结果。联调时可用
  `property_agent.mock_search.first_attempt_from_fixture(...)` 把夹具
  `Result[SearchResult]` 转成该对象。
- `EvaluationModule`、`SearchRunner`：实现
  `property_agent.decision.boundaries` 中的 Protocol。联调实现为
  `ScriptedModuleC` 与 `MockSearchRunner`。

不要把数据库连接、DeepSeek key 或模型对象写入 `RunContext`/`DState`。

上游如需读写聊天历史，使用 `property_agent.persistence.boundaries.ChatRepository`
（实现为 `SqlChatRepository`）。追问子图不会调用 `onboard` / `prepare_query` /
`build_search_plan`。

## 组装和运行

```python
engine = build_engine()
sessions = build_session_factory(engine)
deps = build_postgres_deps(
    module_c=your_evaluation_module,
    search_runner=your_search_runner,
    sessions=sessions,
)

SqlRunRepository(sessions).prepare_run(ctx=ctx, profile=profile)
config = thread_config(ctx["run_id"])

async with postgres_decision_graph(deps) as graph:
    result = await graph.ainvoke(
        initial_state(ctx=ctx, profile=profile, outcome=outcome),
        config,
    )
```

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

运行控制层用 `source_message_id` 从 `messages` 读取原文，再调用冻结的
`onboard(request, profile, messages, ctx=...)`。如需不同 envelope，可替换
`OnboardingHandoff`，无需修改 decision 节点。

## 安全与恢复约束

- DeepSeek 只润色 `PendingQuestion.text`，其他字段由程序生成并保持不变。
- 只有单一提案、用户明确同意、模型返回当前 proposal ID 时才自动接受。
- 多提案“全部接受”、含糊表达和需求修改统一交给 onboarding。
- 相同 `client_message_id` 和相同内容可安全重放；同 ID 不同内容会被拒绝。
- checkpoint 与业务写入不是同一事务。问题、回答、profile mutation 和推荐均有稳定
  幂等键，节点重放不能产生重复业务记录。
- `AsyncPostgresSaver` 连接必须覆盖 graph 调用的完整生命周期，不能从 context manager
  返回 graph 后关闭连接再继续使用。
