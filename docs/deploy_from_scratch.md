# Ofox 飞书机器人从零复现指南

本文档说明如何从零创建飞书企业自建应用，并部署一个 Ofox.ai 模型报告机器人。

## 目标架构

```text
飞书企业自建应用
  -> 机器人能力
  -> 长连接事件订阅
  -> python -m app.worker
  -> Ofox models API
  -> SQLite 本地快照
```

## 占位符

```text
<PROJECT_DIR>       项目部署目录
<RUN_USER>          运行 worker 的 Linux 用户
<REPO_URL>          项目代码仓库地址
<FEISHU_APP_ID>     飞书应用 App ID
<FEISHU_APP_SECRET> 飞书应用 App Secret
<CHINESE_FONT_PATH>  服务器上可显示中文的字体文件路径
<REPORT_RECEIVE_ID_TYPE> 主动推送接收目标类型，推荐 open_id
<REPORT_RECEIVE_ID>      主动推送接收目标 ID
```

## 前置条件

- Linux + systemd
- Python 3.12 或更高版本
- `uv`
- 可访问飞书开放平台长连接服务
- 可访问 `https://api.ofox.ai/v1/models`
- 一个飞书企业自建应用及其管理员权限
- 一个可显示中文的 TrueType/OpenType 字体文件

## 创建飞书应用

1. 在飞书开放平台创建企业自建应用。
2. 在“凭证与基础信息”中记录 App ID 和 App Secret。
3. 在“添加应用能力”中启用机器人。
4. 在“权限管理”中添加并发布所需权限。

所需权限：

```text
im:message.p2p_msg:readonly
im:message:send_as_bot
im:resource
```

## 配置事件订阅

进入“事件与回调”：

1. 订阅方式选择“使用长连接接收事件”。
2. 订阅事件：

```text
im.message.receive_v1
application.bot.menu_v6
```

## 配置机器人菜单

机器人菜单使用“推送事件”。

```text
菜单名：帮助
响应动作：推送事件
event_key：help

菜单名：可用提供商
响应动作：推送事件
event_key：list_providers

菜单名：模型报告
响应动作：推送事件
event_key：send_report
```

完成权限、事件和菜单配置后，在“版本管理与发布”中发布应用。

## 部署代码

```bash
sudo install -d -o "<RUN_USER>" -g "<RUN_USER>" "<PROJECT_DIR>"
git clone "<REPO_URL>" "<PROJECT_DIR>"
cd "<PROJECT_DIR>"
uv sync --locked
```

如果代码已经放在目标目录，只需要进入目录后执行：

```bash
uv sync --locked
```

## 配置环境变量

```bash
cd "<PROJECT_DIR>"
umask 077
cp .env.example .env
```

编辑 `.env`，填入自己的飞书应用凭证：

```dotenv
FEISHU_APP_ID=<FEISHU_APP_ID>
FEISHU_APP_SECRET=<FEISHU_APP_SECRET>
CHINESE_FONT_PATH=<CHINESE_FONT_PATH>
LOG_LEVEL=INFO
FEISHU_MESSAGE_MAX_AGE_SECONDS=120
DAILY_REPORT_TIME=12:30
DAILY_REPORT_TIMEZONE=Asia/Shanghai
FEISHU_REPORT_RECEIVE_ID_TYPE=<REPORT_RECEIVE_ID_TYPE>
FEISHU_REPORT_RECEIVE_ID=<REPORT_RECEIVE_ID>
```

`CHINESE_FONT_PATH` 必须指向服务器上存在的字体文件。报告会在服务端渲染成 PNG，
建议使用可覆盖中文字符的字体。

`.env` 只能保存在服务器本地，不要提交到代码仓库。

`FEISHU_MESSAGE_MAX_AGE_SECONDS` 默认 `120`。
`DAILY_REPORT_TIME` 使用 24 小时制 `HH:MM`，默认 `12:30`。
`DAILY_REPORT_TIMEZONE` 默认 `Asia/Shanghai`。
未配置 `FEISHU_REPORT_RECEIVE_ID_TYPE` 或 `FEISHU_REPORT_RECEIVE_ID` 时，
长连接 worker 仍正常启动，但会跳过每日主动推送。
每日检测只在发现新增模型时推送模型报告图片，并随后发送关注命令提示文本。

## 手动验证

先确认模块可以导入：

```bash
uv run python -m compileall app
```

验证 Ofox API：

```bash
uv run python - <<'PY'
from app.config import load_config
from app.ofox_client import OfoxClient

config = load_config()
models = OfoxClient(config.ofox_models_api_url).fetch_models()
print(f"models={len(models)}")
print(models[0].id if models else "no models")
PY
```

启动长连接 worker：

```bash
uv run python -m app.worker
```

预期日志包含：

```text
Starting Feishu websocket worker
```

保持进程运行，在飞书中给机器人发送：

```text
provider deepseek
watch add DeepSeek V4 Flash
watch list
```

预期：

- `provider openai` 返回飞书图片消息，图片内包含提供商摘要、模型表格和价格列。
- `watch add <模型名称>` 添加一个当前 catalog 中存在的模型名称到全局关注列表。
- `watch list` 返回飞书图片消息，图片内包含关注模型表格。

点击机器人菜单“帮助”“可用提供商”“模型报告”，均应收到机器人回复。

预期：

- “帮助”返回短文本可用命令。
- “可用提供商”返回飞书图片，包含提供商/模型数表格和查询示例。
- “模型报告”返回飞书图片，包含摘要表、新增模型表和关注模型表。
- 首次运行会创建 SQLite 基线，摘要表状态列显示"首次运行，已建立本地模型基线"，新增模型行显示"首次运行"。

## systemd 示例

systemd 只需要常驻运行 WebSocket worker。当前项目不需要 Nginx，也不需要公网 HTTP 回调地址。

```ini
[Unit]
Description=Ofox Feishu Bot WebSocket Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<RUN_USER>
WorkingDirectory=<PROJECT_DIR>
Environment=PYTHONUNBUFFERED=1
ExecStart=<PROJECT_DIR>/.venv/bin/python -m app.worker
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

保存为 `/etc/systemd/system/ofox-feishu-bot.service` 后执行：

```bash
sudo systemd-analyze verify /etc/systemd/system/ofox-feishu-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now ofox-feishu-bot.service
sudo systemctl status ofox-feishu-bot.service -l --no-pager
```

查看日志：

```bash
sudo journalctl -u ofox-feishu-bot.service -f
```

## 验收命令

```bash
uv run pytest
uv run ruff check app
uv run ruff format --check app
uv run python -m compileall app
git status --short --ignored
```

确认 `.env`、`var/ofox.sqlite3`、`.venv/`、缓存和日志文件处于 ignored 状态。
