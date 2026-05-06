# Octer Hermes

Octer.ai 平台插件 —— 通过 WebSocket 把 [Octer.ai](https://octer.ai) 云端发起的 `tool_request` 桥接到本地 [Hermes Agent](https://github.com/nousresearch/hermes-agent)，并把 Agent 的回复以 `tool_response` 发回云端。

这是 [`octer-channel`](https://github.com/smallke/octer-channel)（OpenClaw 的 Node.js 版本）的 Hermes Python 实现。

## 架构

```
Octer.ai 云端  ←──WebSocket──→  octer-hermes 插件  ←──in-process──→  Hermes Agent
                (tool_request)                       (_message_handler)
                (tool_response)
```

每个 `tool_request` 包含一个唯一 `request_id` 和用户 `query`。插件把 `request_id` 同时当作 `chat_id` 和 `user_id` 构造 `MessageEvent`，触发 Hermes Agent 处理后将完整回复打包成 `tool_response` 发回云端。

## 安装

### 1. 软链接到 Hermes 插件目录

```bash
ln -sfn $(pwd) ~/.hermes/plugins/platforms/octer
```

> 或拷贝目录：`cp -r . ~/.hermes/plugins/platforms/octer`

### 2. 安装 Python 依赖

```bash
pip install -r requirements.txt
# 或在 hermes-agent 项目环境里：
# pip install websockets
```

### 3. 配置 API Key

**方式 A — 交互式向导（推荐）：**

```bash
hermes gateway setup
# 在平台列表中选 Octer
```

**方式 B — 编辑 `~/.hermes/.env`：**

```bash
echo "OCTER_API_KEY=evo_your_key_here" >> ~/.hermes/.env
```

**方式 C — `~/.hermes/config.yaml`：**

```yaml
gateway:
  platforms:
    octer:
      enabled: true
      extra:
        api_key: evo_your_key_here
        account_id: default
```

### 4. 获取 API Key

1. 访问 [octer.ai/workspace](https://octer.ai/workspace)
2. **Me** → **Settings** → **API Keys** → **Create Key**
3. 复制以 `evo_` 开头的密钥

### 5. 启动网关

```bash
hermes gateway restart
hermes gateway status   # 应看到 🌐 Octer: configured, running
```

## 配置项

| 环境变量 | 说明 |
|----------|------|
| `OCTER_API_KEY` | **必填**。Octer.ai API Key（`evo_...`） |
| `OCTER_ALLOWED_USERS` | **二选一**。推荐设为 `octer-cloud`（所有 Octer 云端请求使用此固定 user_id） |
| `OCTER_ALLOW_ALL_USERS` | **二选一**。设为 `true` 跳过白名单 |

> 不配以上任何一项，hermes 会以"Unauthorized user"拒绝所有 octer 请求。

## 验证端到端

1. 在 Octer.ai 工作区触发一次 tool_request
2. 检查日志：

   ```bash
   tail -f ~/.hermes/logs/gateway.log | grep octer
   ```

   应看到：

   ```
   [octer] tool_request id=req_xxx tool=hermes_agent query_len=42
   [octer] tool_response id=req_xxx success=True len=128
   ```

3. 在 Octer.ai 端收到 Agent 回复

## 故障排查

| 问题 | 解决方法 |
|------|----------|
| `OCTER_API_KEY required` | 设置环境变量或运行 `hermes gateway setup` |
| `OCTER_API_KEY must start with evo_` | 检查 API Key 是否完整复制 |
| `missing dependency: pip install websockets` | `pip install websockets` |
| WebSocket 反复断开 | 检查网络；插件每 3 秒自动重连 |
| 收到了 tool_request 但没回复 | 检查 `~/.hermes/logs/gateway.log` 中的 handler 错误堆栈 |
| `hermes gateway status` 不显示 octer | 确认目录在 `~/.hermes/plugins/platforms/octer/` 且有 `plugin.yaml` |

## 文件清单

```
octer-hermes/
├── plugin.yaml          # Hermes 插件清单
├── __init__.py          # exports register
├── adapter.py           # OcterAdapter + register()
├── client.py            # WebSocket 连接管理
├── dedup.py             # 请求去重 (60s TTL)
├── requirements.txt     # websockets>=12.0
├── .env_example
├── LICENSE              # MIT
└── README.md
```

## 与 OpenClaw 版本的差异

| 维度 | `octer-channel` (OpenClaw) | `octer-hermes` |
|------|---------------------------|----------------|
| 语言 | Node.js (ESM) | Python 3.11+ |
| 框架 | OpenClaw Plugin SDK | Hermes Plugin Path |
| 注册 | `api.registerChannel({ plugin })` | `ctx.register_platform(...)` |
| 入站 | `channelRuntime.dispatchReplyFromConfig` | `BasePlatformAdapter._message_handler` |
| Standalone | `npm start` | 不支持（必须在 Hermes Gateway 内运行） |

## 协议参考

- WebSocket URL: `wss://octer.ai/ws/bridge?api_key=<key>`
- 心跳间隔: 30 秒
- 重连间隔: 3 秒
- 单条 `tool_response` 上限: 50000 字节（超出截断）

详细协议见 [`octer-channel` 源码](https://github.com/smallke/octer-channel)。

## License

[MIT](LICENSE)
