# StayLah Web

桌面网页前端，分支 `codex/staylah-web`，已同步 `origin/main` 的 `c5e5fed`，复用 main 的 A/B/C 编排。
原 checkout 不变；前端工作目录为 `/Users/chenzuojin/Documents/Hackathon/staylah-web`。

## 启动

无需 Node 构建和额外 Web 框架即可查看界面：

```bash
cd /Users/chenzuojin/Documents/Hackathon/staylah-web
python3.11 -m web.server
```

访问 http://127.0.0.1:8080。首页的 sample conversation 可以体验需求确认、结果卡片、排序、多选及移除。示例房源和价格明确标记为演示，演示模式不会调用模型。

连接正式编排：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# 按根目录 README 配置 .env、runtime.toml，并启动 PostgreSQL、执行数据库迁移。
.venv/bin/python -m web.server --live --port 8081
```

前端和 API 同源。只监听 `127.0.0.1`，这是本地开发服务器。没有新增前端密钥；正式模式复用 `postgres_conversation_runtime`。刷新页面会开启新会话；网页会话缓存位于服务进程内，尚未实现历史会话恢复和生产身份认证。

## 三个找房入口

- 通勤找房：公交地铁、驾车、骑行、步行；时间 10–60 分钟，每 5 分钟一档；目的地文本输入。
- 地铁找房：地铁线与站名双列联动，距离为 300/500/800/1000/1500/2000 米。
- 学校找房：学校文本输入。

桌面支持悬浮打开，也支持点击、键盘、Esc 关闭。点击“添加条件”会在原有输入末尾追加一条独立要求，三种入口互不覆盖；可在输入框继续编辑。输入框随内容增高。手动输入不被模板替换。

站点数据来自 [LTA 官方站点索引](https://www.lta.gov.sg/map/mrt/index.xml)，于 2026-09-22 获取并按线路整理为 `public/mrt-stations.json`。它是静态选项快照，不表示实时运营状态。

## 本机后端状态（2026-09-22）

- `.env` 已在本地配置，权限 `0600`；Git 忽略 `.env`、`.local-tools/` 与 `.local-state/`。
- PostgreSQL 17 已安装；项目数据库位于 `.local-state/postgres`，只监听 `127.0.0.1:5432`，业务迁移和 checkpoint 初始化成功。
- 正式网页运行于 http://127.0.0.1:8081。
- 真实 A 消息已经过 DeepSeek 和 PostgreSQL 返回 `awaiting_confirmation`；B/C 网关收到真实响应；OneMap 地理编码收到真实响应。
- OpenCLI 1.8.7 已安装在 `.local-tools/`，项目的 PropertyGuru 适配器已注册；`opencli doctor` 已确认 daemon 与 Browser Bridge `v1.0.24` 均连接。PropertyGuru 搜索页和详情页的真实只读调用已通过；一次网页 A→B→C 运行进入了真实搜索，但在详情/定位阶段达到本轮截止时间并返回 `source_failure`，因此完整推荐链路仍需进一步缩小调查额度或继续排查来源耗时。

重启本项目数据库（如未运行）：

```bash
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D .local-state/postgres -l .local-state/postgres.log -o '-h 127.0.0.1 -p 5432 -k /tmp' start
.venv/bin/python -m web.server --live --port 8081
```

数据库和 OpenCLI 安装目录均为本机开发依赖，不随仓库分发。新机器按根目录 README 准备 PostgreSQL 和 Browser Bridge，并在本地配置 `.env`。

## 数据流与模块边界

仅查看各模块的公开输入输出及必要接线：A 的 `profile / confirmation`，B 的 `fulfill_requirements`，C 的 `recommendation / listing_snapshot`，外层 `ConversationOrchestrator.handle_message`。

- `POST /api/session` 创建服务端会话，返回 `session_id` 和 `mode`。
- `POST /api/turn` 使用 `X-Session-ID`，提交 `{text, message_id, selected_listing_keys?, confirmation_id?}`。
- 返回现有 `TurnResult`，并补充 `profile`、`confirmation` 和展示用 `cards`。
- **需求确认**：从 A checkpoint 读取结构化 profile；确认按钮绑定 confirmation ID 和当前 profile version。过期确认不能触发搜索，修改通过下一条自然语言消息交回 A。
- **房源组件**：C 的 `ordered_items` 按 `listing_key` 关联同一次 run 的 `listing_snapshot.items`；展示价格、户型、面积、推荐理由、取舍、未知项和来源链接。不会编造匹配比例、通勤距离或实拍图。当前 Listing 契约没有图片字段，因此使用明确标注的 CSS 建筑示意图。
- **下一轮上下文**：浏览器只传选中的 `listing_key`（最多六个）；服务端仅接受本会话此前返回的房源，从可信缓存重建卡片信息。由于现有编排入口只接受文本，适配器将房源作为明确标记的参考数据附加到本轮 text，再调用同一 conversation 的 `handle_message`。这是兼容现有入口的上下文传递，不是新增 graph state 字段，也不改变 A/B/C 内部决策逻辑。
- 待回答的 C interrupt 继续由现有 orchestrator 恢复；最终回复能力取决于当前 A/C 路由，前端不承诺模型一定能完成任意房源比较。
- 同一会话请求串行执行；失败重试保留 message ID，已完成请求返回缓存，避免重复确认/搜索。
- UI 使用 `textContent` 渲染模型/房源文本，来源链接只允许 HTTP(S)。

## 文件

- `public/index.html`：页面结构。
- `public/style.css`：淡橙、淡蓝毛玻璃样式；桌面双列卡片，小屏单列。
- `public/app.js`：需求确认、推荐卡片、选择上下文、排序、对话及错误状态。
- `public/shortcuts.js`：悬浮条件面板、地铁联动及追加 prompt。
- `public/mrt-stations.json`：带来源与日期的地铁站点快照。
- `server.py`：本地 HTTP 服务与既有编排适配器。
- `../tests/test_web.py`：确认版本、会话隔离、选择验证、幂等重试及真实 A/C graph 的离线集成验证。

## 已验证范围

```bash
.venv/bin/python -m unittest tests.test_web tests.test_orchestration -v
node --check web/public/app.js
node --check web/public/shortcuts.js
```

16 项测试通过。离线集成测试运行真实 A/C graph，B 与模型响应使用测试替身。浏览器验证了首页、需求确认、结果展示、多选状态，以及三组找房条件同时追加且保留原文。真实数据库、A、模型网关和 OneMap 的验证见上文；真实房源到 C 推荐的完整搜索尚未验收。
