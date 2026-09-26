# 重构验收与回退

验证日期：2026-09-27（Asia/Singapore）。原版基准：`41f86d9`。
工作分支：`codex/behavior-preserving-refactor`；未合并或改写 `binzhi5`。

## 交付范围

业务实现已归入 requirements、search、evaluation、domain、runtime 等具名目录。
A、C、搜索交接、会话编排、仓储和网页的大文件按职责拆开。按用户追加要求，真实链路通过后
删除了 45 个已被替代的旧入口/兼容文件；Python 导入与 A/B/C 诊断命令改用新路径，网页和整体编排启动命令不变。
原模块与新文件的对应关系**只维护在根目录 [模块对应表](../../模块对应表.md)**。
测试目录的 `module-map.json` 仅是旧文件缺失检查和原版 AST 归一化的数据，不是另一份人工清单。

未修改数据库表结构/迁移、剩余依赖版本、runtime.toml 参数、正式搜索使用的三个 guru_search 适配器、页面样式和快捷条件脚本。删除教学项目专用的 langgraph-cli 直接依赖声明。
源码运行仍定位原项目配置；wheel 安装沿用原内置配置回退，并带上网页静态文件。
AGENTS.md 的工作区删除不属于本次重构提交。

## 已完成的验证

| 验证 | 结果与边界 |
| --- | --- |
| 完整回归 | **166 项通过，0 失败、0 跳过**；包括 9 项隔离 PostgreSQL 测试 |
| 原有失败校准 | 原版 152 项中 9 失败、1 报错、7 跳过；10 项过时断言逐项说明见 [基线记录](README.md)，保留对应能力验证 |
| Python 行为保留 | 134 个抽取符号与原版函数体 AST 一致，仅归一化导入路径；涵盖提示词、算法、仓储和网页方法 |
| 前端语句保留 | 11 段拆出的 JavaScript 与原语句 SHA-256 一致；全部脚本语法检查通过，defer 加载顺序与资源存在性通过 |
| 输出回放 | 固定发布、追问、空结果、失败、回答恢复及 C 降级场景输出与原版相同；另回放历史真实搜索的 12 套房源，证据、顺序、覆盖、错误及输入不变 |
| 导入和契约 | 旧文件已不存在，运行代码无旧模块导入；55 个 TypedDict 的字段/类型/必填键与清理前快照一致；B 签名保持；原评审稿中被回归使用的 9 个路由用例原样迁移并通过 |
| 持久化 | 画像版本、消息历史、运行、问题、推荐、重复提交；收藏重连恢复、重复收藏、取消收藏和跨用户隔离均通过；删除前真实成功会话的 10 条推荐、顺序、消息、checkpoint 和收藏也在删除后恢复通过 |
| checkpoint | 原版生成的 A 等待确认、C 等待回答由新版恢复成功；新版生成的同类状态也能由原版恢复，覆盖代码回退 |
| 安装和启动 | 从干净目录构建的新 wheel 在仓库外导入通过，确认不含旧兼容文件，网页资源完整；网页、A CLI、整体编排 CLI 入口检查通过，直接 `python web/server.py --help` 兼容 |
| 历史数据清理 | 旧生成器及其 14 项生成器专用检查一同移除；应用回归所用 6 份夹具按原字节迁移，9 个路由用例输入/期望逐项相同 |
| 依赖 | pip freeze 中第三方依赖与原基线相同；只有 editable 本项目的 Git 提交号发生变化 |

回放保留业务标识和关联。仅把 `duration_ms` 归零；LangGraph 的运行时 `__interrupt__`
封装不作为 JSON 快照比较，但 pending_question 等载荷完整比较，真实数据库恢复另行验证。
固定边界输入包含已有测试脚本及模型故障注入，不能据此声称真实模型输出固定不变。

## 网页

原版与新版分别启动预览服务，用同一浏览器、视口和示例流程比较：

