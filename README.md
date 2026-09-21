# falcon-show-me-you-agents

- `contracts_v0.py`：A、B、C 共享的 Python 接口契约。
- `build_contract_examples.py`：生成并验证接口示例。
- `CONTRACT_CHANGES_FOR_BC.md`：字段变更和迁移说明（2026/09/14）。

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
