# 新加坡房产推荐 Agent：Pipeline 设计 v1

> 历史设计资料，保留原方案。当前实现请看 [模块设计](../../模块设计.md)、[网页 API](../../web/README.md) 和 [共享契约](../../property_agent/contracts.py)，不要将本文中的未实现功能当成现有行为。

日期：2026-09-08  
状态：设计基线；用户产品决策已确认，技术选型为实施建议，尚未实现或联调。  
适用范围：独立 Web 应用，面向经纪人及其购房客户，按需搜索住宅挂牌。  
配套文档：[接口与数据契约](contracts.md)、[验收与评测](acceptance.md)。

## 1. 决策与边界

| ID | 决策 | 工程含义 |
|---|---|---|
| D1 | 独立可交付网页 | 自有前端、身份校验、后端、数据库、后台执行环境；最终用户不需要安装 skill |
| D2 | 首版实时搜索挂牌 | ListingProvider 按请求查询外部来源；不建设全量房源采集、同步和检索库 |
| D3 | 数据库存业务状态和有限证据 | 保存偏好、聊天、反馈、运行、候选证据快照及推荐；历史快照不能充当最新在售库存 |
| D4 | 具备定时任务能力 | 用户开启关注后，服务端持久化规则并自动重查；网页关闭仍可执行 |
| D5 | 模型负责推荐顺序 | 评价维度写入版本化 prompt，由模型综合判断；程序不计算加权总分，也不偷偷重排 |

“实时”指在任务执行时查询来源，减少本地库存同步滞后。来源页面本身可能过时，因此分别记录查询时间、来源更新时间和可售核实状态。

三个被借鉴的概念分别是：

- Onboard：需求访谈、偏好提取和需求变更的业务流程。
- Rough-screening：读取需求、规划搜索、比较候选、由模型排序及解释的业务流程。
- 工具层：上述流程访问房源、地图和业务数据的稳定接口。它属于工程分层，并非另一个筛选模块。

借鉴 can-rent-lah 的流程与适配器模式，不把 SKILL.md 当作应用运行依赖。流程说明转成后端 prompt、数据契约和程序节点。源码原样复用所需许可、数据来源访问权限仍按此前分析单独落实。

### 首版包含

客户档案、多轮聊天、实时搜索、必要详情查询、模型排序、推荐对比、反馈更新、关注规则、定时重查和应用内提醒。

### 首版暂缓

全量房源库、跨站精确房产实体合并、自动联系外部经纪人、合同审查、交易支付、自动购房资格结论、推荐模型训练、加权评分引擎。地图与通勤作为可插拔工具；没有接通时不生成对应事实。

## 2. 系统容器与部署

<!-- diagram: 01-system -->

~~~mermaid
flowchart TB
    U["经纪人 / 用户"] --> WEB["独立 Web 应用<br/>聊天、需求面板、推荐、关注任务"]
    WEB <-->|HTTPS + SSE| API["API 服务<br/>身份校验、业务接口、事件回放"]
    API <-->|业务读写、提交任务| DB[("PostgreSQL<br/>偏好、聊天、运行、证据快照、推荐、任务")]
    DB -->|领取持久化任务| WORKER["Worker<br/>在线任务优先 + 定时任务执行"]
    SCHED["调度循环<br/>到期关注规则"] -->|事务创建任务| DB
    WORKER --> AGENT["单一 Agent 工作流<br/>Onboard / Rough-screening / Review"]
    AGENT <-->|提取需求、规划、排序、解释| LLM["LLM API"]
    AGENT <-->|有类型的调用与结果| TOOLS["工具与服务层<br/>权限、参数、预算、硬条件、证据"]
    TOOLS <-->|实时搜索和详情| PROVIDER["ListingProvider 适配器"]
    PROVIDER <-->|API 或受控浏览器运行环境| SOURCE["外部房源来源"]
    TOOLS <-->|按需补充地理证据| MAP["地图 / 通勤接口"]
    TOOLS <-->|保存业务结果和执行证据| DB
    AGENT -.->|执行 checkpoint| DB
