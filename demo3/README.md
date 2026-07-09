# Demo3 — Rules + MCP

> 目标：在 demo2（LLM × 工具 × 循环 × 记忆 × 规划）基础上增加 **Rules（行为约束）** 和 **MCP（外部工具协议）**，并把 `plan` 重新设计为 `run_agent` 顶层的自动决策分叉。
> 让 Agent 守规矩、能接入外部能力，并且自己决定什么时候该规划。

本文件配套 `agent.py` + `mcp_server.py` 使用，按照教学音频整理为 8 章。

---

## 1. 结论：demo3 vs demo2

**demo2 留下的三个遗憾：**

1. **工具硬编码** —— 只有 execute_bash / read_file / write_file，想接外部能力（查天气、查数据库、调第三方 API）必须改代码
2. **无行为约束** —— 大模型想写什么代码就写什么、想跑什么命令就跑什么（`rm -rf` 都行）
3. **规划靠手动** —— 用户得记得敲 `--plan` 或 `/plan`，Agent 自己不知道何时该规划

**demo3 的解法：**

| 维度 | demo2 | demo3 |
|---|---|---|
| **工具来源** | 全部本地硬编码 | **本地 4 + MCP 3 = 7 个工具，跨进程发现** |
| **plan 入口** | 独立 `--plan` / `/plan` 命令，用户手动开关 | **`run_agent` 顶层 1 轮决策，LLM 自主分叉** |
| **行为约束** | ❌ 无 | ✅ `.agent/rules.md` 注入 system prompt |
| **跨进程能力** | ❌ 工具只能同进程函数调用 | ✅ JSON-RPC 2.0 over HTTP |
| **架构** | 双层（run_agent 编排 + run_agent_step） | **同构双层：run_agent 分叉 + run_agent_steps 共享 messages** |

**新增的两个能力 × 一个结构调整：**

1. **Rules** —— 用 `.agent/rules.md` 文件约束大模型的代码生成 / 工具选择
2. **MCP** —— 把工具搬到独立的 HTTP 服务，通过 JSON-RPC 2.0 跨进程调用
3. **plan 自动决策** —— 把 plan 从用户手动开关改为 `run_agent` 顶层 1 轮决策的分叉点

> **demo3 = demo2 × Rules × MCP**（plan 自动决策）

---

## 2. 全局架构

```
demo3/
├── agent.py              # Agent 主程序（Part 1-7）
├── mcp_server.py         # MCP Server（HTTP + JSON-RPC 2.0）
├── requirements.txt      # anthropic + requests
└── .agent/
    ├── rules.md          # Rules 规范文件（注入 system prompt）
    └── skills/           # Skills 占位目录（demo4 主题）
```

### Agent 进程（agent.py）

```
┌──────────────────────────────────────────────────────┐
│  Part 1: LLM 客户端初始化（与 demo1/demo2 一致）         │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 2: 本地工具定义 + 实现（新增 plan 工具）            │
│   ─ execute_bash / read_file / write_file / plan       │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 3: Rules 加载器（新增）                            │
│   ─ 从 .agent/rules.md 读取规范                         │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 4: 记忆系统（沿用 demo2）                          │
│   ─ agent_memory.md + 滑动窗口 50 行                    │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 5: MCP Client（新增）                              │
│   ─ MCPClient.send: JSON-RPC 2.0 over HTTP             │
│   ─ initialize / list_tools / call_tool                │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 6: 工具合并（新增）                                │
│   ─ merge_tools: 本地 4 + MCP 3 = 7                     │
│   ─ 两端 schema 一致（input_schema），一行拼接           │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 7: Agent 主循环（顶层分叉 + 共享 messages ReAct）  │
│   ─ run_agent：1 轮顶层决策 → plan 场景 or 非 plan 场景  │
│   ─ run_agent_steps：共享 messages 的 ReAct 子循环       │
│   ─ _dispatch_tool：本地 or MCP 二选一（plan 不进这里）  │
└──────────────────────────────────────────────────────┘
```

### MCP Server 进程（mcp_server.py）

```
┌──────────────────────────────────────────────────────┐
│  Part 1: 工具定义（schema + fn）                         │
│   ─ add / multiply / weather                           │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 2: MCP 协议常量                                    │
│   ─ PROTOCOL_VERSION = "2024-11-05"                    │
│   ─ METHOD_INITIALIZE / TOOLS_LIST / TOOLS_CALL        │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 3: JSON-RPC Handler                               │
│   ─ handle_request → 按 method 分发                     │
│   ─ _handle_initialize / _handle_tools_list/call       │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 4: HTTP Server 入口                                │
│   ─ POST /mcp 接收 JSON-RPC，所有 method 走同一端点       │
└──────────────────────────────────────────────────────┘
```

