---
name: octer-hermes
version: 1.0.0
description: "Octer Hermes — Hermes Agent platform plugin that bridges Octer.ai cloud over WebSocket. Use when the user mentions octer, octer-hermes, Octer.ai, or asks to install/configure/connect Hermes to the Octer cloud."
metadata:
  hermes:
    tags: [platform, plugin, octer, websocket, install, gateway]
    related_skills: []
  requires:
    bins: ["python3", "pip"]
---

# Octer Hermes —— 安装 / 配置 / 排错

> **重要 — Agent 行为约束**：在执行任何 octer 相关操作前，必须先检查 `OCTER_API_KEY` 是否已配置：
> ```bash
> grep -E '^OCTER_API_KEY=' ~/.hermes/.env 2>/dev/null || echo "MISSING"
> ```
> 若输出 `MISSING` 或值为空，**停下来**引导用户走"配置 API Key"流程。**绝不**在缺少有效 `evo_` 开头的 API Key 时尝试启动 / 重启网关，否则连接会立刻失败。

## 这是什么

`octer-hermes` 是一个 Hermes Agent 平台插件（Hermes plugin path），把 Octer.ai 云端发来的 `tool_request` 通过 WebSocket 桥接到本地 Hermes Agent，再把 Agent 的回复以 `tool_response` 发回云端。等价于 Node.js 版本 [`octer-channel`](https://github.com/smallke/octer-channel) 的 Python/Hermes 实现。

```
Octer.ai 云端  ←──WebSocket──→  octer-hermes 插件  ←──in-process──→  Hermes Agent
                (tool_request)                       (_message_handler)
                (tool_response)
```

## 安装流程

按以下 4 步执行，**每步必须验证完成再进入下一步**。源码位于 `/home/ubuntu/users/zq/octer-hermes/`（或用户指定的路径）。

### 1. 链接到 Hermes 插件目录

```bash
ln -sfn /home/ubuntu/users/zq/octer-hermes ~/.hermes/plugins/platforms/octer
```

验证：
```bash
ls -la ~/.hermes/plugins/platforms/octer/plugin.yaml
# 应显示文件存在
```

### 2. 安装 Python 依赖

```bash
pip install -r ~/.hermes/plugins/platforms/octer/requirements.txt
```

或直接：
```bash
pip install "websockets>=12.0"
```

验证：
```bash
python3 -c "import websockets; print(websockets.__version__)"
```

### 3. 配置 API Key

**首选 —— 交互式向导：**
```bash
hermes gateway setup
# 在平台列表里选择 "Octer"，按提示输入 evo_ 开头的 API Key
```

**或直接写 `~/.hermes/.env`：**
```bash
cat >> ~/.hermes/.env <<'EOF'
OCTER_API_KEY=evo_PASTE_YOUR_KEY_HERE
OCTER_ALLOWED_USERS=octer-cloud
EOF
```

**注意事项**：
- API Key 必须以 `evo_` 开头，否则插件 `connect()` 时会以 `invalid_apikey` 致命错误退出
- 用户没有 Key 时，引导他们去 [octer.ai/workspace](https://octer.ai/workspace) → **Me** → **Settings** → **API Keys** → **Create Key**
- **不要替用户编造 / 假设 Key**

### 4. 重启网关并验证

```bash
hermes gateway restart
hermes gateway status
```

期望输出包含：
```
🌐 Octer: configured, running
```

实时确认 WebSocket 已接通：
```bash
tail -n 50 ~/.hermes/logs/gateway.log | grep '\[octer'
# 应看到:
# [octer-channel][default] connected to Octer.ai
# [octer] adapter connected (account=default)
```

## 端到端验证

让用户在 Octer.ai 网页或工作区触发一次请求，然后：
```bash
tail -f ~/.hermes/logs/gateway.log | grep '\[octer'
```

应看到一对完整的请求 / 响应日志：
```
[octer] tool_request id=req_xxx tool=hermes_agent query_len=42
[octer] tool_response id=req_xxx success=True len=128
```

且用户在 Octer.ai 端能收到 Agent 的回复。

## 配置项参考

| 环境变量 | 必填 | 说明 |
|----------|:----:|------|
| `OCTER_API_KEY` | ✅ | Octer.ai API Key（`evo_...`） |
| `OCTER_ALLOWED_USERS` | 二选一 | 推荐设为 `octer-cloud`（所有 Octer 云端请求使用此固定 user_id） |
| `OCTER_ALLOW_ALL_USERS` | 二选一 | 设为 `true` 跳过白名单，接受所有 Octer 请求 |

> ⚠️ **必须配其一**，否则 hermes 会以"Unauthorized user"为由拒绝所有 octer 请求（看日志会发现 response 永远是 ~135 字符的固定拒绝文案）。

或写入 `~/.hermes/config.yaml`：
```yaml
gateway:
  platforms:
    octer:
      enabled: true
      extra:
        api_key: evo_xxx
        account_id: default
```

## 故障排查

| 现象 | 根因 / 处理 |
|------|------------|
| `hermes gateway status` 不显示 octer | 插件目录未链接到 `~/.hermes/plugins/platforms/octer/`；检查 `plugin.yaml` 是否存在 |
| 启动时 `OCTER_API_KEY required` | 走"配置 API Key"流程，再 `hermes gateway restart` |
| 启动时 `OCTER_API_KEY must start with evo_` | Key 不完整或粘贴错误，回到 [octer.ai/workspace](https://octer.ai/workspace) 重新复制 |
| 启动时 `missing dependency: pip install websockets` | `pip install "websockets>=12.0"` |
| WebSocket 反复断开 | 检查网络连通性（能否 `curl https://octer.ai`）；插件每 3 秒自动重连 |
| 收到了 `tool_request` 但没回复 | 看 `~/.hermes/logs/gateway.log` 中 `[octer] handler error` 堆栈；多半是本地 Agent / 模型配置问题 |
| 重连后请求被去重过滤 | 正常行为；同一 `request_id` 在 60s 内只处理一次 |

## 关键文件

```
~/.hermes/plugins/platforms/octer/
├── plugin.yaml          # Hermes 插件清单
├── __init__.py          # exports register
├── adapter.py           # OcterAdapter + register()
├── client.py            # WebSocket 连接、心跳、重连
├── dedup.py             # 请求去重 (60s TTL)
├── requirements.txt     # websockets>=12.0
├── .env_example
├── LICENSE              # MIT
├── README.md
└── SKILL.md             # 本文件
```

## 协议规格

- **WebSocket**：`wss://octer.ai/ws/bridge?api_key=<key>`
- **心跳间隔**：30 秒（客户端发 `{type:"ping"}`）
- **重连间隔**：3 秒（无退避）
- **单条 `tool_response` 上限**：50000 字节，超出按 utf-8 边界截断
- **请求去重**：`request_id` 60 秒 TTL，1000 条上限

## 与 OpenClaw 版本的差异

| 维度 | `octer-channel` (OpenClaw) | `octer-hermes` (本插件) |
|------|---------------------------|-----------------------|
| 语言 | Node.js (ESM) | Python 3.11+ |
| 注册 | `api.registerChannel({ plugin })` | `ctx.register_platform(...)` |
| 入站派发 | `channelRuntime.dispatchReplyFromConfig` | `BasePlatformAdapter._message_handler` |
| Standalone | `npm start` | **不支持**（必须在 Hermes Gateway 内运行） |
| 配置 | `openclaw config set ...` | `~/.hermes/.env` 或 `config.yaml` |

> 用户如果搞混了两个版本：OpenClaw 用户用 `octer-channel`（Node.js），Hermes 用户用 `octer-hermes`（本插件）。
