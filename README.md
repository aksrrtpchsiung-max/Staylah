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

共享业务契约位于 `contracts_v0.py`，正式产品入口是：

```bash
.venv/bin/python -m property_agent.orchestration --conversation demo-001
```

## 主要目录

| 路径 | 职责 |
| --- | --- |
| `requirement_understanding/` | A 的需求提取、澄清、确认和 RequirementRequest 构造 |
| `part1/`、`part2/`、`part3/`、`part45/` | B 的计划、监督、来源能力、调查与汇总 |
| `guru_search/` | PropertyGuru OpenCLI 适配器与技能 |
| `part_c.py` | C 的筛选、检索、评估、复核与路线判断 |
| `property_agent/orchestration/` | A → B → C 外层会话循环与 CLI |
| `property_agent/decision/` | C 后的 Decision LangGraph |
| `property_agent/persistence/` | SQLAlchemy 模型、仓储和依赖接线 |
| `runtime.toml` | 非敏感模型参数、超时、搜索额度和运行模式 |
| `.env` | 本地密钥与数据库连接，不提交到 Git |

更完整的边界与时序说明见 [`docs/architecture/`](docs/architecture/) 和 [`docs/clarification-integration.md`](docs/clarification-integration.md)。

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

PostgreSQL 集成测试：

```bash
TEST_DATABASE_URL=postgresql+psycopg://property_agent:property_agent_dev@127.0.0.1:5432/property_agent \
  .venv/bin/python -m unittest tests.test_postgres_integration
```

常用单模块检查：

```bash
.venv/bin/python -m unittest tests.test_a_b_integration
.venv/bin/python -m unittest tests.test_search_integration
.venv/bin/python -m unittest tests.test_part_c_integration
.venv/bin/python -m unittest tests.test_orchestration
```

真实来源检查会访问 DeepSeek、PropertyGuru、OneMap 或 OpenStreetMap，不能用来替代离线回归测试；具体命令见后续模块说明。

## 进一步阅读

- [`docs/architecture/contracts.md`](docs/architecture/contracts.md)：共享契约与模块边界
- [`docs/architecture/pipeline-v1.md`](docs/architecture/pipeline-v1.md)：搜索管线设计
- [`docs/clarification-integration.md`](docs/clarification-integration.md)：追问、补搜与持久化接线
- [`C_EVALUATOR_FUNCTION_GUIDE.md`](C_EVALUATOR_FUNCTION_GUIDE.md)：C 的函数级说明

## 本地多轮 CLI 调试

在仓库根目录创建已被 Git 忽略的 `.env.local`：

```text
DEEPSEEK_API_KEY=你的本地密钥
```

首次使用时创建项目内虚拟环境并安装依赖：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
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
.venv/bin/python docs/examples/build_contract_examples.py
```

## C 模块：DeepSeek 评估流程

`part_c.py` 的流程是：

```text
B 返回候选（C 不再执行 screen，最多 12 套）
  → retrieve（DeepSeek 按 hard/soft 需求权重打分；Python 按分数、价格排序）
  → evaluate（Python 取前 10 套；DeepSeek 生成 summary、limitations 和下一步建议）
  → review（检查数量、夸大和证据；直接修正草稿，不退回 evaluate）
  → decide_next（执行 evaluate 建议，但保留安全边界）
```

三个 LLM 步骤与 B 的 planner/supervisor 共用 `config.py` 中的 DeepSeek HTTP 客户端；
B 通过轻量 `ainvoke` 适配层调用，C 直接读取结构化 JSON。默认模型 ID 为：

```text
deepseek-v4-flash
```

把 DeepSeek 密钥放在 `.env` 中（不要提交到 Git）；URL 与模型默认值在 `runtime.toml`：

```dotenv
DEEPSEEK_API_KEY=你的 DeepSeek 密钥
```

设置好 API Key 后，`retrieve()`、`evaluate()`、`review()` 会自动使用 DeepSeek。应用也可以在
启动时显式注入：

```python
from part_c import (
    configure_deepseek_keyword_matcher,
    configure_deepseek_evaluation_review_model,
)