---

## 3. Rules：上下文约束

### 3.1 为什么需要约束

写代码的 Agent 容易出**两个毛病**：

| 毛病 | 表现 |
|---|---|
| **风格漂移** | 一会儿 camelCase，一会儿 snake_case；Python 3.8 和 3.11 语法混用 |
| **行为失范** | `rm -rf` 也敢直接执行；写出 500 行的巨型函数 |

demo2 没有任何约束机制。最简的解法：**用一个 Markdown 文件，拼到 system prompt 里**。

### 3.2 Rules 文件

`.agent/rules.md`（普通 Markdown，不是配置文件）：

```markdown
## 一、代码生成规范

1. Python 版本：仅使用 Python 3.10 及以上语法
2. 命名规范：变量 / 函数用 snake_case，类用 PascalCase
3. 函数长度：单个函数不超过 100 行
4. 类型注解：所有公开函数必须带类型注解
```

### 3.3 Rules 怎么生效

```python
def load_rules() -> str:
    if not os.path.exists(RULES_FILE):
        return ""
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

def build_system_prompt(verbose=False) -> str:
    parts = [基础 prompt...]
    rules = load_rules()
    if rules:
        parts.append("\n## 项目规范（Rules）\n\n" + rules)   # ← 拼到后缀
    memory = load_memory()
    if memory:
        parts.append("\n## 历史任务记忆（最近）\n\n" + memory)
    return "\n".join(parts)
```

System Prompt 现在分**三层**：

