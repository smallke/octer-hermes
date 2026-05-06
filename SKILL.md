---
name: octer-hermes
version: 1.0.0
description: "Install and configure the Octer.ai platform plugin for Hermes Agent. Use when the user says any of: 'install octer', 'install octer-hermes', 'connect Hermes to Octer.ai', 'set up octer plugin', 'install the skill from <octer URL>', or pastes an `evo_…` API key with octer context. The skill performs end-to-end install: fetch source → copy to plugin dir → check deps → write API key → enable → restart gateway → verify."
metadata:
  hermes:
    tags: [platform, plugin, octer, websocket, install, gateway]
    related_skills: []
  requires:
    bins: ["git", "python3"]
---

# Octer Hermes —— 自动安装 / 配置 / 排错

> **触发示例**（agent 看到这些应该走本 skill）：
> - "Please install the skill from https://…/octer…/SKILL.md and use API_KEY evo_xxx"
> - "帮我把 hermes 接到 octer.ai，key 是 evo_xxx"
> - "install octer-hermes"

## Agent 必读规则

1. **API Key 抽取**：从用户当前消息里抓取 `evo_[a-zA-Z0-9]+` 形式的串，作为 `OCTER_API_KEY`。**找不到就停下问用户**，绝不自己编。
2. **不依赖外部 SKILL URL**：用户给的 URL 只是触发词。安装步骤都在本 skill 内，不要去 fetch 那个 URL 来对照。
3. **源码位置**：见下方 §1。如果用户没说 git URL，按 §1 的默认值去 clone。
4. **先检查再动作**：每步执行前用 `grep` / `ls` / `python -c "import …"` 确认现状，避免重复操作。
5. **绝不在缺 API Key 时启动 gateway**——连接会立刻 `invalid_apikey` 失败。

## 这是什么

