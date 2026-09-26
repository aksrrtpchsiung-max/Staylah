# 重构验收与回退

验证日期：2026-09-27（Asia/Singapore）。原版基准：`41f86d9`。
工作分支：`codex/behavior-preserving-refactor`；未合并或改写 `binzhi5`。

## 交付范围

业务实现已归入 requirements、search、evaluation、domain、runtime 等具名目录。
A、C、搜索交接、会话编排、仓储和网页的大文件按职责拆开，旧 Python 导入和启动入口保留。
原模块与新文件的对应关系**只维护在根目录 [模块对应表](../../模块对应表.md)**。
测试目录的 `module-map.json` 仅是自动兼容检查的数据，不是另一份人工清单。

未修改数据库表结构/迁移、依赖版本、runtime.toml 参数、guru_search 网站工具、页面样式和快捷条件脚本。
源码运行仍定位原项目配置；wheel 安装沿用原内置配置回退，并带上网页静态文件。
AGENTS.md 的工作区删除不属于本次重构提交。

## 已完成的验证

| 验证 | 结果与边界 |
| --- | --- |
| 完整回归 | **164 项通过，0 失败、0 跳过**；包括 9 项隔离 PostgreSQL 测试 |
| 原有失败校准 | 原版 152 项中 9 失败、1 报错、7 跳过；10 项过时断言逐项说明见 [基线记录](README.md)，保留对应能力验证 |
| Python 行为保留 | 134 个抽取符号与原版函数体 AST 一致，仅归一化导入路径；涵盖提示词、算法、仓储和网页方法 |
| 前端语句保留 | 11 段拆出的 JavaScript 与原语句 SHA-256 一致；全部脚本语法检查通过，defer 加载顺序与资源存在性通过 |
| 输出回放 | 固定发布、追问、空结果、失败、回答恢复及 C 降级场景输出与原版相同；另回放历史真实搜索的 12 套房源，证据、顺序、覆盖、错误及输入不变 |
| 导入和契约 | 旧新路径共享实现；运行时类型/异常身份一致；B 公共签名保持；44 个当前契约示例通过类型和签名检查 |
| 持久化 | 画像版本、消息历史、运行、问题、推荐、重复提交；收藏重连恢复、重复收藏、取消收藏和跨用户隔离均通过 |
| checkpoint | 原版生成的 A 等待确认、C 等待回答由新版恢复成功；新版生成的同类状态也能由原版恢复，覆盖代码回退 |
| 安装和启动 | wheel 在仓库外独立目录导入通过，网页资源完整；网页、A CLI、整体编排 CLI 入口检查通过，直接 `python web/server.py --help` 兼容 |
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

截图留在本地 `output/playwright/refactor/`，不提交临时浏览器产物。复现时先以 1440 × 1000
打开预览页，依次截图首页、点击 “Explore a sample conversation”、点击 “Looks good, find my home”，
再以 390 × 844 截图结果与重新打开后的首页。比较时清空测试浏览器的本地存储并统一视口。
这是固定预览数据的视觉回归，不代表实时房源内容逐字一致。

## 真实服务：部分通过，完整链路尚未通过

1. OpenCLI daemon 和 Browser Bridge 健康检查通过。
2. OneMap 对 `200640`、`307987`、`049213` 的真实查询 **3/3 通过**。
3. A 使用真实模型完成需求解析并进入 `awaiting_confirmation`。
4. C 对历史真实房源执行真实模型检索、评估、复核：首次三个阶段均为 success，复核 passed=true。
   交付脚本追加复跑时检索、评估成功，复核遇到 DeepSeek `finish_reason=length` 空内容，
   按现有逻辑返回 `partial + MODEL_UNAVAILABLE` 并使用确定性复核。脚本据此非零退出，未把降级算作完全成功。
5. 最新 A→B→C 联网搜索在 PropertyGuru 返回 **PARSE_ERROR**，没有发布推荐。
   用未经重构的 `41f86d9`、同一环境和输入复跑，同样失败，错误为：
   “PropertyGuru 未解析到房源，页面也未明确表示无匹配，不能当作空结果”。

因此结构迁移、离线回归、数据恢复和固定网页对比已完成；**实时完整推荐验收仍有未通过项**。
本次没有混入网站解析修复、提示词调整或增加 token 额度，也不宣称能保证所有未来外部响应。

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
.venv/bin/python scripts/smoke_live_refactor.py
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
- 本验收文档所在后续提交：真实记录回放、收藏补充测试、唯一对应表、文档和验证脚本。

每个阶段以独立 Git 提交保存。回退时先保留未提交的个人改动，再从最新阶段开始按逆序 revert，
包括依赖于该阶段的后续提交；数据库无需回滚迁移。需要比较原版可建立隔离 worktree：

```bash
git worktree add --detach /tmp/property-agent-original-worktree 41f86d9
```

原版基线与 `binzhi5` 保留，未进行合并、推送或部署。不要用 reset --hard 覆盖带个人改动的工作区。
