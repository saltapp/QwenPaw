# QwenPaw AiManus Channel 插件

让 ai-manus 后端通过 **WebSocket** 与 QwenPaw 平台进行双向实时通信。单条长连接承载所有事件、审批、推送消息，替代传统的 SSE + 轮询方式。

## 目录

- [前置条件](#前置条件)
- [快速安装](#快速安装)
- [验证安装](#验证安装)
- [ai-manus 后端配置](#ai-manus-后端配置)
- [消息协议](#消息协议)
- [架构说明](#架构说明)
- [配置参考](#配置参考)
- [故障排查](#故障排查)
- [开发指南](#开发指南)

## 前置条件

- QwenPaw 已部署运行（Docker 容器名 `qwenpaw`）
- QwenPaw 版本支持 `custom_channels` 机制（≥ 1.0）
- ai-manus 后端已部署运行

## 快速安装

### 步骤 1：部署插件到 QwenPaw

插件必须放在 QwenPaw 的 `custom_channels` 目录下。QwenPaw 启动时会自动扫描该目录，加载所有符合规范的 Channel 模块。

```bash
# 将插件目录复制到 QwenPaw 容器中
docker cp qwenpaw_plugin/ai_manus qwenpaw:/app/working/custom_channels/ai_manus

# 重启 QwenPaw 使插件生效
docker restart qwenpaw
```

> **说明**：QwenPaw 通过 [channel registry](qwenpaw_plugin/ai_manus/__init__.py) 自动发现 `custom_channels/` 下的包（含 `__init__.py` 的目录）或单文件模块（`.py`）。插件中的 `AiManusChannel` 类会被识别为其 `channel` 属性值 `"ai_manus"` 对应的 Channel。

### 步骤 2：启用 Channel

插件部署后还需要在 QwenPaw 配置中启用。编辑 QwenPaw 的 `/app/working/config.json`，在 `channels` 中添加：

```jsonc
{
  "channels": {
    // ... 其他 channel 配置 ...
    "ai_manus": {
      "enabled": true
    }
  }
}
```

完整操作命令：

```bash
# 进入容器编辑配置
docker exec -it qwenpaw vi /app/working/config.json
# 或者用 Python 一行脚本添加
docker exec qwenpaw python3 -c "
import json
with open('/app/working/config.json') as f:
    cfg = json.load(f)
cfg.setdefault('channels', {})['ai_manus'] = {'enabled': True}
with open('/app/working/config.json', 'w') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
"

# 重启生效
docker restart qwenpaw
```

## 验证安装

执行以下命令确认插件加载成功：

```bash
# 1. 确认文件已部署
docker exec qwenpaw ls /app/working/custom_channels/ai_manus/

# 2. 查看启动日志，确认 channel 已注册
docker exec qwenpaw tail -50 /app/working/qwenpaw.log | grep -i "ai.manus"
# 预期输出：AiManusChannel started (WebSocket endpoint: /api/ai-manus/ws/{session_id})

# 3. 确认配置已启用
docker exec qwenpaw python3 -c "
import json
with open('/app/working/config.json') as f:
    cfg = json.load(f)
ch = cfg['channels'].get('ai_manus', {})
print('enabled:', ch.get('enabled', False))
"
```

## ai-manus 后端配置

在 ai-manus 项目的 `.env` 文件中配置以下变量：

```env
# ---- QwenPaw 连接 ----
# QwenPaw 的 HTTP API 地址（ai-manus 容器内访问 qwenpaw 容器）
QWENPAW_API_URL=http://qwenpaw:8088/api/console/chat

# WebSocket 端点路径（对应插件的 /api/ai-manus/ws/{session_id}）
QWENPAW_WS_PATH=/api/ai-manus/ws

# QwenPaw 中配置的 Agent ID
QWENPAW_AGENT_ID=default

# QwenPaw 认证 Token
QWENPAW_AUTH_TOKEN=

# ---- 后端自身 ----
# ai-manus 的公网可达 URL（QwenPaw 下载文件等场景需要回调）
BACKEND_URL=http://localhost:8000
```

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `QWENPAW_API_URL` | 是 | `http://127.0.0.1:8088/api/console/chat` | QwenPaw HTTP API 地址 |
| `QWENPAW_WS_PATH` | 是 | `/api/ai-manus/ws` | WebSocket 端点路径 |
| `QWENPAW_AGENT_ID` | 否 | `default` | Agent 标识 |
| `QWENPAW_AUTH_TOKEN` | 否 | 空 | 认证 Token |
| `BACKEND_URL` | 否 | `http://localhost:8000` | 后端公网地址 |

> 配置定义在 [config.py](../backend/app/core/config.py#L121-L128)

## 消息协议

插件与 ai-manus 后端之间通过 WebSocket 传递 JSON 消息。所有消息最外层均有 `type` 字段标识消息类型。

### 客户端 → QwenPaw（ai-manus 发送）

#### chat — 发起对话

```json
{
  "type": "chat",
  "user_id": "user-001",
  "text": "帮我分析这个文件",
  "content": [
    {"type": "text", "text": "帮我分析这个文件"},
    {"type": "file", "file_url": "https://...", "file_name": "report.pdf"}
  ]
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `type` | string | 是 | 固定值 `"chat"` |
| `user_id` | string | 是 | 用户标识 |
| `text` | string | 否 | 纯文本消息 |
| `content` | array | 否 | 多模态内容（text/file/image 等） |

#### approve — 批准工具执行

```json
{
  "type": "approve",
  "request_id": "approval-req-001"
}
```

#### deny — 拒绝工具执行

```json
{
  "type": "deny",
  "request_id": "approval-req-001",
  "reason": "涉及敏感操作"
}
```

#### ping — 心跳

```json
{"type": "ping"}
```

### QwenPaw → 客户端（ai-manus 接收）

所有服务端推送的消息均包含 `ws_type` 字段区分消息类别。

#### event — Agent 事件流

```json
{
  "ws_type": "event",
  "type": "message",
  "content": "我来帮你分析...",
  "...": "其他 AgentScope Event 字段"
}
```

#### approval — 工具审批请求

```json
{
  "ws_type": "approval",
  "request_id": "approval-req-001",
  "session_id": "session-001",
  "tool_name": "run_shell_command",
  "severity": "high",
  "result_summary": "rm -rf /data/temp/*",
  "timeout_seconds": 300,
  "created_at": "2025-06-01T10:00:00Z"
}
```

#### push_message — 主动推送

```json
{
  "ws_type": "push_message",
  "role": "assistant",
  "message": "你设置的提醒时间到了"
}
```

#### pong / error

```json
{"ws_type": "pong"}
```
```json
{"ws_type": "error", "error": "描述信息"}
```

## 架构说明

### 整体拓扑

```
┌─────────────────────┐              ┌─────────────────────────┐
│  ai-manus 后端       │              │  QwenPaw 容器            │
│                     │   WebSocket  │                         │
│  lingxi_partner.py ─┼──────────────┼─► /api/ai-manus/ws/{sid}│
│  (WS 客户端)         │◄─────────────┼─  ai_manus/__init__.py  │
│                     │   JSON 消息   │  (WS 服务端)             │
│                     │              │                         │
│                     │              │  ┌───────────────────┐  │
│                     │              │  │ Agent 引擎         │  │
│                     │              │  │ (AgentScope Runtime)│ │
│                     │              │  └───────────────────┘  │
└─────────────────────┘              └─────────────────────────┘
```

### QwenPaw 插件加载流程

1. QwenPaw 启动 → `ChannelManager.from_config()` 被调用
2. `get_channel_registry()` 合并内置 channel + 扫描 `CUSTOM_CHANNELS_DIR` 下的自定义 channel
3. `register_custom_channel_routes(app)` 调用每个自定义 channel 模块的 `register_app_routes()` 注册 HTTP/WS 路由
4. 根据 `config.json` 的 `channels.ai_manus.enabled` 决定是否实例化 `AiManusChannel`

### 单个 Session 的连接生命周期

```
ai-manus 发起 WS 连接
    │
    ▼
QwenPaw accept → 启动 poll_approvals() 后台协程
    │
    ├── 收到 chat → 取消上一个未完成的 run_agent()，启动新的
    │       └── run_agent() 遍历 ch._process() 生成器
    │           └── 每个事件序列化后 ws_type="event" 发回
    │
    ├── 收到 approve/deny → 调用 ApprovalService 处理
    │
    └── 收到 ping → 回复 pong
    │
    ▼
WS 断开 → 取消所有协程 → 清理 _active_ws 映射
```

### 对接后端代码

插件与 ai-manus 后端的对应关系：

| 插件侧（QwenPaw） | 后端代码（ai-manus） |
|---|---|
| `/api/ai-manus/ws/{session_id}` 端点 | [lingxi_partner.py](../backend/app/domain/services/flows/lingxi_partner.py) — `_build_ws_url()` |
| 接收 `chat` 消息，返回 `event` 事件流 | `run()` 发送消息，`_ws_reader_loop()` 接收事件 |
| `poll_approvals()` 推送审批 | 后端处理 `ws_type="approval"` 消息 |
| `send()` 推送提醒 | 后端处理 `ws_type="push_message"` 消息 |
| 应用关闭时 `stop()` 清理连接 | [main.py](../backend/app/main.py#L46-L48) 调用 `close_all_ws()` |

## 配置参考

### Channel 完整配置项

```jsonc
{
  "channels": {
    "ai_manus": {
      "enabled": true,              // 是否启用（必填）
      "bot_prefix": "",             // 机器人消息前缀
      "filter_tool_messages": false, // 过滤工具调用消息
      "filter_thinking": false,     // 过滤思考过程消息
      "dm_policy": "open",          // 私信策略
      "group_policy": "open",       // 群聊策略
      "allow_from": [],             // 白名单用户列表
      "deny_message": "",           // 拒绝访问时的提示
      "require_mention": false      // 是否需要 @机器人
    }
  }
}
```

日常使用只需设置 `"enabled": true`，其余使用默认值即可。

## 故障排查

### 插件未加载

**症状**：QwenPaw 日志中无 `AiManusChannel` 相关信息。

```bash
# 检查文件是否正确部署
docker exec qwenpaw ls -la /app/working/custom_channels/ai_manus/
# 应该有 __init__.py 文件

# 检查 QwenPaw 日志中的 channel 加载错误
docker exec qwenpaw grep -i "custom channel\|ai_manus\|AiManus" /app/working/qwenpaw.log
```

### WebSocket 连接被拒绝

**症状**：ai-manus 后端日志显示 `Connection refused` 或 `404`。

```bash
# 确认 WebSocket 端点已注册
docker exec qwenpaw grep "ai-manus/ws" /app/working/qwenpaw.log

# 确认 config.json 中 ai_manus.enabled = true
docker exec qwenpaw python3 -c "
import json
with open('/app/working/config.json') as f:
    cfg = json.load(f)
print(cfg.get('channels', {}).get('ai_manus', 'NOT FOUND'))
"
```

### 消息发送后无响应

**症状**：ai-manus 发送 `chat` 后收不到 `event` 回复。

```bash
# 1. 确认 WS 连接已建立
docker exec qwenpaw grep "ai-manus WS connected" /app/working/qwenpaw.log

# 2. 确认 Agent process 无报错
docker exec qwenpaw grep "Agent process error\|ai-manus WS error" /app/working/qwenpaw.log

# 3. 检查 ai-manus 后端日志
docker logs ai-manus-backend-1 2>&1 | grep -i "lingxi\|qwenpaw\|ws"
```

### 审批卡片未弹出

确认 QwenPaw 的 tool-guard 安全功能已开启。审批轮询间隔 1 秒，可能在工具调用后有几秒延迟。

## 开发指南

### 本地修改插件

1. 直接修改 `qwenpaw_plugin/ai_manus/__init__.py`
2. 重新部署到容器：

```bash
docker cp qwenpaw_plugin/ai_manus qwenpaw:/app/working/custom_channels/ai_manus
docker restart qwenpaw
```

3. 查看日志验证：

```bash
docker exec qwenpaw tail -f /app/working/qwenpaw.log | grep -i "ai.manus"
```

### 插件结构约束

插件必须满足以下条件才能被 QwenPaw 正确加载：

- 位于 `CUSTOM_CHANNELS_DIR`（`/app/working/custom_channels/`）下
- 是目录且包含 `__init__.py`（即 Python package），或是单文件 `.py` 模块
- 模块内至少有一个继承 `qwenpaw.app.channels.base.BaseChannel` 的类
- 该类有 `channel` 属性作为 channel 标识（本插件为 `"ai_manus"`）

### 可选：注册自定义路由

如果插件需要额外的 HTTP/WebSocket 端点，在模块顶层定义 `register_app_routes(app)` 函数。QwenPaw 启动时会自动调用它。**所有路由必须以 `/api/` 开头**，否则会被 SPA catch-all 吞掉。

本插件利用此机制注册了 `/api/ai-manus/ws/{session_id}` 端点。
