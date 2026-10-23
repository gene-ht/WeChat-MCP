# Artifacts 初始化与使用规范

`artifacts/` 是运行期私有数据的根目录。不同设备、不同使用者都按本文档初始化，
保证目录结构、文件格式和业务规则一致。

**`artifacts/` 整体被 `.gitignore` 忽略：不提交、不推送、不打包、不共享。**
仓库只保留 `config/*.example.*` 模板与代码。

## 1. 初始化

在仓库根目录执行：

```bash
mkdir -p artifacts/state artifacts/config artifacts/outreach artifacts/chat-history

cp config/policy.example.toml   artifacts/config/policy.toml
cp config/history.example.toml  artifacts/config/history.toml
cp config/outreach.example.toml artifacts/outreach/outreach.toml
cp config/leads.example.csv     artifacts/outreach/leads.csv

chmod 700 artifacts artifacts/state artifacts/config artifacts/outreach artifacts/chat-history
chmod 600 artifacts/config/*.toml artifacts/outreach/*
```

初始化后按需修改：

- `policy.toml`：消息/好友限额、回复节奏；
- `history.toml`：长历史导出参数；
- `outreach.toml`：公司、渠道话术；
- `leads.csv`：留表头即可，运行时会自动写入。

## 2. 目录与职责

```
artifacts/
  state/                    # 运行时状态，可重建
    state.json              # 模式、发送计数、草稿、好友申请计数
    audit.jsonl             # 操作审计，只追加
  config/
    policy.toml             # 限额 + 回复节奏
    history.toml            # 长历史导出
  outreach/
    outreach.toml           # 渠道话术（个人副本）
    leads.csv               # 线索表（CSV 即数据库）
  chat-history/
    <会话名>/
      YYYY-MM-DD.json       # 采集基准（唯一事实来源）
      YYYY-MM-DD.txt        # 由 JSON 渲染的文档
```

### 文件格式约定

- **时间**：ISO 8601，带 `+08:00`（如 `2026-10-23T14:49+08:00`）；
- **JSON**：UTF-8，2 空格缩进，原子写入（临时文件 + rename）；
- **CSV**：UTF-8，首行固定表头，字段顺序不可变；
- **消息与卡片**：一律时间正序，早的在前。

`leads.csv` 表头：

```
phone,channel,name,wechat_name,user_state,stage,greeting_sent,accepted,followup_sent,replied,note,created_at,updated_at
```

`chat-history/<会话名>/YYYY-MM-DD.json`：

```json
{
  "chat": "会话名",
  "date": "2026-10-23",
  "collected_at": "2026-10-23T17:03:43+08:00",
  "complete": true,
  "boundary": {"type": "watermark", "values": ["…", "…", "…"]},
  "watermark": ["…", "…", "…"],
  "messages": [{"sender": "OTHER", "text": "…", "at": "2026-10-23T14:49+08:00"}],
  "cards": [{"preview": "Chat History…", "title": "群聊的聊天记录", "texts": ["…"]}]
}
```

## 3. 统一业务规范

### 3.1 消息与好友限额（`policy.toml`）

- 回复：默认 30 条/小时，0 表示不限；
- 好友申请：默认 1 个/分钟、100 个/天；
- 限额是本地保护阈值，不是微信官方规则，按账号情况调整；
- 修文件后重启 MCP server 生效。

### 3.2 回复节奏（`policy.toml` 的 `[reply_pacing]`）

- 思考 1.0–2.8 秒；
- 打字 7 字/秒（0.8–8 秒封顶）；
- 操作间隔 0.25–0.9 秒；
- 键鼠有人操作时一律暂停，空闲 30 秒后继续。

### 3.3 长历史导出（`history.toml`）

- 每次执行都是一次完整的单向采集：从最新往上，不回头、不重跑；
- 停止条件：早于目标日期的分隔符，或已存 `watermark` 的最近 3 条**连续全匹配**；
- 合并转发每次都完整收集：打开 → 等 3 秒 → 固定步长下滚 → 连续 5 步无新增即停 → 关闭；
- 单次页数由 `max_pages` 限制，到达上限保留已采集部分。

命令：

```bash
uv run python -m wechat_mcp.history_export "会话名" --days 1
```

### 3.4 岗位文档（`chat-history/`）

- 采集只写 JSON；txt 必须由 JSON 渲染（`render_document`），不从对话上下文拼；
- 文档按区域分组、原文收录；
- 同一会话同一天只有一个 JSON，重复采集增量合并、按 `(sender, text)` 去重。

### 3.5 获客线索（`outreach/`）

- 阶段流转：`new → request_sent → accepted → greeted → replied`；
- 发送好友申请后写 `request_sent`，通过后 `accepted`，跟进后 `greeted`，回复后 `replied`；
- CSV 按 mtime 自动重载，可直接编辑；写入用原子替换；
- 默认 dry-run，只有显式关闭才真实发送。

### 3.6 状态与审计（`state/`）

- `state.json` 记录模式、计数、草稿；删除后会重建，不影响 `chat-history`；
- `audit.jsonl` 只追加，用于回看操作，不建议手工改写。

## 4. 隐私与权限

- 目录 700、文件 600；
- 不提交、不打包、不同步到公开位置；
- 备份自行加密，恢复时保持相同相对路径；
- 文档、截图、日志中出现的手机号/昵称需打码。

## 5. 跨设备迁移

1. 新设备执行第 1 节初始化；
2. 只迁移加密后的 `artifacts/` 子目录（至少 `outreach/`、`chat-history/`、`config/`）；
3. 不迁移 `state/`，让新设备重新生成计数与模式；
4. 迁移后执行第 6 节验收。

## 6. 验收清单

```bash
# 1) 目录存在
ls artifacts/{state,config,outreach,chat-history}

# 2) 忽略规则生效（应输出 .gitignore 命中）
git check-ignore -v artifacts/state/state.json
git check-ignore -v artifacts/chat-history/demo/2026-01-01.json

# 3) 没有 artifacts 被跟踪
git ls-files artifacts | wc -l    # 期望 0

# 4) 配置可加载
uv run python -c "import tomllib;tomllib.load(open('artifacts/config/policy.toml','rb'));print('ok')"

# 5) 线索表头正确
head -1 artifacts/outreach/leads.csv
```