- 桌面 1440 × 1000：首页、需求确认、推荐结果。
- 移动端 390 × 844：首页、推荐结果。
- **5 组截图均为 0 像素差异**，文件摘要见 [browser-results.json](browser-results.json)。
- 浏览器实际检查：确认按钮、推荐卡片、收藏/取消收藏、快捷条件加入输入框、侧边栏键盘调整和原存储键。
- 对原版、新版注入同样的图片请求失败，两者均耗尽备用 URL 后保留占位图。
- 取消请求、超时取消、历史结构化卡片恢复和消息幂等由网页/编排测试覆盖。

截图摘要保存在 browser-results.json；二次清理已删除本地临时截图与浏览器日志。复现时先以 1440 × 1000
打开预览页，依次截图首页、点击 “Explore a sample conversation”、点击 “Looks good, find my home”，
再以 390 × 844 截图结果与重新打开后的首页。比较时清空测试浏览器的本地存储并统一视口。
这是固定预览数据的视觉回归，不代表实时房源内容逐字一致。

## 真实服务与旧文件清理

- OpenCLI daemon / Browser Bridge 正常；OneMap 三个真实地址查询 3/3 通过。
- 删除前，原固定中文需求已完成 A 确认 → B 实时搜索 → C 检索/评估/复核 → 发布，返回 10 条推荐，issues=[]。
- 删除后复跑暴露出同一个旧问题：A 有时保留“淡滨尼（Tampines）”，原区域词表只识别其中单独的名字，
  B 因而把整段双语名称传给网站，返回 PARSE_ERROR。这个失败在未经重构的原版上也曾出现。
- 修复 `9ea911c` 单独增加严格的同地点双语识别：只有括号内外两个完整名称都指向词表中同一地区才合并。
  保留原文、住房条件、来源和证据；冲突地名、未知名字和括号内的额外条件不进行猜测。
- **删除旧文件并修复后，同一中文原文再次真实跑通**：A 保留双语目标，B 查询 `Tampines`，
  C.retrieve / evaluate / review 均 success、issues=[]，最终 published / completed、10 条推荐。
  精简运行证据见 [live-cleanup-results.json](live-cleanup-results.json)。

这次发布仍采用原有候选排序和披露规则，10 条推荐并不代表每套都满足全部硬条件；缺证据、家具冲突和
来源覆盖未完成等限制仍显示在原 recommendation 中。没有更改提示词、token 额度、网站筛选或 C 的发布规则。
此前独立模型复核测试遇到过 `finish_reason=length` 并按原逻辑降级，这条历史结果仍保留；
本次最终整链路的三个 C 阶段均未降级。

旧文件对应关系只在根目录《模块对应表》中保留。旧公共 Python 导入路径不再支持；
外部脚本应按对应表迁移。Git 历史保留原文件，可回退清理提交恢复。

## 二次清理

本轮删除 70 个冗余文件、迁移 14 项必要内容，新增 4 个文档/夹具/包文件；受版本管理文件从 293 减至 227，净减少约 15.2 MiB。根目录《项目目录》中的 67 个具体路径已核对存在，全部本地 Markdown 链接有效。

移除了未被找房应用使用的计算器教学项目、专用服务配置和 CLI、过期架构草案、重复契约及示例、
旧批量数据生成工具、未使用的模拟 Provider，以及 PropertyGuru 的联系/收藏辅助工具和技能模板。
正式网页收藏仍由应用仓储提供，search/detail/contract-listing 三个网站适配器未改动。

实际使用的 6 份历史夹具移到 tests/fixtures/search，9 个路由用例移到 tests/fixtures/decision_routes.json，
仅供测试的搜索替身移到 tests/mock_search。原真实需求和计划诊断脚本改用 scripts/live_requirements.py、
scripts/live_search.py；默认输入、参数和执行逻辑保持。当前目录见根目录《项目目录》，开发文档集中到 docs。

主回归从 168 项减少到 166 项，只移除了已删除计算器的两项测试。删除它们后，发现一个 C 降级测试
依赖计算器测试写入假 API key 的全局副作用；现在改为向该测试显式注入模型故障，原发布、降级文案和
MODEL_UNAVAILABLE 断言全部保留。没有为通过测试改变业务实现。