```
┌─────────────────────────────────────────────┐
│  System Prompt                              │
│  ┌───────────────────────────────────────┐  │
│  │  基础 Prompt（不变）                    │  │
│  └───────────────────────────────────────┘  │
│  ┌───────────────────────────────────────┐  │
│  │  Rules（demo3 新增）                    │  │  ← .agent/rules.md
│  └───────────────────────────────────────┘  │
│  ┌───────────────────────────────────────┐  │
│  │  Progressive Context（沿用 demo2）      │  │  ← agent_memory.md
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### 3.4 Rules 揭示的原理

**Rules 是「记忆系统」的同构延伸** —— 都是把外部文件搬运进 prompt。区别只是：

- **记忆** 拼的是「过去做了什么」（动态）
- **Rules** 拼的是「希望 Agent 怎么做」（静态）

底层完全是同一个机制：**有选择地把外部信息搬运进 prompt**。

---

## 4. MCP 协议：从函数调用到 RPC

### 4.1 demo2 工具的局限

demo2 的工具都是**本地函数**：

```python
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}
```

简单高效，但能力被锁在**同一个 Python 进程**。现实世界的 Agent 需要：

- 查天气、查数据库、调第三方 API
- 用别人维护的远程服务
- 接非 Python 写的能力（Node / Go / Rust）

**MCP（Model Context Protocol）** 就是要统一接入这些外部能力。

### 4.2 MCP 的本质

把工具从"Agent 进程里的函数"改成"独立 HTTP 服务暴露的 RPC 方法"：

| 维度 | 本地工具（demo2） | MCP 工具（demo3） |
|---|---|---|
| **载体** | Python 函数 | HTTP 服务（任何语言） |
| **发现方式** | 看 `agent.py` 源码 | 调 `tools/list` 拿 schema |
| **调用方式** | `fn(**args)` 函数调用 | POST JSON-RPC `tools/call` |
| **位置** | 同进程 | 同机 / 跨机 / 跨云 |
| **扩展** | 改代码、重启 Agent | 加一个 MCP server，Agent 自动发现 |

### 4.3 JSON-RPC 2.0：MCP 的传输协议

**请求**：

```json
{
  "jsonrpc": "2.0",
  "id":      1,
  "method":  "tools/call",
  "params":  {"name": "add", "arguments": {"a": 2, "b": 3}}
}
```

**响应 · 成功**：

```json
{"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "5"}]}}
```

**响应 · 失败**：

```json
{"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "未知方法: foo"}}
```

### 4.4 MCP 三大核心 method

| method | 中文名 | 作用 |
|---|---|---|
| `initialize` | 握手 | 协议版本协商、能力声明（真实场景还会做鉴权） |
| `tools/list` | 工具发现 | 返回完整工具 schema 列表 |
| `tools/call` | 工具调用 | 按 `name` + `arguments` 执行，返回 `content` 包装结果 |

### 4.5 为什么选 JSON-RPC 而不是 REST

- **统一端点**：所有调用走同一个 `/mcp`，由 `method` 字段分发，URL 干净
- **协议无关**：可以走 HTTP、stdio、SSE——同一份协议适配多种传输

---

## 5. MCP Server 实现（mcp_server.py）

### 5.1 Part 1：工具定义（schema + 实现）

每个工具仍是「schema + fn」两件套，与本地工具**完全同构**（schema 字段名都一样，便于 Agent 直接合并）：

```python
TOOLS = [
    {
        "name": "add",
        "description": "计算两个数字的和",
        "input_schema": {                      # 与 agent.py 本地工具统一用 input_schema
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
    # multiply 同构
    # weather 查询指定城市（演示用，预设 + 随机兜底）
]

def fn_add(a, b):
    return a + b

def fn_multiply(a, b):
    return a * b

_WEATHER_DB = {"北京": ("晴", 25), "上海": ("多云", 22), ...}

def fn_weather(city):
    if city in _WEATHER_DB:
        condition, temp = _WEATHER_DB[city]
    else:
        condition = random.choice(["晴", "多云", "阴", "小雨"])
        temp = random.randint(10, 35)
    return f"{city} 今天天气：{condition}，气温 {temp}°C"

TOOL_FUNCTIONS = {"add": fn_add, "multiply": fn_multiply, "weather": fn_weather}
```

### 5.2 Part 3：JSON-RPC Handler

```python
def handle_request(payload: dict) -> dict:
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params", {}) or {}

    try:
        if method == "initialize":
            result = _handle_initialize(params)
        elif method == "tools/list":
            result = _handle_tools_list(params)
        elif method == "tools/call":
            result = _handle_tools_call(params)
        else:
            return _error(req_id, -32601, f"未知方法: {method}")

        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except Exception as e:
        return _error(req_id, -32603, f"服务端内部错误: {e}")


def _handle_initialize(params):
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "demo3-mcp-server", "version": "1.0.0"},
    }

def _handle_tools_list(params):
    return {"tools": TOOLS}

def _handle_tools_call(params):
    name = params.get("name")
    arguments = params.get("arguments", {}) or {}
    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"未知工具: {name}")
    fn = TOOL_FUNCTIONS[name]
    value = fn(**arguments)
    # MCP 协议规定返回值用 content 数组包装（便于多模态扩展）
    return {"content": [{"type": "text", "text": str(value)}]}
```

### 5.3 Part 4：HTTP Server 入口

```python
class MCPHTTPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            response = _error(None, -32700, f"JSON 解析失败: {e}")
        else:
            response = handle_request(payload)

        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
```

**就这么简单** —— MCP Server 的本质就是一个 HTTP 服务，按 JSON-RPC 协议处理三个 method。没有黑魔法。

> 注：`tools/call` 最后那一行 `{"content": [{"type": "text", "text": ...}]}` 看似啰嗦——但这个结构是为**多模态**留的扩展点。今天只塞纯文本，未来同一个工具调用可以同时返回 text、image、audio。

---

## 6. Agent 端：MCP Client + 工具合并

### 6.1 MCPClient：JSON-RPC 调用包装

```python
class MCPClient:
    def __init__(self, url: str):
        self.url = url
        self._id = 0
        self.initialized = False

    def _next_id(self):
        self._id += 1
        return self._id

    def send(self, method: str, params=None) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  method,
            "params":  params or {},
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"MCP 网络错误 ({method}): {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(f"MCP HTTP {resp.status_code}")
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"MCP 调用失败 ({method}): {data['error']}")
        return data.get("result", {})

    # 三个上层封装
    def initialize(self):
        result = self.send("initialize", {...})
        self.initialized = True
        return result

    def list_tools(self):
        return self.send("tools/list", {}).get("tools", [])

    def call_tool(self, name, arguments):
        result = self.send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        return "\n".join(b["text"] for b in content if b.get("type") == "text")
```

三层封装让 MCP 调用看起来就像普通 Python 方法——`mcp.list_tools()`、`mcp.call_tool("add", {...})`——但底层每次都是完整的 JSON-RPC 往返。

### 6.2 工具合并：一行代码搞定

两端 schema 格式完全一致（都用 `input_schema`），合并就是**直接拼接**——`_mcp_schema_to_anthropic` 转换函数都不需要：

```python
def merge_tools(local_tools, mcp_tools):
    """两端 schema 格式一致，直接拼接。"""
    return list(local_tools) + list(mcp_tools)
