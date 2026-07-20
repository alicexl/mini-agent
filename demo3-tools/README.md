# Demo3 — 工具扩展轴

> 在 demo1-react（base）上独立叠加「工具轴」：新增本地 `edit` 工具 + 接入 MCP 外部工具协议。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（7 章）
  1. 结论：demo3 vs demo1
  2. 本地工具扩展：edit（string replacement）
  3. MCP 协议：从函数调用到 RPC
  4. MCP Server 实现
  5. Agent 端：MCP Client + 工具合并
  6. 真实案例：edit 精细修改 + MCP 远程调用
  7. 总结与下一节预告

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | Agent 主程序（Part 1-5：客户端 / 本地工具（含 edit）/ 工具实现 / MCP Client / 主循环） |
| `mcp_server.py` | MCP Server（HTTP + JSON-RPC 2.0，暴露 add / multiply / weather 三个工具） |
| `讲稿.md` | 教学讲稿 |

## 设计要点

### 本地工具扩展：edit

- demo1 的 3 件套（execute_bash / read_file / write_file）保留不变
- demo3 新增 `edit`——精确替换文件中的一段文本（string replacement）
- 与 write_file 的核心区别：
  - `write_file`：发整文件内容 → 重写整文件（适合创建新文件）
  - `edit`：只发 old + new 两段 → 在原文件上做替换（适合改一行 / 改一个值）
- 默认只替换第一处；`replace_all=true` 替换全部匹配
- 设计动机与 Claude Code 的 Edit 工具一致——对大文件做小改动时节省 token

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

依赖清单（`anthropic` + `requests`）。

### 配置 API Key

打开 `agent.py`，把 Part 1 顶部的 `API_KEY = ""` 改成你的智谱 BigModel Key：

```python
# agent.py Part 1
API_KEY         = ""                                         # ← 只改这一行
BASE_URL        = "https://open.bigmodel.cn/api/anthropic"
MODEL           = "glm-5.2"
MCP_URL         = "http://127.0.0.1:8888/mcp"
```

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖。

### 启动（两个终端）

demo3 需要**两个进程**——MCP Server 和 Agent 各占一个终端：

```bash
# 终端 1：起 MCP Server
python mcp_server.py

# 终端 2：起 Agent
python agent.py
```

进入交互模式后输入任意任务。输入 `quit` / `exit` / `q` 退出。

> **降级模式**：如果 MCP Server 没启动，Agent 自动降级为仅本地工具模式（4 个工具），MCP 的 add / multiply / weather 不可用。

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