configure_deepseek_keyword_matcher()
configure_deepseek_evaluation_review_model()
```

`evaluate()` 的输出新增了：

- `assessment.next_action`：`publish`、`research`、`ask_user` 或 `finish`。
- `assessment.next_reason_code`：该路线的原因。

编排层调用 `decide_next()` 前，应把这两个值复制到 `DecisionState` 的
`evaluation_next_action` 与 `evaluation_next_reason_code`。`decide_next()` 会优先采用这一建议；
但若次数用尽、用户取消、超时或系统状态不合法，它会拒绝执行不安全的建议。生产版
`review()` 会把可修正的数量、文案和证据问题直接改好并返回 `passed=true`，不触发 repair 循环。

没有 API Key、网络失败、输出截断或模型返回格式不符合约定时，`retrieve()` 使用本地
结构化约束评分并返回 `status="partial" + MODEL_UNAVAILABLE`。本地评分只检查标准化
Listing 字段；派生数据、开放数据和未知字段不获得分数，也不会导致房源被删除。
`evaluate()` 和 `review()` 仍可使用可解释的确定性规则并返回 `status="partial"`。`review()` 的 fallback 仍检查
证据引用、排名连续性、展示数量和证据时效，并直接删除或修正可确定的问题后返回
`passed=true`；模型不可用会以 `MODEL_UNAVAILABLE` 明确记录，不能等同于完成语义复核。
推荐的 `limitations` 会披露本轮使用了确定性评估。该模式用于本地集成测试，不能等同于完成了
独立 LLM 语义复核。C 不再复核候选是否符合硬条件，不能把 B 候选数量当作已确认合格数。
## 大模型 API 接入

`config.py` 通过 DeepSeek 的 OpenAI 兼容 `/chat/completions` 接口提供一个共享 HTTP 客户端。
B 的现有 LangGraph 节点通过 `DeepSeekChatModel.ainvoke()` 适配器使用它，C 的结构化步骤通过
`DeepSeekChatClient.complete()` 使用同一个底层实现。

Python 3.11+，安装本次验证使用的依赖版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

在项目根目录 `.env` 中填写主办方提供的密钥。已有文件可直接编辑；新环境可复制以下配置：

```dotenv
DEEPSEEK_API_KEY=
# 可选覆盖；通常使用 runtime.toml 默认值即可
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT_SECONDS=45
DEEPSEEK_TEMPERATURE=0
DEEPSEEK_MAX_TOKENS=2600
```

环境变量优先于 `.env`；默认从 `config.py` 所在目录读取，和启动目录无关。
密钥必填，`.env` 与 `.venv` 已被 Git 忽略。URL 填 API 根地址，不追加 `/chat/completions`。

运行 `__main__` 内的三组真实模型输入输出检查（需要配置密钥和联网）：

```bash
.venv/bin/python config.py
```

三组输入分别为 Tampines、Clementi、Punggol 的租房需求，通过 LangGraph 调用真实网关，
打印实际回复。每次最多生成 256 tokens，按配置限制等待时间；失败退出非零。
`--live` 仅保留命令兼容，现在默认就是实际调用，不注入模拟响应。

业务代码在服务构造时创建模型，再注入节点闭包：

```python
from config import create_chat_model

model = create_chat_model()

async def model_node(state):
    reply = await model.ainvoke(state["messages"], stream=False)
    return {"messages": [reply]}
```

模型客户端和密钥不放入共享契约、`RunContext` 或图状态。
`source_mode` 仍只描述房源来源，业务节点后续需要按 `ctx.deadline_at` 约束剩余时间。
搜索业务图已在 `graph.py` 实现。

搜索管理节点通过消息接口让模型从合法任务菜单中选择任务，再严格校验 JSON 和任务 ID。
模型不生成房源、地址或坐标；这些事实必须由 Provider 取得。
不依赖网关的原生工具调用或 `bind_tools()`。

## 3a 房源搜索与详情

`part3/capabilities/listings.py` 提供两个异步内部能力，供 LangGraph 执行节点调用：

- `ListingsCapability.search_page(plan, query_id, *, ctx, cursor=None)`：执行一页搜索，返回
  `Result[ListingPage]`，包含标准 `Listing`、下一页游标、分页可信状态、截断标记和筛选覆盖。
- `ListingsCapability.read_detail(listing, *, ctx)`：读取详情并合并证据，返回 `Result[Listing]`。
  详情失败保留搜索页数据并返回 `partial`；同口径价格冲突保留两份证据，金额设为 `None`。

运行模块 `__main__` 内的三组真实搜索及详情检查：

```bash
.venv/bin/python part3/capabilities/listings.py --output /tmp/listings-live.json
.venv/bin/python providers/guru_search.py
```

输入来自共享 `SearchPlan` / `RunContext` 契约，默认查询 Tampines、Clementi、Punggol。
打印实际 3a 输入、搜索输出和详情输出；没有预设的房源结果，也没有独立测试文件。
三组搜索均取得候选且详情成功后才通过。两个模块也支持 `python -m` 运行。
详情验收还会逐项核对页面明确标注的家具、挂牌日期、房东同住、水电、Wi-Fi、做饭、
访客、宠物、共享浴室和产权年限，以及对应的详情证据；仅请求成功不足以通过。
适配器同时读取语义标签和图标标签，复用图标必须匹配明确原文；例如挂牌 ID 不会被当成
水电信息，`Not tenanted` 不会被当成访客规则，租期不会覆盖产权年限。

真实来源需要 Node.js 20+、OpenCLI 和已连接的 Browser Bridge。安装或更新适配器时，
必须一起复制搜索、详情和字段转换三个文件（只复制 search.js 会缺少模块）：

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
opencli propertyguru search Tampines --listing rent --max 3500 --page 1 --limit 3 --output-mode page -f json
opencli propertyguru detail <房源ID> --output-mode structured -f json
```