```

> **设计取舍**：MCP 协议规范本身用的是 `inputSchema`（驼峰），工业级实现需要在 Agent 端做一次字段名转换（`inputSchema` → `input_schema`）。demo3 既然两端都自己写，就让 MCP server 直接用 `input_schema`，省掉转换函数，让合并逻辑纯粹到「只是个 list 拼接」。真实场景接入第三方 MCP server 时仍需做这层兼容。

合并后大模型看到的就是一份完整的工具列表，**根本不知道哪个是本地的、哪个是 MCP 的**：

| 来源 | 工具名 | 用途 |
|---|---|---|
| 本地 | `execute_bash` | 执行 shell 命令 |
| 本地 | `read_file` | 读文件 |
| 本地 | `write_file` | 写文件 |
| 本地 | `plan` | 任务拆解（demo3 自动决策） |
| MCP | `add` | 加法 |
| MCP | `multiply` | 乘法 |
| MCP | `weather` | 天气查询 |

### 6.3 主循环路由：`_dispatch_tool` 只管本地 / MCP

主循环不再 if/else 硬写路由——普通工具调用统一交给 `_dispatch_tool`，**它只负责本地 or MCP 二选一**（plan 由 `run_agent` 顶层拦截，根本不进这里）：

```python
def _dispatch_tool(name, args, local_fns, mcp_client, verbose):
    """普通工具分发：本地 or MCP。plan 不在这里。"""
    if name in local_fns:
        return str(local_fns[name](**args))
    return mcp_client.call_tool(name, args)
```

**对大模型来说，调用本地工具和 MCP 工具是完全一样的体验**：都是 `tool_use` 块、都拿到 `tool_result`。差异藏在 `_dispatch_tool` 里。

---

## 7. Plan 决策：从手动开关到顶层分叉

### 7.1 demo2 的尴尬

demo2 的 plan 要**用户手动开**：

```bash
python agent.py --plan     # 启动时全局开启
# 或 REPL 内
/plan                      # 切换 Plan 模式
```

问题：
- 用户**忘了开**，复杂任务就走一步看一步
- 用户**不知道任务复杂度**，开关时机难判断
- 大模型**最懂任务**，但它没有决定权

### 7.2 demo3 的解法：顶层 1 轮决策，分两条路径

demo3 把决策权交回 LLM，但**不是把 plan 做成循环内的普通工具**——而是让 `run_agent` 先做 **1 轮顶层决策**，按 LLM 是否调 `plan` 工具分两条路径：

```python
def run_agent(user_input, all_tools, ...):
    messages = [{"role": "user", "content": user_input}]
    response = client.messages.create(tools=all_tools, messages=messages, ...)

    plan_block = find_plan_tool_use(response)
    if plan_block:
        # ===== Plan 场景 =====
        steps = plan_block.input["steps"]
        ...                                                                    # 见 §7.3
        for step in steps:
            messages.append({"role": "user", "content": step})
            run_agent_steps(messages, step_tools, ...)  # 共享 messages
        return run_agent_steps(messages, step_tools, ...)  # 最终总结
    else:
        # ===== 非 Plan 场景 =====
        return run_agent_steps(messages, all_tools, ..., initial_response=response)
```

LLM 在第 1 轮决策时，看到工具列表里的 `plan` 工具：

```python
LOCAL_TOOLS = [
    # execute_bash / read_file / write_file 不变
    {
        "name": "plan",
        "description": (
            "任务规划工具。当用户的任务复杂、需要拆解成多个有序步骤时调用。"
            "大模型通过此工具返回结构化的 steps 列表，由 Agent 逐步执行。"
            "简单任务无需调用此工具。"               # ← 让模型自己判断
        ),
        "input_schema": { ... },
    },
]
```

description 那句"**复杂任务调用、简单任务无需调用**"是关键——LLM 读了就会自己判断：简单任务直接走 ReAct；复杂任务先调 plan 拆步骤。

### 7.3 实现：`run_agent` 分叉 + `run_agent_steps` 共享 messages

核心思路：**两种路径都跑同一个 `run_agent_steps`**（共享 messages 的 ReAct 循环），只是工具集不同。

```python
def run_agent_steps(messages, tools, local_fns, mcp_client,
                    system_prompt, verbose, initial_response=None):
    """共享 messages 的 ReAct 子循环。"""
    response = initial_response
    for i in range(1, STEP_MAX_ITERATIONS + 1):
        if response is None:
            response = client.messages.create(
                model=MODEL, tools=tools, system=system_prompt, messages=messages,
            )
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            # name 不可能是 "plan"（step_tools 已去掉），统一走 _dispatch_tool
            result = _dispatch_tool(block.name, block.input or {},
                                     local_fns, mcp_client, verbose)
            tool_results.append({"type": "tool_result",
                                 "tool_use_id": block.id, "content": str(result)})
        messages.append({"role": "user", "content": tool_results})
        response = None
    return f"[未在 {STEP_MAX_ITERATIONS} 轮内完成]"


