<div align="center">

# WeChat MCP Server

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![MCP Compatible](https://img.shields.io/badge/MCP-compatible-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

中文 | [English](../README.md)

</div>

一个基于 macOS 无障碍 API 和屏幕截图的 MCP 服务器，可自动化操作微信。它让 LLM 能够以编程方式与微信聊天进行交互。

## 功能特性

- 📨 获取任何聊天（联系人或群组）的最近消息
- ✍️ 发送回复，或只写入草稿不发送
- 🛡️ 运行模式（`active` / `draft_only` / `paused` / `takeover`）、会话白名单、发送频率限制、重复消息拦截
- ✅ 发送后回读验证：以会话中真实出现的新消息为准，而不是假设按键成功
- 🧾 所有写操作记录 JSONL 审计日志
- 📷 发布纯文字朋友圈消息，并可设置仅创建草稿
- 👥 通过微信号添加联系人并配置隐私选项
- 🔍 智能聊天搜索，支持精确名称匹配
- 🤖 5 个专门为微信自动化设计的 Claude Code 子代理

## 快速开始

### 安装

```bash
pip install wechat-mcp-server
```

### 在 Claude Code 中配置

```bash
# 如果通过 pip 安装
claude mcp add --transport stdio wechat-mcp -- wechat-mcp

# 如果使用 uv 进行开发
claude mcp add --transport stdio wechat-mcp -- uv --directory $(pwd) run wechat-mcp
```

<details>
<summary>在 Claude Desktop 中配置</summary>

```json
// 如果通过 pip 安装
{
  "mcpServers": {
    "wechat-mcp": {
      "type": "stdio",
      "command": "wechat-mcp"
    }
  }
}

// 如果使用 uv 进行开发
{
  "mcpServers": {
    "wechat-mcp": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "--directory",
        "{path/to/wechat-mcp}",
        "run",
        "wechat-mcp"
      ],
    }
  }
}
```

</details>

<details>
<summary>在 Codex 中配置</summary>

```bash
# 如果通过 pip 安装
codex mcp add wechat-mcp -- wechat-mcp

# 如果使用 uv 进行开发
codex mcp add wechat-mcp -- uv --directory $(pwd) run wechat-mcp
```

</details>

### macOS 权限设置

⚠️ **重要**：为终端授予无障碍权限：

1. 打开**系统设置 → 隐私与安全性 → 辅助功能**
2. 添加你的终端应用程序（Terminal.app、iTerm2 等）
3. 使用服务器前确保微信正在运行

## 使用方法

### 基本命令

```bash
# 使用默认的 stdio 传输方式运行
wechat-mcp --transport stdio

# 使用 HTTP 传输方式运行
wechat-mcp --transport streamable-http

# 使用 SSE 传输方式运行
wechat-mcp --transport sse

# 暂停/恢复正在运行的 server（没有 server 时写入配置，下次启动生效）
wechat-mcp --set-mode paused
wechat-mcp --set-mode active

# 关闭正在运行的 server
wechat-mcp --stop
```

### 可用的 MCP 工具

| 工具 | 作用 |
| --- | --- |
| `get_wechat_status` | 当前模式、微信是否运行、当前会话、白名单、近一小时发送数 |
| `set_wechat_mode` | 切换 `active` / `draft_only` / `paused` / `takeover` |
| `list_wechat_chats` | 会话列表中可见的聊天名称 |
| `fetch_messages_by_chat` | 读取最近消息；`sender_names=True` 返回群聊发送者昵称 |
| `fetch_messages_since` | 按日期收集某会话之后的全部消息（带时间） |
| `draft_reply_to_chat` | 只把回复写入输入框，**不发送** |
| `reply_to_messages_by_chat` | 发送回复，受策略层约束 |
| `add_contact_by_wechat_id` | 通过微信号添加联系人 |
| `publish_moment_without_media` | 发布纯文字朋友圈（`publish=False` 仅草稿） |

完整的工具规格请查看[详细 API 文档](detailed-guide.md)。

## 运行模式与安全边界

发送不是模型说发就发：`reply_to_messages_by_chat` 在策略不允许时返回
`{"sent": false, "blocked_reason": ...}`，不会重试，也不会勉强发送。

| 模式 | 读取 | 草稿 | 发送 |
| --- | --- | --- | --- |
| `active`（默认） | ✅ | ✅ | ✅ |
| `draft_only` | ✅ | ✅ | ❌ |
| `paused` | ✅ | ❌ | ❌ |
| `takeover` | ❌ | ❌ | ❌（微信完全交给人工） |

其他保护：

- **白名单**：`WECHAT_MCP_ALLOWED_CHATS` 限制可写入的会话，留空表示不限制。
- **频率限制**：回复默认每小时 30 条；加好友默认每分钟 1 个、每天 100 个，配置在 `artifacts/config/policy.toml`。
- **回复节奏**：同文件的 `[reply_pacing]` 控制"思考 1.0–2.8 秒 + 7 字/秒打字 + 0.25–0.9 秒操作间隔"。
- **重复拦截**：与上一条自己发出的内容完全相同时不重复发送。
- **草稿保护**：输入框已有他人正在输入的内容时拒绝覆盖。
- **发送验证**：发送后回读会话，`verified: false` 表示"已发送但未能确认"。
- **审计**：所有动作追加到 `<state_dir>/audit.jsonl`，默认只记录文本哈希。
- **其他写操作**：`add_contact_by_wechat_id`、`publish_moment_without_media` 在 `paused` / `takeover` 模式下同样会被拒绝。
- **人工操作优先**：监听键盘/鼠标事件（只监听，不记录按键内容），一旦有人操作就停止读取、草稿和发送；机器空闲 `WECHAT_MCP_HUMAN_IDLE_SECONDS`（默认 30 秒）后自动恢复。程序自己合成的按键/点击会按来源 PID 排除，不会误触发。
- **焦点是借用而不是抢占**：调用结束后会把焦点还给原来的应用；读取已打开的会话时不会激活微信。

### 配置项

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `WECHAT_MCP_MODE` | `active` | 运行模式 |
| `WECHAT_MCP_ALLOWED_CHATS` | 空（全部） | 逗号分隔的会话白名单 |
| `WECHAT_MCP_MAX_SENDS_PER_HOUR` | `30` | 每小时发送上限 |
| `WECHAT_MCP_MAX_REPLY_CHARS` | `2000` | 单条回复最大长度 |
| `WECHAT_MCP_VERIFY_SENDS` | `1` | 发送后回读验证 |
| `WECHAT_MCP_ALLOW_DUPLICATE` | `0` | 允许连续发送相同内容 |
| `WECHAT_MCP_AUDIT_TEXT` | `0` | 审计日志记录明文正文 |
| `WECHAT_MCP_STATE_DIR` | `~/.local/state/wechat-mcp` | 状态与审计目录 |
| `WECHAT_MCP_HUMANIZE` | `1` | 是否模拟人工节奏（操作间隔、等待界面稳定） |
| `WECHAT_MCP_RESTORE_FOCUS` | `1` | 调用结束后把焦点还给原来的应用 |
| `WECHAT_MCP_RESPECT_HUMAN` | `1` | 检测到人工键鼠操作时暂停 |
| `WECHAT_MCP_HUMAN_IDLE_SECONDS` | `30` | 空闲多久后自动恢复 |
| `WECHAT_MCP_MIN_DELAY` | `0.25` | 两次操作之间的最短间隔（秒） |
| `WECHAT_MCP_MAX_DELAY` | `0.90` | 两次操作之间的最长间隔（秒） |

运行中的 server 会在 `<state_dir>/control.sock` 上监听（仅本人可读写），
因此 `wechat-mcp --set-mode ...` 可以立即切换模式，不需要重启 MCP 会话。

保守的线上配置示例：

```bash
export WECHAT_MCP_MODE=draft_only
export WECHAT_MCP_ALLOWED_CHATS="工作群,客户支持群"
export WECHAT_MCP_MAX_SENDS_PER_HOUR=10
```

## 架构

```
src/wechat_mcp/
  mcp_server.py           # MCP 工具层（薄）：解析参数、调用 service、转换错误
  services.py             # 用例编排：fetch / draft / send，串起 policy、state、audit
  policy.py               # 纯逻辑决策：模式、白名单、频率、重复
  state.py                # 持久化模式、草稿、发送历史（原子写入 JSON）
  audit.py                # 追加式 JSONL 审计日志
  config.py               # 环境变量配置
  composer.py             # 输入框写入与发送、焦点复查、发送后回读
  messages.py             # 消息读取、滚动、发送者归属
  wechat_accessibility.py # 微信专属 AX 导航（窗口、会话、搜索）
  ax.py / ui.py           # 通用 AX 原语 / 合成输入事件
  contacts.py             # 添加联系人流程
  moments.py              # 朋友圈流程
```

`ax.py` / `ui.py` 不含任何微信知识；`wechat_accessibility.py` 是唯一编码微信
AX 标识符的模块。所有会按键或点击的路径都会先确认微信在前台——合成按键只会
落到当前焦点窗口，不做这个检查就可能把内容打进别的应用。

## 获客流程（Outreach）

按手机号加好友、按渠道打招呼、通过后跟进，并维护一张线索表。

```bash
cp config/outreach.example.toml config/outreach.toml
```

产物统一放在 `artifacts/outreach/`：

| 文件 | 用途 |
| --- | --- |
| `outreach.toml` | 渠道话术（直接编辑） |
| `leads.csv` | 线索表本身（唯一数据源），可用 Excel 打开，已 gitignore |
| `config/policy.toml` | 频率限制：每小时消息数、每分钟/每天加好友数 |

阶段流转：
`new → request_sent → accepted → greeted → replied`（另有 `failed` / `skipped`）。

工具：`import_leads`、`list_leads`、`send_friend_request`、
`send_lead_followup`、`mark_lead_stage`。

**默认 dry-run**：只返回 `would_send` 预览、不改状态；确认话术后再设
`WECHAT_MCP_OUTREACH_DRY_RUN=0` 真发。好友是否通过目前由人工确认后调用
`mark_lead_stage` 记录。

## Claude Code 子代理

本项目包含 5 个专门为微信自动化设计的智能子代理。它们通过 Claude Code 实现对微信的自然语言控制。

### 可用的子代理

1. **聊天记录总结器** - 总结聊天历史并提取关键信息
2. **自动回复器** - 自动生成并发送合适的回复
3. **消息搜索器** - 在聊天历史中搜索特定内容
4. **多聊天监控器** - 监控多个聊天并优先处理消息
5. **聊天洞察分析器** - 分析关系动态和沟通模式

📖 [查看完整的子代理指南](../.claude/agents/README.md)

## 开发

### 使用 uv 进行本地设置

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 克隆并设置
git clone https://github.com/yourusername/WeChat-MCP.git
cd WeChat-MCP
uv sync

# 本地运行
uv run wechat-mcp --transport stdio

# 单元测试（不需要微信）
uv run pytest

# 真机冒烟测试（需显式开启）
WECHAT_MCP_LIVE=1 uv run pytest tests/test_live.py -m live -s
```

## 文档

- 📘 [详细指南](detailed-guide.md) - 完整的 API 文档和架构说明
- 🤖 [子代理指南](../.claude/agents/README.md) - 如何使用 Claude Code 子代理

## 系统要求

- macOS（使用无障碍 API）
- 已安装并运行微信 Mac 版（⚠️ 微信 4.1.x 部分版本不再暴露 UI 树，`get_wechat_status` 会返回 `ax_tree_ok: false`；此时需要 3.8.x 或键盘/截图方案）
- Python 3.12+
- 终端的无障碍权限

## 贡献

欢迎贡献！请随时提交 Pull Request。

## 许可证

MIT License - 详见 [LICENSE](../LICENSE) 文件

## 本地数据

运行数据（状态、审计、线索、岗位导出、个人配置）都在 `artifacts/`，属于隐私目录，不随仓库推送。初始化步骤、目录结构和业务规范见 [Artifacts.md](../Artifacts.md)。
