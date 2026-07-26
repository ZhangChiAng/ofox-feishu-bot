# Ofox 飞书机器人

监控 `https://api.ofox.ai/v1/models` 的模型目录，并通过飞书机器人返回图片报告。使用 `var/ofox.sqlite3` 保存模型快照与全局关注列表。每日主动检测新增模型，并在检测到时发送模型报告。

## 飞书应用

创建企业自建应用并启用机器人能力。事件与回调均选择“使用长连接接收”，订阅事件：

```text
im.message.receive_v1
application.bot.menu_v6
```

在回调配置中订阅：

```text
card.action.trigger
```

发布应用前确保具备发送消息和上传图片所需权限。私聊使用至少需要：

```text
im:message.p2p_msg:readonly
im:message.reactions:write_only
im:message:send_as_bot
im:resource
```

配置以下三个顶级机器人菜单，均使用“推送事件”：

| 菜单 | event_key | 返回内容 |
| --- | --- | --- |
| 模型报告 | `send_report` | 摘要、新增模型、关注模型 |
| 可用提供商 | `list_providers` | 提供商模型数和查询示例 |
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
| `FEISHU_MESSAGE_MAX_AGE_SECONDS` | 否 | 私聊文本消息和菜单事件最大可处理年龄，正整数秒，默认 `120` |
| `DAILY_REPORT_TIME` | 否 | 每日检测时间，逗号分隔的 `HH:MM` 列表，默认 `09:30,14:00` |
| `DAILY_REPORT_TIMEZONE` | 否 | 每日检测时区，默认 `Asia/Shanghai` |
| `FEISHU_REPORT_RECEIVE_ID_TYPE` | 否 | 主动推送目标类型，例如 `chat_id` 或 `open_id` |
| `FEISHU_REPORT_RECEIVE_ID` | 否 | 主动推送目标 ID |

未配置 `FEISHU_REPORT_RECEIVE_ID_TYPE` 或 `FEISHU_REPORT_RECEIVE_ID` 时，worker 仍接收命令和菜单事件，但不执行每日主动推送。

## 使用

机器人收到有效的私聊文本消息后，会先在原消息上添加“敲键盘”表情作为回执，再单独发送处理结果。点击机器人菜单后，会先收到“⌨️ 正在处理…”消息，再收到处理结果。图片、文件和群聊消息不会触发处理。

文本命令：

```text
provider <提供商>
```

`provider <提供商>` 返回该提供商模型表，依次按输出、输入、缓存读取价格递增展示前 30 条；新增模型和关注模型使用相同排序。

关注列表通过机器人菜单“关注管理”维护，并由所有有权使用机器人的用户全局共享。卡片支持逐项关注/取消、提供商与关键词组合筛选、分页和二次确认后清空；关键词忽略大小写并匹配模型名称、ID 与提供商。若模型目录暂不可用，已有关注仍可查看、取消或清空，添加功能会暂时禁用。每日发现新增模型时，图片报告后会附带分页快捷卡片，只提供逐项操作。

## License

MIT