def run_agent(user_input, all_tools, local_fns, mcp_client, ...):
    messages = [{"role": "user", "content": user_input}]
    response = client.messages.create(tools=all_tools, messages=messages, ...)

    plan_block = next((b for b in response.content
                       if b.type == "tool_use" and b.name == "plan"), None)

    if plan_block:
        # ===== Plan 场景：遍历 steps，共享 messages =====
        steps = plan_block.input.get("steps", []) or []
        messages.append({"role": "assistant", "content": response.content})
        messages.append({  # plan ack：明确告诉 LLM「只做当前 step」
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": plan_block.id,
                         "content": (
                             f"已规划 {len(steps)} 个步骤。我会逐个发给你下一步任务，"
                             f"请严格遵守：只执行当前这一步，完成后立即 end_turn，"
                             f"不要预先做后续 step。")}],
        })
        step_tools = [t for t in all_tools if t.get("name") != "plan"]   # 禁止嵌套

        for i, step in enumerate(steps, 1):
            # step 消息也包装一遍，双重保险
            step_msg = f"【Step {i}/{len(steps)}】请只执行这一步：\n{step}\n\n完成后立即 end_turn。"
            messages.append({"role": "user", "content": step_msg})
            run_agent_steps(messages, step_tools, local_fns, mcp_client, ...)

        # 所有 step 完成后，让 LLM 看完整上下文做最终总结
        return run_agent_steps(messages, step_tools, local_fns, mcp_client, ...)

    # ===== 非 Plan 场景：首轮响应直接灌进 ReAct =====
    return run_agent_steps(messages, all_tools, local_fns, mcp_client, ...,
                           initial_response=response)
```

关键设计点：

| # | 设计 | 用意 |
|---|---|---|
| 1 | **`run_agent` 只做 1 轮顶层决策** | 让 LLM 用全部工具（含 plan）选一条路，不参与 step 执行 |
| 2 | **两种场景共享 `run_agent_steps`** | 同一份 ReAct 循环代码，工具集不同（plan 场景去掉 plan） |
| 3 | **step 间共享 messages** | 与 demo2 一致——上下文跨 step 积累，前一步的结果后一步能看见 |
| 4 | **step_tools 去掉 plan（禁止嵌套）** | step 里再调 plan 会让大模型迷路；执行权交给 Agent |
| 5 | **prompt 强制单步执行** | plan ack + step 包装双重告诉 LLM「只做当前 step、不预先做后续」——否则 LLM 会主动链式把所有工具一口气调完，让 step 遍历失去意义 |
| 6 | **plan 完成后显式调 LLM 总结** | 看完整 step 上下文做总结，不会"再来一遍" |

### 7.4 架构：顶层分叉 + 共享 messages 的 ReAct

```
demo2 的双结构：                demo3 的同构双结构：

  run_agent                      run_agent（顶层 1 轮决策）
    ├ get_plan（规划，手动开关）    │  ├─ Plan 场景：遍历 steps
    └ run_agent_step × N           │  │   ├ messages.append(user=s1)
       （每 step 独立 ReAct）       │  │   ├ run_agent_steps(...)  ← 共享 messages
                                    │  │   ├ messages.append(user=s2)
                                    │  │   ├ run_agent_steps(...)
                                    │  │   └ ...
                                    │  └─ 非 Plan 场景：直接 run_agent_steps
                                    └ run_agent_steps（共享 messages 的 ReAct 循环）
```

**让 LLM 用工具决定一切**——是否拆、怎么拆都交给大模型；**拆完的执行交给 Agent**——共享 messages 让上下文自然积累，prompt 强制单步避免大模型一口气做完跳过中间状态，避免大模型"忘了"自己在第几步。这正是工业级 Agent 框架的设计思路。

---

## 8. 示例解读：plan 拆步骤后跑通

任务**故意选成有自然多步结构**——加法 → 乘法 → 查天气 → 写文件 → 读回验证，5 个 step，让大模型有充分理由调 plan。下面是真实运行出来的轨迹。

### 第 0 阶段：启动 + 工具发现

```
# 终端 1
$ python mcp_server.py
Demo3 MCP Server 已启动
监听:   http://127.0.0.1:8888/mcp
工具:   add, multiply, weather

# 终端 2
$ python agent.py
[MCP] 握手成功：{'name': 'demo3-mcp-server', 'version': '1.0.0'} 协议版本 2024-11-05
[MCP] 发现 3 个工具：add, multiply, weather
[Tools] 合并后共 7 个工具：execute_bash, read_file, write_file, plan, add, multiply, weather
```

### 第 1 轮 ReAct：LLM 选择 plan

```
用户: 任务如下：1) 计算 35+47；2) 用第 1 步的结果乘以 8；3) 查北京天气；
              4) 把结果写到 test_report.txt；5) 验证写入成功