~~~

| 组件 | 职责 | 建议实现 |
|---|---|---|
| Web | 聊天、可编辑需求、推荐卡片与地图、关注任务与提醒 | Next.js + TypeScript |
| API | 身份和客户权限、请求幂等、消息与任务提交、SSE 事件回放 | FastAPI + Pydantic |
| Agent Runtime | Onboard、搜索规划、候选补查、模型排序、有界修复 | Python + LangGraph，单一 Agent 工作流 |
| Tools / Services | 有类型的工具、预算和硬条件校验、证据封装、业务持久化 | Python 服务模块 + Repository |
| Worker / Scheduler | 领取持久化任务、运行 Agent、扫描到期关注规则、失败恢复 | 独立常驻 Python worker；scheduler 为其中一个轻量循环 |
| Business DB | 业务记录、有限证据、任务队列、运行事件、checkpoint | PostgreSQL，SQLAlchemy + Alembic |
| ListingProvider | search / detail 标准化接口 | 授权 API 适配器；或独立受控浏览器 runner |
| ModelProvider | 模型调用与结构化结果 | 支持工具调用和结构化输出的 LLM API |

API 与 worker 使用同一后端代码库，不拆业务微服务。初版不必增加 Redis、独立向量数据库或 PostGIS；需要大规模空间检索时再扩展。地图计算可以直接调用外部工具。

### 浏览器适配器的部署前提

opencli 的浏览器能力依赖本地 daemon、Chrome 扩展和浏览器会话，不能假定一个云端 API 进程可以直接使用最终用户电脑上的 Chrome。

如果采用 opencli 路线，须提供服务端可访问的受控运行主机，在主机内管理 Chrome、扩展、会话和并发。Web 用户仍通过普通浏览器使用我们的应用，无需安装扩展。runner 仅暴露预定义 search / detail 能力，不对公网提供任意页面脚本执行接口。

该 runner 是可替换的来源适配器。授权 HTTP API 或明确标识的演示数据都实现同一契约。来源访问失败必须返回可识别错误；不能悄悄回退到模拟数据并称为实时搜索。

