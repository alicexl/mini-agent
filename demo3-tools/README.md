# Demo3 — MCP 工具协议

> 在 demo2（base × 记忆）基础上叠加 MCP 跨进程工具协议——**继承全部记忆能力（无自动压缩），工具边界从本进程扩展到跨进程**。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（6 章）
  1. 结论：demo3 比 demo2 多了什么
  2. MCP 协议：从函数调用到 RPC
  3. MCP Server 实现
  4. Agent 端：MCP Client + 工具合并
  5. 与 demo2 的差异 + 真实案例
  6. 总结## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 主入口：客户端 + ReAct 主循环（本地/MCP 统一分发）+ REPL |
| `tools.py` | 工具层（demo1 四件套，本地） |
| `mcp.py` | **MCP 客户端层**：握手 / 发现 / 调用；Server 不可用时降级仅本地 |
| `render.py` | 渲染层（demo2 版：分色 + cache 统计） |
| `memory.py` | 记忆层（demo2 版减自动压缩：MEMORY.md / 手动 compact / caching） |
| `session.py` | 会话层：`memory/<会话ID>.jsonl` 持久化 + `/resume` |
| `commands.py` | 命令层：`/help` `/status` `/tools` `/memory` `/resume` `/new` `/compact` `/quit` |
| `mcp_server.py` | MCP Server（HTTP + JSON-RPC 2.0，暴露 add / multiply / weather 三个工具） |
| `memory/` | 运行时生成的双层记忆目录（已 gitignore，同 demo2） |

## 设计要点

### 与 demo2 的差异

- **新增 `mcp.py`**：MCP 客户端层——工具扩展轴的核心
- **无自动压缩**：主循环不查阈值，压缩只由 `/compact` 手动发起（记忆能力其余全部保留）

### MCP（外部工具协议）

- 协议：JSON-RPC 2.0 over HTTP，统一端点 `POST /mcp`，按 `method` 字段分发
- 本 demo 只实现 MCP 的 tools 能力，涉及三个主要 method：`initialize`（握手）→ `tools/list`（工具发现）→ `tools/call`（工具调用）
- 工具合并：MCP server 的 schema 与本地工具都用 `input_schema`，合并就是直接 `+` 拼接
- 路由：`_dispatch_tool` 按 tool name 二选一——本地函数直接调用，MCP 工具走 JSON-RPC POST
- 降级模式：MCP Server 未启动时，Agent 自动降级为仅本地工具模式（4 个工具）

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`anthropic` + `rich` + `prompt_toolkit` + `requests`。

### 配置 API Key

环境变量 `ANTHROPIC_API_KEY`（推荐，可持久化）；未设时启动交互式输入。MCP 地址在 `mcp.py` 顶部 `MCP_URL`（默认 `http://127.0.0.1:8888/mcp`）。

### 启动（两个终端）

```bash
# 终端 1：起 MCP Server
python mcp_server.py

# 终端 2：起 Agent
python -X utf8 agent.py
```

进入交互模式后输入任意任务（如「用 MCP 工具计算 35 乘以 47」——本地与 MCP 工具可在同一任务里混用，LLM 视角下无差异）。命令 `/help` 查看，`/tools` 看工具清单，`/quit` 退出。

> **降级模式**：如果 MCP Server 没启动，Agent 自动降级为仅本地工具模式（4 个工具），MCP 的 add / multiply / weather 不可用。

> **管道自动跑**：`printf '任务\n/quit\n' | python -X utf8 agent.py`——退出必须用 `/quit`。

### 调试 MCP Server

MCP 是开放协议，可以用 `curl` 直接验证三个 method：

```bash
# 握手
curl -X POST http://127.0.0.1:8888/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'

# 列工具
curl -X POST http://127.0.0.1:8888/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

# 调用 add
curl -X POST http://127.0.0.1:8888/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}'
```