[LLM 决策] stop_reason = tool_use
  - text     : 好的！这个任务包含 5 个步骤，我先调用 `plan` 工具进行拆解，然后逐步执行。
  - tool_use : plan({'steps': [
      '用 add 工具计算 35+47',
      '用第 1 步的结果乘以 8',                          ← 共享 messages 依赖
      '用 weather 工具查询北京天气',
      '把前三步的结果（可从 messages 取）写入 test_report.txt',   ← 共享 messages 依赖
      '用 read_file 验证文件写入成功'
    ]})

[Plan] LLM 拆解 5 个步骤，共享 messages 逐步执行
```

`run_agent` 检测到 plan tool_use → **进入 plan 分叉**：plan ack 作为 tool_result 回灌（**ack 内容里明确写了「逐个发给你、只做当前 step、不预先做后续」**），剥离 plan 工具，然后**遍历 steps，每个 step 都包装成 `【Step i/N】请只执行这一步` 后追加到同一份 messages 上**跑 `run_agent_steps`。

### Step 1-5：每个 step 严格做一件事

```
──────────────────────────────────────────────────────────
[Step 1/5] 用 add 工具计算 35+47
──────────────────────────────────────────────────────────
  [LLM] add({'a': 35, 'b': 47})
  [工具 · MCP]  add({'a': 35, 'b': 47})   →  82
  [迭代 2 完成] ✅ 第 1 步完成：35 + 47 = 82，等待下一步指令。

──────────────────────────────────────────────────────────
[Step 2/5] 用第 1 步的结果乘以 8
──────────────────────────────────────────────────────────
  [LLM] multiply({'a': 82, 'b': 8})        ← 82 来自共享 messages 里 Step 1 的 tool_result
  [工具 · MCP]  multiply({'a': 82, 'b': 8})  →  656
  [迭代 2 完成] ✅ 第 2 步完成：82 × 8 = 656

──────────────────────────────────────────────────────────
[Step 3/5] 用 weather 工具查询北京天气
──────────────────────────────────────────────────────────
  [LLM] weather({'city': '北京'})
  [工具 · MCP]  weather({'city': '北京'})  →  北京 今天天气：晴，气温 25°C
  [迭代 2 完成] ✅ 第 3 步完成：北京天气查询结果，晴，25°C

──────────────────────────────────────────────────────────
[Step 4/5] 把前三步的结果（可从 messages 取）写入 test_report.txt
──────────────────────────────────────────────────────────
  [LLM] write_file({'path': 'test_report.txt',
                    'content': '========== 任务报告 ==========\n\n1️⃣ 加法计算：35 + 47 = 82\n2️⃣ 乘法计算：82 × 8 = 656\n3️⃣ 北京天气：晴，气温 25°C\n=================================='})
  [工具 · 本地] write_file(...)   →  [成功] 文件已写入 (142 字符)
  [迭代 2 完成] ✅ 第 4 步完成：已将所有结果写入 test_report.txt（142 字符）

──────────────────────────────────────────────────────────
[Step 5/5] 用 read_file 验证文件写入成功
──────────────────────────────────────────────────────────
  [LLM] read_file({'path': 'test_report.txt'})
  [工具 · 本地] read_file(...)    →  ========== 任务报告 ========== 1️⃣ 加法计算：35 + 47 = 82 ...
  [迭代 2 完成] ✅ 第 5 步完成：文件验证通过！
```

**每个 step 严格只调一次工具就 end_turn**——prompt 双重约束（plan ack + step 包装）让 LLM 不会主动跨 step 链式调用。Step 2 的 multiply 直接从共享 messages 里看到 Step 1 的 tool_result 拿到 82，无需 LLM 重算；Step 4 的 write_file 从 messages 里看到前 3 步的 tool_result 组装报告内容。

注意——**大模型根本不知道 `add` / `multiply` / `weather` 是远程工具**。Step 1-3 走 MCP RPC、Step 4-5 走本地函数，路由差异全藏在 `_dispatch_tool`，对 LLM 完全透明。

**为什么必须强制单步**？如果不强制，LLM 进入 Step 1 时看到 messages 里 plan 全貌 + 工具都齐，会主动链式一口气把 5 个工具全调完——表面上效率高，实际上让 step 遍历、进度条、可中断/重试都失去意义。所以 `run_agent` 在 plan ack 内容 + step 包装消息双重告诉 LLM「只做当前 step」。

### plan 的最终总结

```
============================================================
[Plan] 所有步骤完成，请求 LLM 最终总结
============================================================
  [LLM] 看到完整的 5 步执行历史（共享 messages）→ 直接生成汇总表
  [迭代 1 完成]

