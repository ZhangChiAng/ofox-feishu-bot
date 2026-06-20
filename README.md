# Ofox 飞书机器人

## 功能

- 从 `https://api.ofox.ai/v1/models` 拉取模型列表。
- 使用本地 SQLite 保存模型快照。
- 通过飞书机器人菜单返回图片版模型报告、提供商分组表格和帮助。
- 通过飞书私聊文本命令查看指定提供商的图片版模型表格。
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
CHINESE_FONT_PATH=/path/to/chinese-capable-font.ttf
LOG_LEVEL=INFO
```

`CHINESE_FONT_PATH` 必须指向服务器上存在的字体文件，建议使用可显示中文的
TrueType/OpenType 字体。

## 命令

机器人支持以下文本命令：

```text
provider <提供商>
```

`provider <提供商>` 返回飞书图片消息，图片内包含提供商摘要和模型表格。

机器人菜单包含以下入口：

| 菜单 | 推送事件 | 返回内容 |
| --- | --- | --- |
| 帮助 | `help` | 短文本帮助信息 |
| 可用提供商 | `list_providers` | 飞书图片，展示提供商/模型数表格 |
| 模型报告 | `send_report` | 飞书图片，展示摘要、新增模型表格和提供商 Top 10 |

机器人菜单只需要配置以下推送事件：

```text
help
list_providers
send_report
```