`octer-hermes` 是一个 Hermes Agent 平台插件（Hermes plugin path），把 Octer.ai 云端发来的 `tool_request` 通过 WebSocket 桥接到本地 Hermes Agent，再把 Agent 的回复以 `tool_response` 发回云端。等价于 Node.js 版本 [`octer-channel`](https://github.com/smallke/octer-channel) 的 Python/Hermes 实现。

```
Octer.ai 云端  ←──WebSocket──→  octer-hermes 插件  ←──in-process──→  Hermes Agent
                (tool_request)                       (_message_handler)
                (tool_response)
```

## 自动安装流程（5 步）

> Source repo: **`https://github.com/smallke/octer-hermes`** （写死，不要改）
>
> 假设用户已通过本 skill 的触发词激活（含 API Key）。从这里开始一气呵成执行 5 步，每步先 check 再做，全部成功才算完。

### 1. 获取源码（git clone 到 ~/.hermes/cache）

```bash
SRC=~/.hermes/cache/octer-hermes
mkdir -p "$(dirname "$SRC")"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" pull --ff-only --quiet
else
  git clone --depth 1 https://github.com/smallke/octer-hermes.git "$SRC"
fi
ls "$SRC/plugin.yaml" >/dev/null && echo "OK: source ready at $SRC"
```

失败处理：
- `git: command not found` → 让用户先装 git
- 网络 timeout / 403 → 让用户检查 GitHub 可访问性

### 2. 拷贝到 Hermes 插件目录

```bash
mkdir -p ~/.hermes/plugins/platforms
rm -rf ~/.hermes/plugins/platforms/octer
cp -r ~/.hermes/cache/octer-hermes ~/.hermes/plugins/platforms/octer
rm -rf ~/.hermes/plugins/platforms/octer/.git ~/.hermes/plugins/platforms/octer/__pycache__
ls ~/.hermes/plugins/platforms/octer/plugin.yaml >/dev/null && echo "OK: plugin installed"
```

> **必须用 `cp -r`，不要用 `ln -s`**：软链会把源码绝对路径写进 Hermes 的 manifest / 日志 / 错误堆栈，持续暴露用户文件结构。只有用户**明确**要"开发模式"才用软链，并告知风险。

### 3. 检查 Python 依赖

```bash
HERMES_PY=$(head -1 "$(which hermes)" | sed 's|^#!||')
"$HERMES_PY" -c "import websockets, sys; sys.exit(0 if websockets.__version__ >= '12' else 1)" \
  && echo "OK: websockets present" \
  || "$(dirname "$HERMES_PY")/pip" install "websockets>=12.0"
```

> Hermes ≥ v0.12 自带 `websockets 15.0.1`，绝大多数情况第一行就 OK 了。**绝不**用系统 `pip install websockets`——hermes 跑在自己 venv 里，看不到系统 pip 装的包。

### 4. 写入 API Key + 白名单

从用户当前消息里提取 `evo_[a-zA-Z0-9]+`（必须以 `evo_` 开头，长度≥30）作为 `<API_KEY>`。**找不到就停下来问**，绝不假造。

```bash
API_KEY="<API_KEY>"   # ← 替换成从用户消息里抓到的真实 Key
touch ~/.hermes/.env

# 幂等写入 OCTER_API_KEY
if grep -q '^OCTER_API_KEY=' ~/.hermes/.env; then
  sed -i "s|^OCTER_API_KEY=.*|OCTER_API_KEY=$API_KEY|" ~/.hermes/.env
else
  echo "OCTER_API_KEY=$API_KEY" >> ~/.hermes/.env
fi

# 幂等写入 OCTER_ALLOWED_USERS=octer-cloud（必备，否则 hermes 会拒绝所有请求）
if grep -q '^OCTER_ALLOWED_USERS=' ~/.hermes/.env; then
  sed -i 's|^OCTER_ALLOWED_USERS=.*|OCTER_ALLOWED_USERS=octer-cloud|' ~/.hermes/.env
else
  echo 'OCTER_ALLOWED_USERS=octer-cloud' >> ~/.hermes/.env
fi

# 注释掉 ALLOW_ALL_USERS（如有），优先走显式白名单
sed -i 's|^OCTER_ALLOW_ALL_USERS=true|# OCTER_ALLOW_ALL_USERS=true|' ~/.hermes/.env

grep '^OCTER_' ~/.hermes/.env | sed 's/=evo_.*/=evo_***REDACTED***/'
```

> **报告给用户的所有日志输出都必须把 evo_ 后面的内容打码**，不要明文回显 API Key。

用户没有 Key 时，引导去 [octer.ai/workspace](https://octer.ai/workspace) → Me → Settings → API Keys → Create Key。

### 5. Enable 插件 + 重启网关 + 验证

```bash
hermes plugins enable platforms/octer
```

```bash
# 重启网关（如果当前在跑就 --replace；如果没跑就直接 run）
if pgrep -f "hermes gateway run" >/dev/null 2>&1; then
  setsid -f bash -c 'hermes gateway run -v --accept-hooks --replace > /tmp/hermes-gateway.log 2>&1' &
else
  setsid -f bash -c 'hermes gateway run -v --accept-hooks > /tmp/hermes-gateway.log 2>&1' &
fi
sleep 6
grep -E "octer|connected|ERROR|Unauthorized" /tmp/hermes-gateway.log | head -10
```

期望日志包含全部三行：
```
INFO gateway.run: Connecting to octer...
INFO ...adapter: [octer-channel][default] connected to Octer.ai
INFO ...adapter: [octer] WebSocket ready
```

不能出现：
- `'ClientConnection' object has no attribute 'closed'` → 旧代码遗留，确认 §2 拷的是最新源码
- `OCTER_API_KEY must start with evo_` → §4 没抓到正确 Key
- `Unauthorized user: req_xxx` → §4 漏了 `OCTER_ALLOWED_USERS=octer-cloud`

全部通过即安装成功。让用户在 Octer.ai 端发一次请求最终确认 e2e 链路。

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
