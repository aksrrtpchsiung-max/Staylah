# Falcon — Singapore Property Agent

Falcon 是一个面向新加坡租房与买房场景的多 Agent 工作流。系统把需求理解、真实房源搜索、候选评估和追问决策串成一条可持久化的 A → B → C 会话，并通过 PostgreSQL 保存画像、消息、运行状态和 LangGraph checkpoint。

## 当前能力

- **A · Requirement Understanding**：从多轮对话提取结构化住房需求，处理冲突、澄清与用户确认。
- **B · Search and Investigation**：调用 PropertyGuru、OneMap 和 OpenStreetMap，完成搜索、详情读取、地址定位、周边设施与通勤调查。
- **C · Evaluation and Decision**：对 B 返回的候选进行语义检索打分、推荐评估和证据复核，并决定发布、补搜、追问或结束；不再重复筛选硬条件。
- **Orchestration**：连接 A、B、C；支持 B → A 澄清、C → B 补搜和 Decision interrupt/resume。
- **Persistence**：使用 PostgreSQL 保存业务数据，并使用 `AsyncPostgresSaver` 保存 A/C 图状态。

```mermaid
flowchart LR
    U[User] --> A[A · Requirements]
    A -->|confirmed RequirementRequest| B[B · Live Search]
    B -->|needs clarification| A
    B -->|AttemptOutcome| C[C · Evaluate and Review]
    C -->|research directive| B
    C -->|question| U
    C -->|publish| R[Recommendation]
    A -. profile/checkpoint .-> P[(PostgreSQL)]
    C -. run/checkpoint .-> P
```

共享业务契约唯一定义在 `property_agent/contracts.py`，原根目录兼容入口已在真实链路通过后删除。
旧编号和新实现的唯一对应清单见 [模块对应表](模块对应表.md)。正式产品入口是：

```bash
.venv/bin/python -m property_agent.orchestration --conversation demo-001
```

## 代码组织

正式业务实现集中在 `property_agent/`：`requirements` 负责需求理解，`search` 负责搜索调查，
`evaluation` 负责检索、评估、复核，`decision` 和 `orchestration` 负责决策与整个会话。
跨模块规则在 `domain`，数据库在 `persistence`，配置与模型客户端在 `runtime`。
网页入口在 `web/`，PropertyGuru 网站工具仍在 `guru_search/`。

完整目录见 [项目目录](项目目录.md)，业务流程见 [架构说明](docs/architecture.md)。
旧编号目录和根目录兼容文件已经移除，代码和脚本统一使用新路径；对应关系只维护在
[模块对应表](模块对应表.md)。

## 本地启动

### 1. Python 环境

项目要求 Python 3.11 或更高版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

### 2. 配置密钥

复制模板并填写本地值：

```bash
cp .env.example .env
```

核心变量：

```dotenv
# A、B、C 共用：需求理解、搜索规划、语义排序与复核
DEEPSEEK_API_KEY=

# OneMap：二选一，也可以同时配置
ONEMAP_TOKEN=
ONEMAP_EMAIL=
ONEMAP_PASSWORD=

# PostgreSQL
DATABASE_URL=postgresql+psycopg://property_agent:property_agent_dev@127.0.0.1:5432/property_agent
LANGGRAPH_CHECKPOINT_DB_URI=postgresql://property_agent:property_agent_dev@127.0.0.1:5432/property_agent
```

模型 ID、网关 URL、超时、搜索页数和候选额度统一放在 [`runtime.toml`](runtime.toml)。进程环境变量可以覆盖配置文件；真实密钥只应保存在 `.env`。

### 3. PropertyGuru / OpenCLI

真实房源搜索需要 Node.js 20+、OpenCLI 和已连接的 Browser Bridge：

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
```

`opencli doctor` 应显示 daemon 和浏览器扩展均已连接。适配器使用持久 PropertyGuru 会话，B 会依次读取搜索页和房源详情；详情读取成功后才会把房源状态记录为 active。

### 4. PostgreSQL

启动项目自带的 PostgreSQL 并初始化业务表与 checkpoint 表：

```bash
docker compose up -d --wait
.venv/bin/python scripts/init_postgres.py
```

如果本机 `5432` 端口已被现有 PostgreSQL 容器占用，请复用该数据库并修改 `.env` 中的两个数据库 URI，不要再启动第二个占用相同端口的容器。

### 5. 运行完整 CLI

```bash
.venv/bin/python -m property_agent.orchestration --conversation demo-001
```

每个 `conversation` 都有独立 checkpoint。需要从空白会话重新测试时，请换一个 ID，例如 `demo-002`。输入 `/quit` 退出。

网页连接同一条完整链路：

```bash
.venv/bin/python -m web.server --live
```

省略 `--live` 可预览固定示例；详见 [网页说明](web/README.md)。

## 模型降级行为

- A 依赖 DeepSeek；缺少密钥时无法完成真实需求解析。
- B 的模型计划或监督调用失败时，会在额度和截止时间内使用确定性调度继续执行，并保留 issue。
- C.retrieve 优先使用 DeepSeek 完成需求满足度打分；模型不可用、输出截断或评分不完整时，使用本地结构化约束评分继续流程，并以 `partial + MODEL_UNAVAILABLE` 披露降级。evaluate/review 也可按各自的确定性边界降级。
- 房源事实只来自 Provider 及对应 evidence；模型不能创建房源、修改硬条件或放行无证据事实。

## 测试

完整离线测试不需要外部密钥：

```bash
.venv/bin/python -m unittest discover -s tests
```

PostgreSQL 集成测试（先配置隔离测试库，禁止使用日常数据库）：

```bash
TEST_DATABASE_URL=postgresql+psycopg://refactor@127.0.0.1:55438/postgres \
  .venv/bin/python -m unittest tests.test_postgres_integration
```

常用单模块检查：

```bash
.venv/bin/python -m unittest tests.test_a_b_integration
.venv/bin/python -m unittest tests.test_search_integration
.venv/bin/python -m unittest tests.test_part_c_integration
.venv/bin/python -m unittest tests.test_orchestration
```

真实来源检查会访问 DeepSeek、PropertyGuru、OneMap 或 OpenStreetMap，不能用来替代离线回归测试；具体命令见 [开发与诊断](docs/development.md)。

## 文档

- [模块对应表](模块对应表.md)：唯一的原分工、编号与新代码位置清单。
- [目录与架构](docs/architecture.md)：当前业务边界与流程。
- [开发与诊断](docs/development.md)：模型配置、B 接入与真实调用命令。
- [C 函数说明](docs/evaluation.md)与[追问集成](docs/clarification-integration.md)。
- [网页 HTTP API](web/README.md)与[评测工具](evaluation_suite/README.md)。
- [验收及回退](docs/refactoring/acceptance.md)。
