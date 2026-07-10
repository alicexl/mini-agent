# Demo3 — Rules + MCP

> 在 demo2 基础上增加 **Rules（行为约束）** 和 **MCP（外部工具协议）**，并把 plan 从手动开关改为 LLM 自动决策。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（8 章）
  1. 结论：demo3 vs demo2
  2. Rules：上下文约束
  3. MCP 协议：从函数调用到 RPC
  4. MCP Server 实现
  5. Agent 端：MCP Client + 工具合并
  6. Plan 决策：从手动开关到顶层分叉
  7. 真实案例：plan 拆步骤后跑通
  8. 总结与下一节预告

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | Agent 主程序（Part 1-7：客户端 / 本地工具 / Rules 加载 / 记忆 / MCP Client / 工具合并 / 主循环） |
| `mcp_server.py` | MCP Server（HTTP + JSON-RPC 2.0，暴露 add / multiply / weather 三个工具） |
| `.agent/rules.md` | Rules 规范文件（普通 Markdown），启动时拼进 system prompt |
| `.agent/skills/` | Skills 占位目录（demo4 主题） |
| `讲稿.md` | 教学讲稿 |
| `agent_memory.md` | 运行时生成的长期记忆文件（已 gitignore） |

## 设计要点

### Rules

- 规则文件位置：`.agent/rules.md`（普通 Markdown，非配置文件）
- 生效方式：`build_system_prompt()` 启动时读取该文件，拼到 system prompt 后缀
- System prompt 分三层：基础 prompt → Rules → 记忆（沿用 demo2 的 Progressive Context）

### MCP

- 协议：JSON-RPC 2.0 over HTTP，统一端点 `POST /mcp`，按 `method` 字段分发
- 三个核心 method：`initialize`（握手）→ `tools/list`（工具发现）→ `tools/call`（工具调用）
- 工具合并：MCP server 的 schema 与本地工具都用 `input_schema`，合并就是 list 拼接（`merge_tools`）
- 路由：`_dispatch_tool` 按 tool name 二选一——本地函数直接调用，MCP 工具走 JSON-RPC POST

### Plan 自动决策

- plan 从用户手动开关（demo2 的 `--plan` / `/plan`）改为 `run_agent` 顶层 1 轮决策的分叉点
- LLM 第 1 轮看到含 `plan` 的工具列表，自主判断：复杂任务调 plan 拆步骤，简单任务直接走 ReAct
- 两条路径共用 `run_agent_steps`（共享 messages 的 ReAct 子循环），只是工具集不同（plan 场景去掉 plan 工具，禁止嵌套）

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
