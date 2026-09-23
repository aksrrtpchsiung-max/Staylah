# StayLah Web

StayLah Web 是 A/B/C 房源工作流的同源网页入口。静态前端和 JSON API 由
`web.server` 提供，代码不依赖个人目录；所有命令均从仓库根目录执行。

## 环境准备

1. 创建 Python 环境并安装项目依赖：

   ```bash
   python3.11 -m venv .venv
   .venv/bin/python -m pip install -e .
   ```

2. 从模板创建本机配置：

   ```bash
   cp .env.example .env
   ```

3. 按根目录 [README](../README.md) 配置 DeepSeek、OneMap、PostgreSQL 和
   PropertyGuru OpenCLI。密钥、数据库文件和 OpenCLI 安装目录均保留在本机，不提交到 Git。

## 启动方式

仅预览页面和示例对话：

```bash
.venv/bin/python -m web.server
```

连接真实 A/B/C 编排：

```bash
docker compose up -d --wait
.venv/bin/python scripts/init_postgres.py
.venv/bin/python -m web.server --live
```

服务启动后会在终端打印本次监听地址。默认绑定仅供本机开发使用；端口和绑定地址可通过
CLI 参数或环境变量配置：

```bash
.venv/bin/python -m web.server --live --host <bind-address> --port <port>

# 等价环境变量
STAYLAH_HOST=<bind-address> STAYLAH_PORT=<port> .venv/bin/python -m web.server --live
```

团队在同一网络内联调时，可绑定 `0.0.0.0`，再通过开发机的局域网 IP 和所选端口访问。
不要把内置 `ThreadingHTTPServer` 直接暴露到公网。正式部署应放在 HTTPS 反向代理之后，
并补充身份认证、持久会话、访问日志和进程管理。

## PropertyGuru 适配器

每位开发者都需要在自己的机器安装 OpenCLI，并把当前分支的适配器复制到 OpenCLI 用户目录：

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
```

`opencli doctor` 应确认 daemon 和 Browser Bridge 均已连接。分支更新后若 PropertyGuru 参数发生
变化，应重新执行复制命令，避免本机旧适配器与后端调用不一致。

## 交互行为

- 用户气泡按内容宽度自适应，并受页面最大宽度约束。
- 补充需求支持一次填写一至三项后统一提交；选择房型不会立即发送。
- 预算使用最小值和最大值输入框。
- 条件提示框通过点击展开和关闭，不依赖鼠标悬浮。
- 搜索进行中可由用户主动停止。
- 思考状态轮换显示与新加坡居住、家庭和社区相关的提示。
- 前端和后端用户可见文案统一为英文。

## API 与安全边界

- `POST /api/session` 创建进程内网页会话。
- `POST /api/turn` 使用 `X-Session-ID` 提交消息。
- `POST /api/cancel` 停止当前网页会话中的活动搜索。
- 前端和 API 同源；服务端接受同一 Host 的 HTTP 或 HTTPS Origin。
- UI 使用 `textContent` 渲染模型和房源文本，来源链接仅允许 HTTP(S)。
- 浏览器只提交当前会话已返回的房源标识，服务端从可信缓存恢复卡片数据。

当前网页会话保存在服务进程内，刷新页面会创建新会话。历史会话恢复、生产认证和多实例共享
需要在正式部署前实现。

## 文件结构

- `public/index.html`：页面结构。
- `public/style.css`：响应式视觉样式。
- `public/app.js`：对话、需求确认、搜索状态和推荐卡片。
- `public/shortcuts.js`：通勤、地铁和学校条件输入。
- `public/mrt-stations.json`：带来源日期的地铁站点静态快照。
- `server.py`：静态服务和现有编排器之间的适配层。
- `../tests/test_web.py`：会话、确认、取消、选择校验和幂等行为测试。

## 验证

```bash
node --check web/public/app.js
node --check web/public/shortcuts.js
.venv/bin/python -m unittest tests.test_web tests.test_orchestration -q
```

真实来源检查会访问 DeepSeek、PropertyGuru 和 OneMap，应与离线回归测试分开执行。