助手: ✅ 第 5 步完成：文件验证通过！内容完整无误。

       ## 🎉 任务全部完成！总结如下：

       | 步骤 | 工具 | 说明 | 结果 |
       |------|------|------|------|
       | 1️⃣ | `add` | 计算 35+47 | **82** |
       | 2️⃣ | `multiply` | 计算 82×8 | **656** |
       | 3️⃣ | `weather` | 查询北京天气 | **晴，25°C** |
       | 4️⃣ | `write_file` | 写入文件 | ✅ 已写入 `test_report.txt` |
       | 5️⃣ | `read_file` | 验证写入 | ✅ 内容完整无误 |

       所有计算与天气数据均通过工具获取，结果可靠准确！
```

最后一轮总结没有重做——`run_agent` 在 5 个 step 跑完后**再调一次** `run_agent_steps`（不再 append user 消息，直接让 LLM 看 messages 收尾）。LLM 看到 messages 里 5 个 step 的 tool_use / tool_result + 每个 step 的 end_turn 总结，**没有任何新的工具调用**，直接生成最终汇总表 end_turn。

### 完整执行结构

与 §7.4 图同构，只是把具体步骤填进去：

```
run_agent（顶层 1 轮）
  ├ LLM tool_use=plan([s1..s5])  ← 1 次 LLM
  ├ plan ack 回灌（含「只做当前 step」指令）→ 剥离 plan 工具
  ├ 遍历 5 个 steps（共享 messages，每个 step 包装【Step i/N】前缀）：
  │     每个 step → run_agent_steps → 严格 1 次工具调用 + end_turn   ← 各 2 次 LLM
  └ run_agent_steps（最终总结）→ LLM end_turn，输出汇总表            ← 1 次 LLM

总计：12 次 LLM 请求 + 5 次工具调用
```
```

### 教学要点

| # | 观察 | 启示 |
|---|---|---|
| 1 | **每个 step 严格 1 次工具调用** | prompt 强制单步（plan ack + step 包装）→ LLM 调完工具立即 end_turn，不主动链式 |
| 2 | **Step 2 复用 Step 1 的 82** | 共享 messages 让后续 step 直接看到前序 tool_result，无需重算 |
| 3 | **MCP 工具和本地工具在每个 step 里混用** | 大模型无感——add/multiply/weather 走 MCP RPC，write_file/read_file 走本地函数，路由差异藏在 `_dispatch_tool` |
| 4 | **最终总结无新工具调用** | 5 个 step 跑完后 `run_agent_steps` 只让 LLM 看 messages 收尾，0 次冗余 tool_use |
| 5 | **总计 12 次 LLM 请求 + 5 次工具调用** | 每个 step 2 次 LLM（tool_use + end_turn）+ 顶层 1 次 + 最终总结 1 次 |

### 对照：非 plan 简单场景（顶层分叉的另一条路径）

日常用户提问大多是**单步就能搞定**的，比如「查一下上海天气」。这种场景走的是 `run_agent` 顶层分叉的另一条路——**直接进入 ReAct**，没有 plan 拆解、没有 step 遍历。

```
用户: 用 weather 工具查一下上海天气

============================================================
顶层决策：plan or 直接执行
============================================================
[messages] 当前 1 条消息
  [0] user     : 用 weather 工具查一下上海天气

[LLM 决策] stop_reason = tool_use
  - tool_use : weather({'city': '上海'})      ← LLM 直接选了 MCP 工具，没拆 plan

[非 Plan] 直接进入 ReAct 循环（共享 messages）
  [LLM] weather({'city': '上海'})
  [工具 · MCP]  weather({'city': '上海'})
  [结果] 上海 今天天气：多云，气温 22°C
  [迭代 2 完成] 🌤️ 上海天气：多云，22°C，适宜出行

助手: 🌤️ 上海天气查询结果：天气状况多云，气温 22°C，今天上海多云，气温适宜，适合出行！
```

| # | 观察 | 启示 |
|---|---|---|
| 1 | **LLM 第 1 轮直接选 weather** | 任务简单时 LLM 不会无脑拆 plan，顶层决策自然走到非 plan 分叉 |
| 2 | **`initial_response` 透传** | `run_agent` 把顶层那一次的 response 直接传给 `run_agent_steps` 当起点，**不重发 LLM 请求**——零冗余 |
| 3 | **总计 2 次 LLM 请求 + 1 次工具调用** | 顶层决策 1 次 + ReAct 收尾 1 次，工具调用只发生在 ReAct 子循环里 |

**两条路径的对称性**——`run_agent` 的顶层分叉让架构保持极简：