浏览器安装并启用 [OpenCLI 扩展](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk)，
`opencli doctor` 应显示 daemon 和 extension 已连接。搜索读取公开房源，不需要发送询盘。
Python 自动寻找 PATH 或 `~/.npm-global/bin/opencli`，也可在 `.env` 配置 `OPENCLI_BIN`。

默认搜索/详情命令仍保留原来的列表/摘要形式。Python Provider 使用新增的 `page` / `structured`
形式；旧版适配器输出会报告解析失败。分页游标由来源生成，形如 `pg:v1:2:0`；不自行猜测末页。
中途达到候选上限时，游标保留当前页偏移量，以便下一次搜索继续。页面变动导致偏移失效时报错。

在一次 `search` 执行开始时构造一个共享额度对象，并把 Provider 注入能力对象：

```python
from execution.budget import SearchBudget
from providers.guru_search import GuruSearchProvider
from part3.capabilities.listings import ListingsCapability

# plan、ctx 由编排层传入；两者的 source_mode/attempt_id 必须一致。
budget = SearchBudget(plan, ctx)
listings = ListingsCapability(GuruSearchProvider(), budget)

async def listings_node(state):
    result = await listings.search_page(
        state["plan"], state["query_id"], ctx=state["ctx"],
        cursor=state.get("cursor"),
    )
    return {"listing_page_result": result}
```

Provider 和额度对象留在服务/节点闭包中，不放入可序列化图状态。每次执行新建额度对象，
同一次执行中的查询共享它；失败调用也消耗页数尝试。此额度实现用于单进程执行，恢复持久化图时
需由后续执行层恢复已使用额度，不能重置后继续调用。

目前来源支持交易类型及符合 SGD 月租/总价口径的价格上限参数。最低卧室数、整租/单间、
规范地点 ID 和货币/周期校验会如实列入未支持筛选项，交给后续筛选处理；自由文本地点检索不代表
已经验证房源地区。3a 不放宽硬条件，也不承担搜索管理、最终汇总或周边/通勤调查。

适配器从页面 `__NEXT_DATA__` 脚本读取公开数据，兼容 Browser Bridge 隔离执行环境。
详情读取房源自身 `listingDetail.location.address` 的地址和邮编，供 3b 查询；
不能把附近 MRT 的展示模板当作房源地址。

## 2 / 3a / 3b 联调

搜索业务子图已经接通：`supervisor → execute → supervisor → aggregate → END`。
管理层先只开放 3a 搜索页任务（含可复用页面），搜索阶段结束后才开放 3a 详情和 3b 地址定位。
Agent 在当前阶段内选择一项任务，代码禁止跨阶段调用；额度耗尽可以进入补充阶段，但不代表搜索完整完成。
搜索结束后汇总为契约规定的 `Result[SearchResult]`，不修改 A 给出的硬条件。
`part1/planner.py` 的计划生成已接通；3c 周边设施与 3d 通勤也已接入，见文末能力说明。

```bash
.venv/bin/python -m part2.supervisor
.venv/bin/python -m part3.capabilities.location
.venv/bin/python -m providers.onemap
```

以上命令全部进行真实外部调用，需要配置和联网。每个模块默认至少三组输入，
将实际输出打印为 JSON；没有模拟 Provider、预设模型决策或预设坐标。
`part2.supervisor` 检查每组都有成功的搜索、详情、唯一定位，以及真实模型决策；
模型降级或定位不确定都不会算作联调通过。同时检查每轮模型菜单不混合阶段，所有搜索任务先于补充任务执行。
每组默认只实际取一页、一个候选，并用第二个查询 ID 复用真实页面，验证已有候选时仍须先处理剩余搜索任务，
因此结果可能因候选额度返回 `partial`；这不等于已经找到符合所有硬条件的房源。
阶段测试默认聚焦 2→3a→3b，暂不注册可选配套 Provider；加 `--with-investigations` 可同时执行已注册的补充调查。

