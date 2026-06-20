# Ofox 飞书机器人

监控 `https://api.ofox.ai/v1/models` 的模型目录，并通过飞书机器人返回图片报告。使用 `var/ofox.sqlite3` 保存模型快照与全局关注列表。每日主动检测新增模型，并在检测到时发送模型报告。

## 飞书应用

创建企业自建应用并启用机器人能力。事件订阅选择“使用长连接接收事件”，订阅：

```text
im.message.receive_v1
application.bot.menu_v6
```

发布应用前确保具备发送消息和上传图片所需权限。私聊使用至少需要：

```text
im:message.p2p_msg:readonly
im:message:send_as_bot
im:resource
```

机器人菜单使用“推送事件”：

| 菜单 | event_key | 返回内容 |
| --- | --- | --- |
| 模型报告 | `send_report` | 摘要、新增模型、关注模型 |
| 可用提供商 | `list_providers` | 提供商模型数和查询示例 |
| 帮助 | `help` | 文本命令列表 |

## 部署

需要 Python 3.12、`uv`、可访问 Ofox API 和飞书长连接服务、一个可显示中文的 TrueType/OpenType 字体文件。

```bash
uv sync --locked
cp .env.example .env
uv run --locked python -m app.worker
```

详细的从零部署指南见 [docs/deploy_from_scratch.md](docs/deploy_from_scratch.md)。

## 配置

`.env` 从 `.env.example` 复制，只保存在本机。

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `FEISHU_APP_ID` | 是 | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 是 | 飞书应用 App Secret |
| `CHINESE_FONT_PATH` | 是 | 服务器上存在的中文字体文件路径 |
| `LOG_LEVEL` | 否 | `CRITICAL`/`ERROR`/`WARNING`/`INFO`/`DEBUG`，默认 `INFO` |
| `FEISHU_MESSAGE_MAX_AGE_SECONDS` | 否 | 私聊文本消息最大可处理年龄，正整数秒，默认 `120` |
| `DAILY_REPORT_TIME` | 否 | 每日检测时间，`HH:MM`，默认 `12:30` |
| `DAILY_REPORT_TIMEZONE` | 否 | 每日检测时区，默认 `Asia/Shanghai` |
| `FEISHU_REPORT_RECEIVE_ID_TYPE` | 否 | 主动推送目标类型，例如 `chat_id` 或 `open_id` |
| `FEISHU_REPORT_RECEIVE_ID` | 否 | 主动推送目标 ID |

未配置 `FEISHU_REPORT_RECEIVE_ID_TYPE` 或 `FEISHU_REPORT_RECEIVE_ID` 时，worker 仍接收命令和菜单事件，但不执行每日主动推送。

## 使用

文本命令：

```text
provider <提供商>
watch add <模型名称>
watch remove <模型名称>
watch list
watch clear
```

`provider <提供商>` 返回该提供商模型表，按输出价格从低到高展示前 30 条。`watch add` 只接受当前 Ofox catalog 中存在的完整模型名称；关注列表是服务器全局列表。`watch list` 返回关注模型图片表，`watch clear` 清空全部关注项。

## License

MIT
