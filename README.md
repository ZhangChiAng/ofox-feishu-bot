# Ofox 飞书机器人

监控 `https://api.ofox.ai/v1/models` 的模型目录，并通过飞书机器人返回图片报告。使用 `var/ofox.sqlite3` 保存模型快照与全局关注列表。每日主动检测新增模型，并在检测到时发送模型报告。

## 飞书应用

创建企业自建应用并启用机器人能力。事件与回调均选择“使用长连接接收”，订阅事件：

```text
application.bot.menu_v6
```

在回调配置中订阅：

```text
card.action.trigger
```

发布应用前确保具备发送消息和上传图片所需权限：

```text
im:message:send_as_bot
im:resource
```

配置以下两个顶级机器人菜单，均使用“推送事件”：

| 菜单 | event_key | 返回内容 |
| --- | --- | --- |
| 模型报告 | `send_report` | 摘要、新增模型、关注模型 |
| 关注管理 | `manage_watches` | 全局关注列表交互卡片 |

## 部署

需要 Python 3.12、`uv`、可访问 Ofox API 和飞书长连接服务、一个可显示中文的 TrueType/OpenType 字体文件。

```bash
uv sync --locked
cp .env.example .env
uv run --locked python -m app.worker
```

详细的从零部署指南见 [docs/deploy_from_scratch.md](docs/deploy_from_scratch.md)。

合并到 `main` 后，GitHub Actions 会在 CI 成功后通过 SSH 部署并重启
`ofox-feishu-bot.service`。仓库 Actions Secrets 需要配置：
`DEPLOY_HOST`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`、`DEPLOY_PATH`。
服务器部署目录中的 `.env` 和 `var/ofox.sqlite3` 保持本地保存，不由 CI/CD 生成或覆盖。

## 配置

`.env` 从 `.env.example` 复制，只保存在本机。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 是 | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 是 | 飞书应用 App Secret |
| `CHINESE_FONT_PATH` | 是 | 服务器上存在的中文字体文件路径 |
| `LOG_LEVEL` | 否 | `CRITICAL`/`ERROR`/`WARNING`/`INFO`/`DEBUG`，默认 `INFO` |
| `FEISHU_EVENT_MAX_AGE_SECONDS` | 否 | 菜单事件最大可处理年龄，正整数秒，默认 `120` |
| `DAILY_REPORT_TIME` | 否 | 每日检测时间，逗号分隔的 `HH:MM` 列表，默认 `09:30,14:00` |
| `DAILY_REPORT_TIMEZONE` | 否 | 每日检测时区，默认 `Asia/Shanghai` |
| `FEISHU_REPORT_RECEIVE_ID_TYPE` | 否 | 主动推送目标类型，例如 `chat_id` 或 `open_id` |
| `FEISHU_REPORT_RECEIVE_ID` | 否 | 主动推送目标 ID |

未配置 `FEISHU_REPORT_RECEIVE_ID_TYPE` 或 `FEISHU_REPORT_RECEIVE_ID` 时，worker 仍接收菜单和卡片事件，但不执行每日主动推送。

## 使用

点击“模型报告”或“关注管理”菜单后，会先收到“⌨️ 正在处理…”消息，再收到处理结果。机器人不接收文本消息。

关注列表通过“关注管理”维护。项目按单用户部署设计，不提供用户白名单；若允许多人访问，所有用户会共享同一关注列表。卡片支持逐项关注/取消、提供商与关键词组合筛选、分页和二次确认后清空；关键词忽略大小写并匹配模型名称、ID 与提供商。若模型目录暂不可用，已有关注仍可查看、取消或清空，添加功能会暂时禁用。每日发现新增模型时，图片报告后会附带分页快捷卡片，只提供逐项操作。

关注管理首页、添加模型页和新增模型快捷卡均可直接关闭。关闭会把原消息替换为不可操作的精简状态，不撤回消息，也不修改关注列表；需要时可从机器人菜单重新打开。交互卡片使用 Card JSON 2.0 的共享更新模式，并禁止转发及转发后的交互。图片报告不是交互卡片，不受关闭行为影响。

## License

MIT
