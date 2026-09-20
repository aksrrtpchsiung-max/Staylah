# falcon-show-me-you-agents

- `contracts_v0.py`：A、B、C 共享的 Python 接口契约。
- `build_contract_examples.py`：生成并验证接口示例。
- `CONTRACT_CHANGES_FOR_BC.md`：字段变更和迁移说明（2026/09/14）。

运行契约示例检查：

```bash
python3.11 build_contract_examples.py
```

## 大模型 API 接入

`config.py` 使用 `langchain_ollama.ChatOllama` 接入主办方的 AWS LLM Gateway，
可供 LangGraph 节点同步或异步调用。参考
[Starter Kit 接入示例](https://github.com/kenken64/ShowMeYourAgent-Starter-Kit/blob/bffda0d15c494abef9202cab8feb13e067a40d1d/test_llm_gateway_langgraph.py)。
网关使用 Ollama `/api/chat` 协议，默认模型为 Claude Sonnet 4.5；本地只运行客户端。

Python 3.11+，安装本次验证使用的依赖版本：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install langgraph==1.2.11 langchain-ollama==1.1.0 python-dotenv==1.2.3
```

在项目根目录 `.env` 中填写主办方提供的密钥。已有文件可直接编辑；新环境可复制以下配置：

```dotenv
LLM_GATEWAY_URL=https://api.softwaresystems.app
LLM_GATEWAY_API_KEY=
LLM_MODEL=global.anthropic.claude-sonnet-4-5-20250929-v1:0
LLM_TIMEOUT_SECONDS=60
LLM_TEMPERATURE=0
LLM_MAX_TOKENS=2000
```

环境变量优先于 `.env`；默认从 `config.py` 所在目录读取，和启动目录无关。
密钥必填，`.env` 与 `.venv` 已被 Git 忽略。URL 填网关根地址，不追加 `/api/chat` 或 `/v1`。

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
管理层从合法任务中选择一项，执行 3a 的搜索/详情或 3b 的地址定位。
搜索结束后汇总为契约规定的 `Result[SearchResult]`，不修改 A 给出的硬条件。
`part1/planner.py` 的计划生成现已接通，见下面的完整联调说明；3c/3d 尚未实现。

```bash
.venv/bin/python -m part2.supervisor
.venv/bin/python -m part3.capabilities.location
.venv/bin/python -m providers.onemap
```

以上命令全部进行真实外部调用，需要配置和联网。每个模块默认至少三组输入，
将实际输出打印为 JSON；没有模拟 Provider、预设模型决策或预设坐标。
`part2.supervisor` 检查每组都有成功的搜索、详情、唯一定位，以及真实模型决策；
模型降级或定位不确定都不会算作联调通过。每组默认只取一页、一个候选以控制调用量，
因此结果可能因候选额度返回 `partial`；这不等于已经找到符合所有硬条件的房源。

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
`run` 返回的 `state["result"]` 已是汇总完成的公开结果。
如需要重新整理已取得的内部状态，也可调用 `part45.aggregation.aggregate(state, started)`，
其中 `started` 是执行前记录的 `time.monotonic()`；重新汇总不会调用外部服务。

也可以用 CLI 读取 A 提供的 JSON 输入文件，结构为 `{"plan": {...}, "ctx": {...}}`：

```bash
.venv/bin/python -m part2.supervisor --live --input /绝对路径/search-input.json
```

真实联调需要 LLM Gateway 配置、OneMap 凭据，以及 OpenCLI / Browser Bridge 环境。
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
`Result[SearchResult]`。公开调用保持 `await api.search(plan, ctx=ctx)`；最终结果由编排层交给 C。
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

完整链路测试放在 `api.py` 的 `if __name__ == '__main__':` 中，直接调用公开 `search`。
默认三组实际输入为 Tampines（月租上限 SGD 4000）、Clementi（4500）、Punggol（4000）。
每组一个候选，真实调用模型、guru_search、OneMap，不注入虚拟房源、模型回复或坐标。
检查实际详情/定位证据、输出契约、输入不被修改，以及实际房源重复合并的幂等性。
额度导致的 `partial` 是预期结果；来源失败、模型降级或缺少定位证据均不算通过。

```bash
.venv/bin/python api.py --output /tmp/falcon-search-complete-live.json
# 也可传入至少三组 [{"plan": ..., "ctx": ...}, ...]；截止时间需为未来时间：
.venv/bin/python api.py --input /绝对路径/search-inputs.json --output /tmp/search-results.json
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

详情读取优先使用搜索页返回的完整房源链接。固定加载等待结束后，适配器还会有界等待
`__NEXT_DATA__` 中的实际详情数据；持续未就绪返回 `TEMPORARY_UNAVAILABLE`，由现有管理层
在截止时间内最多重试一次。不能读取的数据不会被当成成功详情或虚构地址。

## 1 制定计划与完整联调

`api.build_search_plan(profile, query, previous_attempts, directive, *, ctx)` 现已实现。
四项业务输入分别来自 A 的 `UserProfile`、`QueryFeatures`、历史 `AttemptSummary` 数组和
可选的 `SearchDirective`，字段严格遵守 `contracts_v0.py`。

计划模块使用 LangGraph 的 `propose_queries → materialize_plan` 两个节点。真实网关模型
根据需求选择已有地点/别名的查询表述和顺序；代码复制已确认硬条件、绑定来源、轮次及额度，
再校验 `Result[SearchPlan]`。模型不生成房源，输入或计划有问题时返回 `error`，不进入搜索。
画像、查询及补搜指令版本必须一致；本轮 `ctx.attempt_id` 必须非空且不与历史轮次重复。
未澄清的需求返回 A，计划生成不执行 `prepare_query`，也不保存后续搜索无法读取的私有画像缓存。

```python
from api import build_search_plan, search

plan_result = await build_search_plan(profile, query, previous_attempts, directive, ctx=plan_ctx)
if plan_result['status'] == 'success':
    # 两次 ctx 保持相同 attempt_id、source_mode、trace_id 与 deadline_at，使用不同 call_id。
    result = await search(plan_result['data'], ctx=search_ctx)
```

可通过 `.env` 或进程环境配置整次搜索的额度，默认与 `test_search.py` 一致：

```dotenv
SEARCH_PAGE_LIMIT=1
SEARCH_CANDIDATE_LIMIT=2
```

这些不是每个查询的单独额度。增大候选数会增加真实详情、定位和模型调用次数。
默认 live 服务只注册 `propertyguru`，不凭模型输出注册其他来源。
复用模型连接时可用 `api.create_live_planner_service()`；独立计划生成不要求 OneMap 或浏览器就绪。

按 `test_search.py` 的风格，`test_all.py` 中列出四组完整输入：
Tampines 整租、Clementi 单间、Punggol 家庭整租、Bishan 买房。可以直接修改
`search_input_1` 到 `search_input_4`，每组都包含四项计划输入，而不是手写 `SearchPlan`。

```bash
.venv/bin/python test_all.py
# 保存实际输入、生成的计划以及 search 完整返回值：
.venv/bin/python test_all.py --output /tmp/falcon-test-all-live.json
# 只运行一组，或只测试计划生成：
.venv/bin/python test_all.py --case 1
.venv/bin/python test_all.py --plan-only
# 模块自己的 __main__ 使用同四组真实计划输入：
.venv/bin/python -m part1.planner
```

所有结果都来自实际模型、guru_search 和 OneMap，不注入虚拟房源、坐标或模型回复。
联调验证计划输出与搜索输入一致、硬条件和输入保持不变、公开输出通过校验，且实际房源包含
详情及定位证据。额度截断的 `partial` 可以通过联调；来源失败、模型降级和缺少证据不会算通过。
每组输出 `Result[SearchResult]`，其中房源是交给 C 筛选推荐的候选；本模块不实现 C 的推荐排序，
也不将未支持的硬条件宣称为已满足。默认执行全部四组，失败退出非零。

2026-09-19 本机实际执行 `test_all.py` 全部四组通过：每组真实生成计划并取得两套房源，
均包含详情和 OneMap 定位证据；计划原样进入 search，模型没有降级。四组搜索均因两候选额度
返回 `partial`，唯一问题是 `BUDGET_EXHAUSTED`。另使用真实返回的续页游标，在独立进程验证了
历史查询恢复，并验证重复的已执行别名返回 `NO_NEW_QUERY`；没有用虚拟响应替代真实调用。

### 历史与补搜

`SearchDirective` 支持保持条件的续页、同一实体别名和已注册来源切换。为了在 v0 的四项输入内
恢复原查询，`execution.history.query_fingerprint(plan, query)` 返回可还原的 `search:v1:` 字符串，
供 A 写入 `AttemptSummary.query_fingerprints`。调用方应记录实际执行过的查询及游标，不把尚未
执行的计划查询当作已执行；`eligible_count` 仍由后续筛选给出，不能拿搜索候选数代替。

```python
from execution.history import query_fingerprint

# executed_query 来自实际执行记录，cursor 使用该次执行的起始游标。
fp = query_fingerprint(plan, executed_query)
# 下一轮 directive 使用 search_result['data']['coverage']['next_pages'] 中的真实游标。
```

指纹包含查询文本、来源、游标及条件，不包含连接或密钥。续页从传入历史恢复，不依赖内存中的
旧计划；相同来源、文本和游标不会因更换查询 ID 被重复执行。旧式不含文本的历史指纹仍可用于
相同 ID 的查重，但无法独立恢复续页，缺信息时明确返回问题给 A。没有可执行新查询返回
`NO_NEW_QUERY`；来源未注册返回 `SOURCE_UNAVAILABLE`，不会自动放宽条件。

历史中的 `error` 轮次保留原查询和条件用于校验及续页恢复，但不参与已完成查询的去重；
来源恢复后可以重新执行同一查询。`success` / `partial` 沿用原有去重规则，旧式指纹也按
相同规则处理。失败轮次的再次执行仍受本轮页数、候选和截止时间限制。

计划模块的 `__main__` 支持读取至少三组真实失败搜索记录和对应的 A 输入，先检查原查询
恢复可选，再调用真实模型生成计划；不构造虚拟失败响应：

```bash
.venv/bin/python -m part1.planner \
  --failed-searches /绝对路径/失败搜索记录.json \
  --planner-inputs /绝对路径/对应计划联调记录.json \
  --output /tmp/planner-retry-results.json
```

失败记录结构为 `[{"input": {"plan": ..., "ctx": ...}, "output": ...}, ...]`，对应 A 输入
使用 `test_all.py --output` 保存的记录；当前回放入口只接收能确认来源调用失败的单查询
`live` 记录，不修改原始失败结果。

2026-09-19 缺陷修复验证：四组真实失败历史重新调用模型生成计划通过 4/4，原查询恢复可选；
八套已取得的真实房源再次调用详情接口，41 项明确字段及对应详情证据通过检查。其中一套
首次返回可重试的来源错误，重试一次成功。真实历史回放同时验证了否定条件、共享浴室、
家具、日期，以及租期不覆盖产权年限。本次完整 `test_all.py` 重跑因搜索页缺少
`__NEXT_DATA__` 返回 `PARSE_ERROR`，未通过完整链路验收；该搜索页读取问题仍需单独处理。