二次清理后的 166 项回归全部通过（包括 9 项隔离 PostgreSQL 测试），安装包在仓库外导入通过，
网页全部静态文件完整、15 个 JavaScript 文件语法检查通过。源码 AST、前端语句摘要及真实房源回放仍通过。
完整真实 A→B→C 再次 published/completed，返回 10 条推荐；retrieve/evaluate 成功，review 遇到模型输出截断（finish_reason=length），按原有规则降级并记录 MODEL_UNAVAILABLE。来源覆盖和家具等
原有证据限制仍由推荐说明披露，不能把推荐数量当作全部硬条件均满足。精简记录已追加到 live-cleanup-results.json。

已提取回归输入的旧运行日志、临时截图、浏览器日志和源码缓存已删除。本地密钥、运行环境、
日常数据库和迁移历史保留。editable 安装已刷新，干净 wheel 中不再包含教学项目和测试替身。

## 可重复执行

普通回归（未提供数据库时会明确跳过 9 项 PostgreSQL 测试）：

```bash
.venv/bin/python -m unittest discover -s tests -q
for script in web/public/*.js web/public/js/*.js; do node --check "$script"; done
```

数据库测试使用单独的测试实例，不指向日常数据库。本次使用 `/private/tmp/` 下的临时
PostgreSQL、端口 55438。将下面变量设置成自己的隔离实例后初始化：

```bash
export TEST_DATABASE_URL='postgresql+psycopg://refactor@127.0.0.1:55438/postgres'
DATABASE_URL="$TEST_DATABASE_URL" LANGGRAPH_CHECKPOINT_DB_URI="$TEST_DATABASE_URL" \
  .venv/bin/python scripts/init_postgres.py
.venv/bin/python -m unittest discover -s tests -q
```

在新的目录导出原版（目录名需未被占用），然后复测跨版本恢复；每次使用新的 prefix：

```bash
mkdir /tmp/property-agent-original
git archive 41f86d9 | tar -x -C /tmp/property-agent-original
.venv/bin/python scripts/verify_checkpoint_compatibility.py create \
  --source-root /tmp/property-agent-original --prefix checkpoint-check-1
.venv/bin/python scripts/verify_checkpoint_compatibility.py resume --prefix checkpoint-check-1
```

反向验证时由当前版本 create，再由 `--source-root` 原版 resume。
真实服务检查会使用现有配置和凭据，产生正常模型/来源调用：

```bash
.venv/bin/python scripts/smoke_live_refactor.py --trace-output /tmp/property-agent-live-trace.json
.venv/bin/python scripts/smoke_live_refactor.py --source-root /tmp/property-agent-original
.venv/bin/python scripts/smoke_live_evaluation.py
.venv/bin/python -m property_agent.search.providers.onemap
```

完整冒烟脚本必须提供 TEST_DATABASE_URL，显式覆盖业务及 checkpoint 连接；它不使用日常数据。
C 冒烟使用已提交的历史公开房源记录和测试需求，不访问数据库，不验证房源当前可用性。

## 分阶段提交和回退

- `6cc03ed`：校准原版回归基线。
- `7ca2bc0`：具名业务目录、共享契约和旧入口兼容。
- `57d57af`：A/C、编排、搜索交接和仓储拆分。
- `2ebc6f6`：网页前后端拆分和静态资源打包。
- `f9d4b83`：真实记录回放、收藏补充测试、唯一对应表、文档和验证脚本。
- `9ea911c`：真实测试发现的同地点双语名称识别修复。
- `a72653f`：移除 45 个旧路径，更新导入、打包、契约快照和文档；真实链路及恢复复测通过。
- 本文所在二次清理提交：删除无用工具和资料，迁移必要夹具与诊断，补充当前项目目录；166 项回归和真实链路通过。

每个阶段以独立 Git 提交保存。只需恢复旧 Python 导入入口时，按逆序 revert 二次清理与 `a72653f` 即可；
双语名称修复是独立提交，可以单独保留或回退。回退时先保留未提交的个人改动，再从最新阶段开始按逆序 revert，
包括依赖于该阶段的后续提交；数据库无需回滚迁移。需要比较原版可建立隔离 worktree：

```bash
git worktree add --detach /tmp/property-agent-original-worktree 41f86d9
```

原版基线与 `binzhi5` 保留，未进行合并、推送或部署。不要用 reset --hard 覆盖带个人改动的工作区。