参考：[opencli Browser Bridge](https://github.com/jackwener/opencli/blob/main/docs/guide/browser-bridge.md)。实时来源的运行位置、访问凭据和可用性必须在首个实施里程碑完成验证。

## 3. 更新后的三条逻辑 Pipeline

| Pipeline | 触发 | 输入 | 输出 |
|---|---|---|---|
| A：客户需求与反馈 | 用户消息、需求面板修改 | 已确认档案、相关消息、新表达 | 更新的偏好、待澄清问题、反馈记录 |
| B：实时搜索与推荐 | 用户找房、明确需求修改、后台重评 | 固定版本的客户需求、执行预算 | 来源证据、由模型排序的推荐、执行记录 |
| C：定时重查与提醒 | 关注规则到期、手动检查 | 最新已确认需求、上一成功基线 | 新搜索、新推荐或有意义的变化提醒 |

A 在必要时调用 B；C 复用 B。数据接入发生在 B 执行过程中，不再设置全量挂牌采集流水线。

## 4. Agent 工作流

<!-- diagram: 02-agent-flow -->

~~~mermaid
flowchart TB
    START["用户消息 / 定时任务"] --> CONTEXT["读取客户偏好、相关聊天、反馈<br/>固定本次需求版本"]
    CONTEXT --> MODE{"用户交互？"}
    MODE -->|是| ONBOARD["模型理解输入<br/>形成需求变更或搜索意图"]
    ONBOARD --> READY{"关键需求是否明确？"}
    READY -->|否| ASK["保存问题和状态<br/>等待下一条用户消息"]
    READY -->|是| PROFILE["校验并保存用户明确的需求变更"]
    PROFILE --> PLAN["模型规划区域、条件和查询顺序"]
    MODE -->|否，使用已确认偏好| PLAN
    PLAN --> SEARCH["工具实时搜索<br/>标准化、去重、记录来源和时间"]
    SEARCH --> CHECK["程序检查硬条件<br/>区分通过、不通过、待核实"]
    CHECK --> ENOUGH{"是否有可比较候选？"}
    ENOUGH -->|否，尚有查询额度| EXPAND["模型调整搜索策略<br/>保持用户硬条件"]
    EXPAND --> SEARCH
    ENOUGH -->|否，额度已用完| EMPTY["返回无匹配或来源不可用<br/>交互时提出可选调整"]
    ENOUGH -->|是| DETAIL["模型选择需要补查的详情 / 通勤<br/>形成有限候选证据集"]
    DETAIL --> RANK["模型依据客户偏好和评价维度<br/>决定顺序、匹配点、取舍、未知项"]
    RANK --> VALID{"结构、引用、硬条件校验通过？"}
    VALID -->|否，仍有修复额度| REPAIR["把具体错误返回模型修复"]
    REPAIR --> RANK
    VALID -->|否，修复额度已用完| FAIL["报告推荐生成失败<br/>保留可核查的搜索结果"]
    VALID -->|是| VERSION{"需求版本、任务租约仍有效？"}
    VERSION -->|否| STALE["标记已被新需求或新任务取代"]
    VERSION -->|是| SAVE["事务保存推荐、助手消息或提醒<br/>保留模型顺序"]
    SAVE --> DONE["网页读取已提交结果 / SSE 通知"]
~~~

### 4.1 需求建立与更新

1. 加载客户当前档案、相关聊天和该客户反馈，不把所有用户日志混合。
2. 模型区分本次意图：补充资料、修改偏好、找房、比较、解释、设置关注。
3. 提取硬条件、软偏好和待确认字段，并关联来源消息。
4. 对用户明确表达的修改直接执行；有歧义或需要放宽硬条件时再澄清。
5. 通过版本检查后保存新的 buyer_profiles.version。

必要信息包括购房意图、预算及其含义、房产类别和区域搜索方向。通勤仅在客户提出要求时成为必要条件，并补充目的地、交通方式和出行时段。未确定某个可选偏好不应阻止搜索。

明确偏好与模型推测分开保存。推测可以成为下次澄清的线索，不能静默覆盖客户已经确认的条件。

### 4.2 搜索与候选补充

模型选择查询区域、关键词、过滤条件以及必要的详情工具。后端限定工具名单、参数、来源范围、查询次数和执行时间。

每次搜索：

- 返回 source、source_listing_id、source_url、fetched_at 和来源原始状态。
- 同一来源重复 ID 去重；跨来源疑似同一单元保留疑似关联，不按楼盘和面积强行合并。
- 保留分页、截断、失败来源和覆盖说明。完整完成计划不等于覆盖整个市场。
- 在内存中标准化币种、金额、面积单位和房型，程序检查明确硬条件。
- 将候选标为 eligible、ineligible 或 needs_verification；未知信息不能视为满足硬条件。
- 模型决定哪些候选值得进一步查详情或通勤，也决定最终比较集合。

没有合格结果时，可以搜索其他已允许区域或获取更多页；不能自行提高硬预算。额度用完后给出无匹配、部分结果或来源不可用的真实状态。

### 4.3 模型评价与排序

评价维度：客户明确优先级、预算适配、位置或通勤、空间和房型、生活方式、信息完整度，以及客户反馈中体现的取舍。

模型接收统一候选证据，输出：

- 候选引用及排序；
- 为什么符合客户需求；
- 相对其他候选的取舍；
- 缺失或待核实的信息；
- 每项事实使用的证据引用。

不设固定权重总分，不让程序按价格或某个数字再次排序。前端按照持久化的模型 rank 展示。

程序负责结构、候选 ID、证据引用、已知数值与硬条件检查。明确违规结果返回模型修复，超过次数上限则报告生成失败；不以程序评分替代模型排序。

自然语言解释的语义正确性不能仅靠引用存在性证明：发布前可加入独立的模型核对节点，开发评测中必须包含人工证据审查。这不改变最终排序由推荐模型决定。

### 4.4 输出与反馈

推荐卡片的价格、面积、状态和链接直接取自工具证据，模型提供理由与次序。分开展示合格推荐与待核实候选。没有足够房源时不凑满三套。

用户可以追问、比较或反馈。单次拒绝只记录为反馈；只有明确表达了通用偏好变化，才更新客户档案。

## 5. 在线执行、持久化与 SSE

<!-- diagram: 03-online-sequence -->

~~~mermaid
sequenceDiagram
    actor U as 用户
    participant W as Web
    participant A as API
    participant D as PostgreSQL
    participant R as Worker / Agent
    participant P as 实时房源工具
    participant L as LLM
    U->>W: 输入需求或反馈
    W->>A: POST message + Idempotency-Key
    A->>D: 同一事务保存消息、run、job
    A-->>W: 202 + run_id
    W->>A: GET run events (SSE)
    R->>D: 短事务领取任务、设置租约
    R->>D: 读取客户需求、相关聊天和反馈
    R->>L: 理解输入，提取结构化需求
    alt 需要澄清
        L-->>R: 问题及待确认字段
        R->>D: 保存问题、checkpoint、waiting_for_user
    else 可以搜索
        R->>D: 校验版本并提交明确的需求变更
        R->>L: 生成搜索计划
        R->>P: 搜索 / 详情 / 地理补充
        P-->>R: 标准化候选、来源时间、覆盖信息
        R->>D: 保存本次有限候选证据
        R->>L: 客户偏好 + 候选证据 + 评价维度
        L-->>R: 有序推荐、理由、取舍、未知项
        R->>R: 校验结果；必要时有界修复
        R->>D: 短事务核对版本和租约，保存结果及事件
    end
    A-->>W: SSE 回放已保存的执行事件和结果引用
    W-->>U: 问题或推荐卡片
    Note over W,R: 网页断开不取消任务；重连按事件序号恢复
~~~

所有可能较长的 Agent 运行先落到持久化 jobs 表，再由 worker 执行。API 接收消息后，在一个事务中保存消息、agent_run 和 job，返回 202 与 run_id。网页通过 SSE 获取阶段进度，通过结果接口读取已提交推荐。

SSE 发送可核查的行动摘要、工具完成状态和结果引用，不展示隐藏推理过程。事件有递增序号并写入 run_events；重连使用 Last-Event-ID 恢复。断网不自动取消任务，取消使用显式 API。

Onboard 提出问题后，run 进入 waiting_for_user，释放 worker。下一条消息产生新的 run，并恢复该会话 checkpoint。数据库中的客户档案是业务事实来源，checkpoint 保存执行上下文，二者分工明确。[LangGraph 持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence)

运行状态：

~~~text
queued → running → waiting_for_user
                 → succeeded
                 → partial
                 → failed
                 → cancelled
                 → superseded
~~~

waiting_for_user 为本次 run 的完成状态；用户回复创建下一次 run。长期等待的不是数据库事务或一直运行的进程。

同一会话只允许一个执行者更新 checkpoint。新消息可以排队；明确替代当前搜索的修改会使旧 run 取消或 superseded。后台任务使用独立 run/checkpoint，不写入前台正在进行的聊天上下文。

## 6. 数据库边界与逻辑模型

<!-- diagram: 05-domain-er -->

~~~mermaid
erDiagram
    USERS ||--o{ CLIENTS : owns
    CLIENTS ||--|| BUYER_PROFILES : has
    CLIENTS ||--o{ CONVERSATIONS : has
    CONVERSATIONS ||--o{ MESSAGES : contains
    CLIENTS ||--o{ AGENT_RUNS : requests
    CONVERSATIONS o|--o{ AGENT_RUNS : groups
    WATCH_RULES o|--o{ AGENT_RUNS : triggers
    AGENT_RUNS ||--o{ CANDIDATE_SNAPSHOTS : observes
    AGENT_RUNS ||--o| RECOMMENDATIONS : produces
    RECOMMENDATIONS ||--o{ RECOMMENDATION_ITEMS : orders
    CANDIDATE_SNAPSHOTS ||--o| RECOMMENDATION_ITEMS : supports
    CLIENTS ||--o{ FEEDBACK : provides
    CANDIDATE_SNAPSHOTS o|--o{ FEEDBACK : references
    CLIENTS ||--o{ WATCH_RULES : monitors
    WATCH_RULES ||--o{ NOTIFICATIONS : generates
    AGENT_RUNS ||--o{ NOTIFICATIONS : explains
    BUYER_PROFILES {
        uuid client_id PK,FK
        int version
        jsonb hard_constraints
        jsonb preferences
        jsonb unresolved
    }
    AGENT_RUNS {
        uuid id PK
        uuid client_id FK
        uuid conversation_id FK
        uuid watch_rule_id FK
        string trigger
        string status
        int profile_version
        jsonb profile_snapshot
        string model_version
        string prompt_version
    }
    CANDIDATE_SNAPSHOTS {
        uuid id PK
        uuid run_id FK
        string source
        string source_listing_id
        timestamp fetched_at
        timestamp source_updated_at
        string verification_status
        jsonb facts_and_evidence
    }
    RECOMMENDATION_ITEMS {
        uuid recommendation_id PK,FK
        uuid snapshot_id FK
        int rank PK
        string eligibility
        jsonb rationale_and_evidence
    }
    WATCH_RULES {
        uuid id PK
        uuid client_id FK
        int version
        string status
        timestamp next_run_at
        jsonb schedule
        jsonb successful_baseline
    }
~~~

ER 图突出业务关系，未展开身份提供方会话、jobs、run_events 和框架 checkpoint 表。

| 数据 | 保存目的 | 是否用于下次实时房源搜索 |
|---|---|---|
| 客户档案、偏好版本 | 恢复用户需求，供在线和后台复用 | 是，作为搜索条件 |
| 聊天、反馈 | 保持对话连续、解释偏好变化 | 是，作为客户上下文 |
| candidate_snapshots | 保存某次运行实际比较的有限候选与证据 | 否，不能充当当前库存 |
| recommendations / items | 恢复推荐报告、保留模型顺序与理由 | 可作历史比较，不作实时事实 |
| watch_rules.successful_baseline | 保存关注范围内少量 ID、价格、状态和证据摘要 | 仅用于与新的搜索结果比较 |
| jobs / run_events / checkpoints | 调度、重试、回放和恢复 | 不参与房源召回 |

candidate_snapshots 是不可变的运行证据。相同来源 ID 在不同 run 中可以有不同快照，不创建全局“当前房源”主表。下次请求必须重新查询来源；查看历史报告则显示原始查询时间。

首版可以保留本次进入模型评价或用户比较的候选，而不是所有搜索页内容。存储规范化字段和必要的证据片段，不保存整站 HTML、浏览器 cookie 或全部图片。建议运行证据默认保留 30 天；被收藏或明确保留的推荐证据按业务记录保留，并支持客户删除。此期限是初始配置建议，可调整。

### 关键关系与约束

- client 属于当前登录经纪人；所有业务查询由后端施加归属条件。
- buyer_profiles 每客户一份，含 version；每次 run 保存实际使用的 profile_snapshot 和 version。
- conversation 属于一个 client；message 属于一个 conversation。
- 每个 snapshot 属于一个 run；推荐只能引用本 run 的候选证据。
- 每个 run 最多提交一个最终 recommendation；items 的 rank 和 snapshot_id 在该推荐中不能重复。
- feedback 属于 client，可以指向候选快照，也可以是一般偏好反馈。
- watch_rule 属于 client，首版按客户最新已确认需求执行；需求版本改变后重建基线，不能混用旧版本结论。
- jobs 对用户请求幂等键、定时 tick 唯一键进行约束；notifications 对变化签名去重。

数据库索引优先覆盖：owner/client 归属、conversation 消息顺序、run 事件序号、snapshot 的 run/source/id、到期 jobs、到期 watch_rules。当前不需要全市场挂牌价格/地理检索索引。

## 7. 定时任务与提醒

<!-- diagram: 04-watch-sequence -->

~~~mermaid
sequenceDiagram
    participant S as Scheduler
    participant D as PostgreSQL
    participant R as Worker / Agent
    participant P as 实时房源工具
    participant L as LLM
    S->>D: 短事务锁定到期规则
    S->>D: 创建唯一 tick 任务并推进 next_run_at
    R->>D: 领取任务，读取规则版本和最新需求
    R->>P: 重跑关注查询；必要时复查旧推荐详情
    P-->>R: 候选、查询时间、覆盖范围和失败项
    R->>D: 读取同一需求版本的最近成功基线
    R->>R: 比较价格、来源状态、首次出现和证据变化
    alt 来源失败或结果不完整
        R->>D: 保存失败或部分结果，不用缺失推断下架
        Note over R,D: 不覆盖对应来源的完整成功基线
    else 可比较数据有变化
        R->>L: 最新偏好和证据，重新判断适配度和顺序
        L-->>R: 新推荐、实质变化说明
        R->>R: 校验；抑制仅措辞或名次抖动
        R->>D: 同一事务保存推荐、基线和去重提醒
    else 无实质数据变化
        R->>D: 更新检查状态和有效基线，不发送提醒
    end
    Note over R,D: 发布前重新检查规则仍启用、需求版本和任务租约有效
~~~

用户启用关注时保存周期、时区、通知偏好、规则版本和 next_run_at。首版 UI 可提供每日或每 6 小时；具体频率需满足来源额度和运行成本。这里设计的是产品功能，并未创建本对话的自动任务。

调度循环在短事务中锁定到期规则，写入唯一键为 watch_rule_id + rule_version + scheduled_at 的 job，并推进 next_run_at。worker 领取后使用最新偏好执行同一推荐工作流。

### 变化判断

- 可比较的变化：相同来源 ID 的价格变化、来源明确标注的状态变化、关注结果中首次出现的候选、已核实特征变化。
- “首次出现”只意味着首次被本规则观察到，不意味着刚刚发布到整个市场。
- 本次搜索没有返回某条记录，不足以判断已售或下架。可按已保存引用尝试 detail；无法确认时标为未核实。
- 纯推荐名次变化或模型措辞变化不触发提醒。先有可验证数据变化，再由模型解释是否影响客户决策。
- 同一需求版本、同一来源和查询范围内比较。某来源失败或搜索被截断时，不以缺失记录覆盖它的完整成功基线。
- 新需求版本首次运行建立新基线，不发送“市场发生变化”的误导提醒。

首次启用关注可直接以启动后的成功运行建立基线，并在任务页显示结果。完成一次可比较搜索后，将推荐、基线更新和应用内提醒放在同一事务中提交。

通知签名由规则版本、需求版本、来源 ID 和规范化变化值生成，不能使用易变的模型措辞。失败重试不产生重复提醒。来源连续失败时任务页展示异常，可合并为一次需要处理的提醒。

## 8. 可靠性与并发

### 任务领取

PostgreSQL jobs 使用短事务领取，记录 lease_owner、lease_until、lease_version、attempts 和 available_at。worker 定期续租；崩溃后任务可以重试。多个领取者可采用 FOR UPDATE SKIP LOCKED；该模式适合队列消费，不用于业务数据的一般一致性查询。[PostgreSQL SELECT 文档](https://www.postgresql.org/docs/current/sql-select.html)

工作进程只有持有当前 lease_version 才能提交结果，防止旧进程在租约失效后覆盖新执行结果。在线与定时任务分别限制并发，优先满足用户正在等待的任务。

### 三个事务边界

1. 请求提交：用户消息 + agent_run + job 一起提交。
2. 任务领取：设置租约后立即提交，不持锁调用模型或外部 API。
3. 结果发布：检查 profile.version、watch_rule.version/status（如有）、run 是否 superseded 和任务 lease_version；通过后提交推荐、事件及提醒。

模型调用、浏览器访问和地图请求都在数据库事务之外完成。FastAPI 的进程内 BackgroundTasks 可用于轻量收尾，但本设计的可恢复推荐和周期任务使用持久化队列与 worker。[FastAPI Background Tasks](https://fastapi.tiangolo.com/tutorial/background-tasks/)

### 初始执行预算（建议值，需联调调整）

每 run 最多 3 个区域、总计 6 页来源结果、60 个去重候选、20 个模型比较候选、10 次详情补查、16 次工具调用、8 次模型调用、1 次输出修复；总执行截止时间 120 秒。详情和地理查询均计入工具预算。不能把这些建议值当作已测吞吐或时延。

达到预算后明确说明搜索范围与未完成部分，不宣称找到了全市场最优房源。来源空结果、认证失败、超时和解析失败采用不同错误码。

## 9. 模型与程序的职责边界

| 模型决定 | 程序保证 |
|---|---|
| 应该追问什么 | 字段类型、身份与归属 |
| 在允许范围内如何规划搜索 | 工具白名单、参数、调用额度 |
| 哪些候选值得补查 | 来源数据结构和证据引用 |
| 不同偏好如何取舍、最终顺序 | 已知硬条件不被绕过 |
| 为什么适合、有什么不足 | 数值卡片使用工具原始事实 |
| 新变化是否值得关注 | 同一事件不重复提醒、版本不过期 |

评价维度属于版本化 prompt。模型名称、prompt 版本、工具契约版本、客户需求快照和候选证据都关联到 run。相同证据可重放评测，但不承诺模型每次生成完全相同顺序。

## 10. 安全与数据隔离

模型没有数据库连接凭据，也不能执行任意 SQL。读取客户或推荐记录都通过有身份上下文的业务服务。模型输出中的 client_id 不能成为授权依据。

房源描述、网页内容和工具返回值属于外部数据，不能修改系统指令、工具权限或客户偏好。通过固定字段封装，将引用和事实与控制指令分开。

API key、浏览器会话凭据保存在服务端；日志仅记录脱敏参数、工具状态、来源引用和耗时。SSE、结果读取、取消任务与通知接口均检查归属。支持删除客户及关联会话、证据和关注任务。

## 11. 实施里程碑

| 里程碑 | 交付内容 | 出口条件 |
|---|---|---|
| M0：来源验证 | 一个 ListingProvider、标准化样本、运行环境 | search/detail 在选定部署环境跑通；live/demo 标识准确 |
| M1：业务骨架 | Web、API、数据库迁移、身份与客户、聊天、任务/SSE | 关闭网页后任务继续，重连可恢复，同客户数据隔离 |
| M2：Agent 闭环 | Onboard、搜索规划、工具调用、模型排序、证据校验、反馈 | 完成一次需求 → 推荐 → 修改需求 → 新推荐 |
| M3：定时关注 | 规则、重查、基线、差异与提醒 | 同一变化只提醒一次，缺失记录不误判为已售 |
| M4：交付验证 | 评测样例、错误恢复、部署说明和演示脚本 | 通过 acceptance.md 的必测场景，报告实测时延与成本 |

M0 是数据能力的关键依赖。M1 与 Agent 契约开发可以先使用明确标识的固定演示来源，待 M0 完成后替换适配器。

## 12. 需要在实施中收敛的参数

以下为工程配置项，不改变已确认的产品决策：首个实时来源与权限、模型供应商和版本、浏览器 runner 是否需要、推荐/证据保留期限、默认关注周期、调用额度、部署位置。当前按可替换接口设计，避免把它们写死到 Agent 业务流程中。

## 图源

五张图的可编辑 Mermaid 源文件位于 [diagrams/](diagrams/)：系统容器、Agent 流程、在线时序、定时任务时序、业务 ER。本文内嵌内容与对应图源保持一致。