- **plan 路径**：1 次顶层 LLM 请求拿到 plan tool_use → 遍历 steps，每步 `run_agent_steps`（共享 messages）→ 最终一次 `run_agent_steps` 收尾
- **非 plan 路径**：1 次顶层 LLM 请求拿到非 plan 的 tool_use → 把这次 response **透传**给 `run_agent_steps` 当起点 → 一轮 ReAct 收尾

两条路径**用同一个 `run_agent_steps` 兜底**，差别只在要不要遍历 steps、要不要透传 initial_response。这就是顶层分叉设计的美感——**「判断走哪条路」和「走路」彻底分开**。

---

## 9. 局限与演进方向

### 9.1 MCP 协议本身的能力没用上

工业级 MCP 还支持：
- **资源订阅**（`resources/subscribe`）—— 服务端推送更新
- **提示词模板**（`prompts/get`）—— 服务端提供 prompt 片段
- **流式响应**（SSE transport）—— 长任务进度推送
- **鉴权 / OAuth** —— 真实场景的握手要带 token

demo3 只实现了最小子集（HTTP + 三个 method + 无鉴权），够展示原理。

### 9.2 plan 仍是单次

拆完直接执行，没有用户确认、没有失败重规划。

| 演进方向 | 做法 |
|---|---|
| **Human-in-the-loop** | 拆完后让用户逐 step 确认，避免大模型拆错直接全跑废 |
| **失败重规划** | 某 step 失败后重新规划剩余部分 |

---

## 10. 总结

| 能力 | demo1 | demo2 | demo3 |
|---|---|---|---|
| LLM × 工具 × 循环 | ✅ | ✅ | ✅ |
| 跨任务长期记忆 | ❌ | ✅ | ✅ |
| 规划 + 多步串联 | ❌ | ✅ | ✅（plan 自动决策） |
| 行为约束（Rules） | ❌ | ❌ | ✅ |
| 外部工具协议（MCP） | ❌ | ❌ | ✅ |

**demo3 三次升级三次对症下药**——Rules 解决行为约束、MCP 解决外部工具、plan 自决策解决手动开关。

> **demo1 = LLM × 工具 × 循环**
> **demo2 = demo1 × 记忆 × 规划**
> **demo3 = demo2 × Rules × MCP**
> **demo4 = demo3 × subagent**（分工合作）

---

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单（仅两项）：

```
anthropic>=0.40.0
requests>=2.28.0
```

### 配置 API Key

打开 `agent.py`，把 Part 1 顶部的 `API_KEY = ""` 改成你的智谱 BigModel Key：

```python
# agent.py Part 1
API_KEY         = ""                                         # ← 只改这一行
BASE_URL        = "https://open.bigmodel.cn/api/anthropic"
MODEL           = "glm-5.2"
API_TIMEOUT_MS  = 3000000                                    # 50 分钟
MCP_URL         = "http://127.0.0.1:8888/mcp"
```

**方式 1：改代码（最简单）** — 直接把 `API_KEY = ""` 改成你的 Key。

**方式 2：首次运行交互式提示** — `API_KEY` 为空时直接运行 `python agent.py`，会提示输入（仅本次运行有效，不持久化）。

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖。

### 启动（两个终端）

```bash
# 终端 1：起 MCP Server
python mcp_server.py

# 终端 2：起 Agent
python agent.py
```

进入交互模式后输入任意任务，观察每一轮 ReAct 循环的决策、行动、感知。输入 `quit` / `exit` / `q` 退出。

### REPL 命令

| 命令 | 作用 |
|---|---|
| `quit` / `exit` / `q` | 退出 |

> demo3 把 demo2 的 `/plan` / `/no-plan` / `/memory` 命令**移除了** —— 因为 plan 已经改为 `run_agent` 顶层自动决策，不再需要手动切换；记忆查看请直接看 `agent_memory.md` 文件。

### 调试 MCP Server

MCP 是开放协议，可以用 `curl` 直接验证：

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

### 降级模式（MCP 不可用）

如果 MCP Server 没启动，Agent 会自动降级为「仅本地工具」模式：

```
[MCP] 连接失败，降级为仅本地工具模式。原因: ...
[MCP] 请确认已在另一个终端运行：python mcp_server.py
[Tools] 合并后共 4 个工具：execute_bash, read_file, write_file, plan
```

Agent 仍可正常使用本地 4 个工具，只是 MCP 的 add / multiply / weather 不可用。

> **注**：`verbose=True` 默认开启，打印每一轮的完整决策、工具调用、路由分发（标 `[执行 · 本地]` 或 `[执行 · MCP]`）。
> 运行时会在当前目录生成 `agent_memory.md`（每个用户的记忆不同，不应提交）。