```bash
.venv/bin/python part2/supervisor.py --output /tmp/search-live.json
```

2026-09-18 本机真实验证：Tampines、Clementi、Punggol 三组完整链路全部通过，
对应实际房源 ID 为 `500252593`、`500256915`、`60052380`。
每组均由真实网关选择任务，经 Browser Bridge 读取搜索页与详情，再调用 OneMap 定位。
三条定位结果均为楼栋精度，模型没有降级；共享输出契约校验通过。
另将这三条实际房源作为 3b 的 `--input` 输入，真实定位也通过 3/3。
这些是原始候选，不能据此宣称它们满足整租或最低卧室数条件。

### OneMap 配置

3b 使用 [OneMap Search API](https://www.onemap.gov.sg/apidocs/search)。
它现在需要 Token；[鉴权接口](https://www.onemap.gov.sg/apidocs/authentication)
返回 Token 和到期时间。往现有 `.env` 添加以下配置之一：

```dotenv
# 方式一：手动提供有效 Token
ONEMAP_TOKEN=

# 方式二：配置注册邮箱和密码，由 Provider 获取并在到期后重新获取 Token
ONEMAP_EMAIL=
ONEMAP_PASSWORD=
```

可以同时提供 Token 和邮箱/密码；Token 无效时最多重新鉴权一次。
只有 Token 时，到期返回 `AUTH_REQUIRED`，不会把鉴权失败当成地址无匹配。
敏感配置不进入图状态、模型消息或返回结果。默认 OneMap 请求间隔 0.25 秒，
每次地址查询最多读取 3 页；候选未读完时不会宣称唯一匹配。

### 在代码中调用

```python
from api import create_live_search_service

# 构造时注入现有大模型网关、GuruSearchProvider、OneMapProvider。
# 服务可以复用，每次 search 会独立创建额度、状态和执行缓存。
service = create_live_search_service()
result = await service.search(plan, ctx=ctx)

# 联调需要查看内部执行历史和定位候选时，用 run 替代 search：
# state = await service.run(plan, ctx=ctx)
# state["history"] / state["locations"] / state["result"]
```

`plan` 和 `ctx` 来自 A/编排层，仍使用 `contracts_v0.py` 的字段：
`source_mode="live"`，查询来源为 `propertyguru`，`attempt_id` 一致，
`ctx.deadline_at` 是带时区的未来时间。
不要先调用 `run` 再调用 `search` 来获取同一次结果，那会启动两次搜索；
`run` 返回的 `state["result"]` 是 B 内部汇总完成的 SearchResult。
如需要重新整理已取得的内部状态，也可调用 `part45.aggregation.aggregate(state, started)`，
其中 `started` 是执行前记录的 `time.monotonic()`；重新汇总不会调用外部服务。

内部调试也可以用 CLI 读取 B 生成的 JSON 输入文件，结构为 `{"plan": {...}, "ctx": {...}}`：

```bash
.venv/bin/python -m part2.supervisor --live --input /绝对路径/search-input.json
```

真实联调需要 DeepSeek 配置、OneMap 凭据，以及 OpenCLI / Browser Bridge 环境。
没有 OneMap Token 或账户密码时，真实服务构造直接报告 `AUTH_REQUIRED`，不会假装定位成功。
模型不可用或返回非法任务时，生产流程会改用固定调度并在 `issues` 中明确说明；
真实联调验收仍判失败。`SearchService(model=None)` 可显式使用固定调度。

共享契约保留 `source_mode="mock"` 以兼容其他模块；本次默认入口及验收只使用 `live`。

### 定位与结果边界

- 3b 从 3a 的 `raw_details` 读取明确标注的 Address、Postal code、Building / Project，
  或 `Location information` JSON 中的地址字段；不使用附近地铁位置冒充房源位置。
- `LocationCapability.locate(request, *, ctx)` 可由后续 3c/3d 复用。
  `LocationRequest` 和 `LocationResult` 定义在 `execution/tasks.py`，属于内部接口。
- 唯一且信息相符的候选才确认坐标；门牌/道路冲突、多楼栋或候选分页未结束时保留歧义。
  精度表示匹配到楼栋或道路，不代表 GPS 实测误差。
- 定位事实写入现有 `Listing.evidence`（`field="location"`，`value` 包含地址、
  经纬度、精度和来源模式）；未解决项进入 `field_issues` 和 `Result.issues`。
  不添加公开字段，也不拿坐标或地址替代规范 `location_id`。
- 同一次搜索的所有查询共享页数、候选额度。候选额度沿用 3a 的返回条目计数，
  包含重复条目；最终输出再按 `listing_key` 去重。额度用尽后仍可在截止时间前补详情/定位。
- 临时失败默认最多重试一次；尊重 `retry_after_seconds` 与截止时间。
  完整无匹配返回 `success` 空列表；已有房源但未完成返回 `partial`；全失败返回 `error`。
- `has_more` 只表达来源给出了可用续页游标；分页未知时结合 `queries_completed=False`
  和 `issues` 判断，不能把 `has_more=False` 单独理解为查完。
- 缓存与任务历史只在单次搜索内使用；未启用持久化断点恢复，不能用新额度对象继续旧图。

## 4 / 5 汇总与完整 search

`part45/aggregation.py` 整理管理层结束状态，`graph.py` 的 `aggregate` 节点在退出前生成
`Result[SearchResult]`。内部调用为 `await api.search(plan, ctx=ctx)`；A 使用下文的新入口 `fulfill_requirements`。
汇总阶段不会继续派工，不改写硬条件，也不执行 C 的匹配筛选或推荐。

- 按 `listing_key` 去重，合并前核对来源和模式；不把不同来源的相似标题当成同一房源。
- 合并搜索/详情/定位证据、原始描述和未解决项。证据 ID 碰撞时保留双方事实并同步修改价格引用。
- 已知字段冲突保留标记；价格冲突金额设为 `None`，状态为 `conflict`，后续重复条目不能覆盖冲突。
- 日期时间统一为 UTC，币种去除空白并转为大写。金额保留来源币种和周期，面积沿用 3a 的 sqft；
  不猜测汇率、月租换算或未知面积，不从自由文本擅自补字段。
- `applied_filters` 取实际页面的共同支持项；部分页面未应用的条件列入 `unsupported_filters`。
  重试恢复后的历史失败不进入 `failed_sources`，失效或循环游标不交给下一轮。
- 全部完成且无问题返回 `success`，包括真实空结果；有有效结果但存在缺口/截断返回 `partial`；
  未取得有效页面且失败返回 `error`、`data=None`。
- `part1.validation.validate_search_result` 校验完整输出、证据引用、追踪标识、版本、数量和覆盖一致性；
  输出校验失败使用 `INVALID_OUTPUT`，与调用方的 `INVALID_INPUT` 区分。

完整链路测试放在 `api.py` 的 `if __name__ == '__main__':` 中，调用新公开入口 `fulfill_requirements`。
默认运行 `test_all.py` 中按 PropertyGuru 实际挂牌设计的四种不同业务需求，
具体验收方式见下文。真实调用模型、guru_search、定位服务，不注入参考房源。
测试脚本直接传入请求与上下文，打印入口原始返回。

```bash
.venv/bin/python -B api.py
```

4/5 自身的真实输入输出检查也在模块 `__main__` 中；给定至少三组搜索输入后，
执行实际管理/搜索/定位，把最终内部状态送入汇总并保存两侧实际数据：

```bash
.venv/bin/python -m part45.aggregation --input /绝对路径/search-inputs.json --output /tmp/aggregation-results.json
```

2026-09-18 完整 `api.search` 真实验收通过 3/3：Tampines / Clementi / Punggol，
房源 ID 分别为 `500252593` / `500256915` / `60052380`。
每组取得真实详情和 OneMap 定位证据，模型无降级；最终均因一候选额度返回 `partial`，
唯一问题为 `BUDGET_EXHAUSTED`，并保留 `pg:v1:1:1` 续页游标。
另对这三套房源在两次真实调用中的观测做合并检查，证据碰撞改名、价格引用与重复合并幂等性均通过。
这次验收覆盖有候选且额度截断的实际链路；未用虚拟响应伪造空结果或外部服务故障。

详情读取优先使用搜索页返回的完整房源链接。搜索和详情适配器在导航后每 300 毫秒检查
`__NEXT_DATA__` 中的实际数据，最多等待 12 秒；OpenCLI 的 `page.wait()` 会在 DOM 稳定后
提前返回，不能用它判断房源数据已经加载。检查同时核对当前查询或房源 ID，避免读取旧页面。
持续未就绪返回 `TEMPORARY_UNAVAILABLE`，由现有管理层在剩余额度和截止时间内最多重试一次。
搜索页明确标记为 `promoted-listing-card` 的相近价格广告在候选截断和续页偏移计算前排除，
防止超预算广告混入搜索命中；普通搜索卡保持原顺序。不能读取的数据不会被当成成功详情或虚构地址。
两项命令均自行直达目标 URL，关闭 OpenCLI 默认的首页预导航，避免额外跳转被浏览器拒绝
而在执行适配器前就报 `Navigation rejected`。若目标导航仍遇到这个明确的浏览器错误，
只释放当前适配器的标签页租约，并在新标签页中恢复一次；仍须通过相同的 URL、数据和验证页检查，
不增加业务搜索重试或重置用户浏览器。

## 新接口接入与完整联调

A 只调用 `api.fulfill_requirements(request, *, ctx)`。输入是已确认的
`RequirementRequest`，输出是 `Result[RequirementFulfillment]`。不要再由 A 构造查询、
计划、历史或 Provider 参数。B 内部保留 `prepare_query`、`build_search_plan`、`search`
三个契约函数，供模块联调使用；它们的画像参数已改成 `ConversationProfile`。

```python
from api import fulfill_requirements

result = await fulfill_requirements(request, ctx=ctx)
if result['data'] is not None:
    fulfillment = result['data']
    # needs_clarification 时由 A 提问；否则把 search_result 候选交给 C 筛选。
    questions = fulfillment['clarification_questions']
    search_result = fulfillment['search_result']
```

外层 LangGraph 执行 `prepare_requirements → plan_search → execute_search → summarize_requirements`。
原始请求一直保留在当前图状态；内部正规投影提取可推送到 guru_search 的过滤条件，
其余 Listing 条件在结果上确定性核对，不能因为 SearchPlan 字段较少而丢失。
没有修改用户确认的条件，也没有伪造完整 ConversationProfile。B 信任 A 发出的确认交接；
A 仍负责核实 `confirmed_version == version`，B 检查请求的版本、确认时间与会话一致性。
`ctx` 原样传到各阶段；允许 `attempt_id=None`，这时 B 为计划生成内部轮次 ID。

候选房源保留真实字段、原始来源和证据，由 C 继续筛选、排序。条件不符或字段未知会明确
记录到 `field_issues`，未知硬条件会造成 `partial`，不会把未知当满足。搜索预算耗尽同样
返回 `partial` 和实际续页信息。房源字段本身沿用最新共享 Listing，不增加私有返回字段。

派生需求逐条返回 fulfilled / unsupported / unverified；已支持真实找房、详情、OneMap
地址定位、通勤与五类周边配套；环境与行政区归属核验尚不支持。按地区词检索不等于已核实行政区，
坐标也不证明通勤时间。开放需求只能尽力补查，目前会明确列入
`skipped_best_effort_requirement_ids`，即使标记 hard，也不会阻断搜索或单独降低完成状态。
缺少会阻断核心查询的信息时，返回 `needs_clarification` 和结构化问题；无需初始化外部服务。

模型与浏览器、OneMap 的配置沿用上面的说明。搜索预算来自 `.env` 或进程环境：

```dotenv
SEARCH_PAGE_LIMIT=4
SEARCH_CANDIDATE_LIMIT=12
SEARCH_PAGE_RESULT_LIMIT=6
SEARCH_PROVIDER_TIMEOUT_SECONDS=30
SEARCH_MAX_RETRIES=1
SEARCH_PLANNER_TIMEOUT_SECONDS=20
SEARCH_SUPERVISOR_TIMEOUT_SECONDS=8
SEARCH_SUPERVISOR_MAX_CALLS=3
SEARCH_FINALIZE_RESERVE_SECONDS=10
```

以上是默认值，进程环境优先于 `.env`。计划中的显式总额度仍优先；每次搜索页返回
不超过 6 条且不超过剩余候选额度。页数包含失败尝试，重试也不能突破总额度。
计划模型与管理模型分别限制为 20 秒和 8 秒，同时受 `DEEPSEEK_TIMEOUT_SECONDS` 和本轮剩余
时间限制。管理模型每轮最多调用 3 次（失败也计入），之后使用固定调度继续执行。
浏览器调用继续串行。截止前 10 秒停止并取消尚未完成的外部任务，保留已有结果进入
汇总；不改写 `ctx.deadline_at`，未查完仍返回 `partial` 或 `error`。

执行 `python graph.py` 可运行文件内三组真实搜索配置检查（Tampines、Clementi、Punggol）；
每组默认总时限 120 秒，可用 `--timeout-seconds` 修改。检查真实候选、单次条数、页数、
模型调用次数/耗时、重试及汇总预留时间；`--output` 可保存实际输入输出。来源失败时
不会用虚拟数据代替，也不会将没有真实候选的用例记为通过。

可复用 `api.create_live_fulfillment_service()`；内部联调用 `service.run(request, ctx=ctx)`
可读取查询、计划、SearchResult 和最终结果。每次请求使用独立状态与预算，不启用跨请求的
历史持久化或自动续页。`SearchDirective` / `AttemptSummary` 仍可用于 B 内部显式续页，
但不是 A 的公开输入；内部历史指纹使用 `execution.history.query_fingerprint`。

`test_all.py` 默认运行以下四种不同需求。2026-09-20 已打开 PropertyGuru 详情页核查设计依据；
这些是遵守 `RequirementRequest` 的测试需求，不是 A 的生产日志，也不伪造 B 的返回。

| 序号 / ID | 输入需求 | 实际挂牌依据 |
| --- | --- | --- |
| 1 / `tampines_condo_rent` | 淡滨尼整租公寓；月租 ≤ SGD 3800；至少 2 卧；家具齐全 | [Treasure at Tampines](https://www.propertyguru.com.sg/listing/for-rent-treasure-at-tampines-25155665)：3500/月，2 卧，家具齐全 |
| 2 / `clementi_common_room` | 金文泰组屋普通房；月租 ≤ SGD 1300；包水电和 Wi-Fi | [712 Clementi West Street 2](https://www.propertyguru.com.sg/listing/hdb-for-rent-712-clementi-west-street-2-500255206)：1200/月，普通房，包水电网络 |
| 3 / `punggol_family_rent` | 榜鹅整租组屋；月租 ≤ SGD 4200；至少 3 卧 2 卫、1000 sqft | [203A Punggol Field](https://www.propertyguru.com.sg/listing/hdb-for-rent-203a-punggol-field-25359579)：3800/月，3 卧 2 卫，1184 sqft |
| 4 / `bishan_hdb_buy` | 碧山购买组屋；总价 ≤ SGD 1000000；至少 3 卧、1000 sqft | [207 Bishan Street 23](https://www.propertyguru.com.sg/listing/hdb-for-sale-207-bishan-street-23-500255052)：920000，3 卧，1109 sqft |

每组都有独立请求/会话 ID、完整中文原文和正确的 `SourceReference` 偏移。
买房使用 `intent=buy`、`transaction_type=sale`、`price.period=total`；单间不拿整套卧室数限制房间。
上表保留四组需求的历史挂牌依据，不作为接口输入或预期返回。

四组完整请求写死在 `test_all.py` 的 `request_1` 至 `request_4` 中。
运行时逐组构造 `ctx`（截止时间为调用时起五分钟），直接调用
`await fulfill_requirements(request, ctx=ctx)`，打印原始返回。
脚本不读取示例文件，不包含额外校验、报告、内部调度或命令行选项。

```bash
.venv/bin/python -B test_all.py
```

`api.py` 的运行入口复用同一调用；`python -m part1.planner` 只运行内部计划生成。
其他模块仍通过 `INPUTS` 复用这四组请求。

以下为旧输入的历史链路记录，不代表上述新需求或 examples 用例验收通过：
2026-09-20 已实际执行新公开接口的四组完整链路，4/4 通过：共 8 套 live 房源，
8 套都有真实详情与 OneMap 定位证据，无模型或来源调用失败。四组因两候选预算返回
partial；Tampines 和 Punggol 各另有一套出租范围待核实。Punggol 的 hard + best_effort
开放需求被明确跳过并保留房源。行政区核验标记 unsupported，未将坐标当作行政区证明。
另有三组缺币种/周期的实际需求通过澄清分支检查；解析、查询转换各四组通过，
需求汇总使用历史真实上游结果回放四组通过，并核验了本次四组最终真实产物。

## 3c 周边设施与 3d 出行

3c 从住宅坐标查询地铁/轻轨站、公交站、超市、学校、公园。默认半径为 **1500 米直线距离**；
用户明确的距离上界优先，支持 1–4999 米。OneMap 提供交通和公园点位；OpenStreetMap
提供超市与学校（含幼儿园、学院、大学）点位。OneMap Parks 主题中明确标为游乐场的记录不当作公园。
默认 Overpass 节点为 OSM 文档列出的全球镜像 `https://maps.mail.ru/osm/tools/overpass/api/interpreter`；
可用 `OVERPASS_URL` 指定具有新加坡数据的其他实例。OSM 数据归属 © OpenStreetMap contributors（ODbL）。
不要配置只有其他国家数据的区域实例，否则空列表没有新加坡设施覆盖的含义。

3d 默认从住宅前往指定目标；未单独提出通勤需求但 `user_context` 有工作/学校地址时，补做通勤概览。
默认 **Asia/Singapore 时区，下一个未来的周一至周五 08:00 出发，公共交通组合路线**。
周一至周五未额外排除公共假日，该假设保存在证据中。用户指定步行、驾车、骑行、公交/轨道模式，
或明确日期/时刻时覆盖对应默认值。支持 ISO 日期、今天/明天/后天、星期以及中文/英文钟点；
多时段、无法明确解释的时段或地点保留缺口，不回退成用户没有指定过的条件。
到达时间要求最多用两次真实出发查询验证一个可在期限前到达的公共交通方案，不宣称最晚出发时间。
驾车、步行、骑行接口是静态路线估时，不能当作早高峰实时路况预测。

配套的步行距离/时间条件由 3c 调用同一个 3d 能力核实，每类最多查直线距离最近的 5 个点位。
只找到部分点位时不声称绝对最近；空结果或没有找到满足阈值的路线保留 `unverified`。
设施代表点可能是建筑/区域中心，不保证是校门、公园入口；点位口径保存在路线证据中。
不把直线距离除以速度当作真实步行耗时，也不按任意同类学校替代具体校名。

完整请求通过 `FulfillmentService → SearchService.search_for_request` 显式进入搜索图，
不改变公开 `fulfill_requirements` 或共享 `search(plan, *, ctx)` 签名。
现有“搜索 → 补充 → 汇总”的调度阶段不变；补充阶段增加 `amenities` 和 `travel` 任务。
单次搜索默认最多 60 次路线调用、60 次设施类别查询；缓存按本次用户/会话/运行隔离，失败不记为成功缓存，
所有实际调用遵守同一个 `ctx.deadline_at`，模块 2 对可重试故障最多再试一次。

输出只使用共享契约原有字段：

- `Listing.evidence` 中的 `nearby_amenity.<category>`：查询范围、来源点位、直线距离和覆盖限制。
- `travel`：起终点、方式、指定时段、实际路线分段、距离、耗时及默认假设。
- `derived_requirement.<requirement_id>`：调查完成状态及条件比较结果。
- `field_issues`、`Result.issues`：未定位、来源故障、额度不足等缺口。

`fulfilled` 表示已完成相应调查，不表示房源满足阈值。例如真实通勤 55 分钟，需求上限 40 分钟：
调查可以完成，但对应证据的 `check=fail`。C 应读取该比较结果和真实证据继续筛选。
受支持需求有候选尚未调查完时为 `unverified`，并返回 `partial`；不支持的指标仍为 `unsupported`。

测试入口保留在各模块自身的 `if __name__ == '__main__':`，不新增独立测试文件：

```bash
.venv/bin/python -B -m part3.capabilities.travel --input /绝对路径/真实通勤输入.json --output /tmp/travel-results.json
.venv/bin/python -B -m part3.capabilities.amenities --input /绝对路径/真实设施输入.json --output /tmp/amenity-results.json
```

通勤输入是至少三项 `{listing, location, requirement, user_context, ctx}`，房源和定位必须来自实际上游。
可用 `expected` 指定要断言的方式、小时、分钟或歧义结果；不注入任何预设地图响应。
设施输入是至少三项 `{request, ctx}`，其中 `request` 为 `execution.tasks.AmenityRequest`。
每组五类设施都实际请求；测试保留一次有界重试前后的返回值，不隐藏外部故障。

2026-09-20 本次真实验收：3c 的 Tampines / Clementi / Punggol 三组全部通过，五类设施均取得真实响应；
第三组保留了一次外部超时及重试恢复记录。3d 三组真实路线通过（默认公交、09:00 公交、08:30 驾车），
另一个 NUS 邮编对应多楼栋的真实负例正确返回待核实，共 4/4。完整公开测试请求经实际
A→B→guru_search→OneMap/OSM→需求汇总，通勤、步行公交站、附近超市三项均为 fulfilled；
整体 partial 的唯一原因是一候选搜索额度用尽。该结果不表示已经找完所有房源。
共享 44 个接口示例通过结构校验，未改写或注入示例中的虚构房源数据。
