# Ofox 飞书机器人

## 功能

- 从 `https://api.ofox.ai/v1/models` 拉取模型列表。
- 使用本地 SQLite 保存模型快照和检查记录。
- 通过飞书机器人菜单返回卡片化模型报告、Provider 分组表格和帮助。
- 通过飞书私聊文本命令查看指定 Provider 的卡片化模型表格。
- 首次运行建立本地基线，后续运行识别新增模型。

## 配置

复制示例配置并填入自己的飞书应用凭证：

```bash
cp .env.example .env
```

`.env` 只保存在本机，不提交到 GitHub。可用变量：

```dotenv
FEISHU_APP_ID=
FEISHU_APP_SECRET=
LOG_LEVEL=INFO
```

## 命令

机器人支持以下文本命令：

```text
provider <provider>
```

`provider <provider>` 返回飞书交互卡片，卡片内包含 Provider 摘要和模型 Markdown 表格。

机器人菜单包含以下入口：

| 菜单 | 推送事件 | 返回内容 |
| --- | --- | --- |
| 帮助 | `help` | 短文本帮助信息 |
| 返回可用提供商 | `list_providers` | 飞书卡片，展示 Provider/模型数表格 |
| 立即发送 | `send_report` | 飞书卡片，展示摘要、新增模型表格和 Provider Top 10 |

机器人菜单只需要配置以下推送事件：

```text
help
list_providers
send_report
```
