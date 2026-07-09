# Demo1 — Agent 底层原理

> 目标：用最少的代码展现 Agent 的底层运行机制。
> 一个能干活、但只有「短期记忆」的最简 Agent。

本文件配套 `agent.py`（单文件实现）使用，按照教学音频整理为 6 章。

---

## 1. 结论

**Agent 与普通对话的区别（5 个维度）：**

| 维度 | 普通对话 | Agent |
|---|---|---|
| 交互模式 | 一问一答，用户驱动 | 有循环，目标驱动，自主往下走 |
| 能力边界 | 只能生成文本 | 可以调用工具作用于真实世界 |
| 执行流程 | 用户提问 → 模型回答 | 用户下达任务 → LLM 思考 → 调用本地工具 → 观察 → 继续思考，不停循环 |
| 状态管理 | 无独立记忆，或靠大模型自身上下文 | 维护完整消息历史，包含工具调用与结果 |
| 自主性 | 无 | 通过与模型交互决定下一步做什么、用什么工具、是否达成目标、何时停止 |

**一句话总结：**

> Agent 不仅有大模型的能力，还有本地工具的能力，还实现了为达成一个目标、一直循环去得到结果的能力。

**Agent 的三要素：**

| 要素 | 角色 |
|---|---|
| **LLM** | 思考能力 —— 大脑 |
| **工具** | 作用于真实世界 / 本地环境的能力 —— 手脚 |
| **循环** | 实现任务分解和迭代，直到完成目标 |

普通对话是「你问我答」；Agent 是「我们为了一个目标，努力去想办法、去完成这个目标」。

---

## 2. 全局架构

最简 Agent 由 **4 个部分** 组成：

```
┌──────────────────────────────────────────────────┐
│  Part 1: LLM 客户端初始化                          │
│  ─ 通过环境变量引用配置，不绑定具体厂商和模型         │
│    兼容任何 Anthropic 协议格式的大模型服务           │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  Part 2: 工具定义（Function Calling schema）       │
│  ─ 一份「工具说明书」，每次随请求发给大模型           │
│    告诉大模型：你有哪些本地能力                       │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  Part 3: 工具实现 + 路由表                          │
│  ─ 真正的执行函数（execute_bash / read_file / ...）│
│  ─ AVAILABLE_FUNCTIONS 路由表：工具名 → 函数         │
└──────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────┐
│  Part 4: Agent 主循环（感知 / 行动 / 决策 = ReAct） │
│  ─ messages → LLM 决策 → 执行工具 → 追加结果        │
│    → 回 LLM → 直到 end_turn 或 MAX_ITERATIONS     │
└──────────────────────────────────────────────────┘
```

对应 `agent.py` 中的四个章节注释。

---

## 3. 逐层解读

### Part 1 — LLM 客户端初始化

不绑定具体厂商，通过环境变量决定走官方 API 还是某个兼容网关：

```python
API_KEY  = os.environ.get("ANTHROPIC_API_KEY")
BASE_URL = os.environ.get("ANTHROPIC_BASE_URL")     # 可选，指向兼容网关
MODEL    = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

client = Anthropic(api_key=API_KEY, base_url=BASE_URL or None)
```

**要点**：这一层只负责「能调通大模型」，具体怎么调、参数怎么填，按实际选用的 SDK 实现就行。

### Part 2 — 工具定义（Function Calling 标准格式）

工具定义用标准 schema 格式：每个工具有 `name`、`description`、`input_schema`。

```python
TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可用于文件操作、系统命令等",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"}
            },
            "required": ["command"],
        },
    },
    # read_file / write_file 同理
]
```

**关键理解**：大模型本身不会执行代码。但通过这份「说明书」，它知道自己**可以请求调用**哪个函数、用什么参数。**真正的执行发生在本地代码里。**

### Part 3 — 工具实现 + 路由表

每个工具对应一个普通 Python 函数。设计要点：

```python
def execute_bash(command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,          # ← 让命令拥有更强能力（风险换能力）
        capture_output=True,
        text=True,
        timeout=60,          # ← 设置超时，防止死循环或长时间阻塞
    )
    # 错误信息也字符串化返回给大模型 → 让它自己看到错误后调整策略
    return ...


# 路由表：工具名 → 实际函数（调度核心）
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}
```

**三个设计点：**

1. **错误信息返回大模型** — 让大模型看到 stderr / 非零 exit code，它的思考能力就能去修复策略、尝试别的命令。
2. **超时设置** — 防止某些命令长时间阻塞或陷入死循环。
3. **`shell=True`** — 风险比较大，但让 Agent 拥有更强的能力，因为有些任务确实需要。

**路由表是调度核心**：当大模型说「我要调用 `execute_bash`」时，Agent 通过这张字典把名字映射到具体函数并执行。新增工具只要改这张表（demo1 是硬编码，未来可插件化）。

### Part 4 — Agent 主循环（ReAct）

```python
MAX_ITERATIONS = 30  # 防止大模型陷入死循环

def run_agent(user_input: str, verbose: bool = True) -> str:
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        # 1. 决策：大模型思考下一步
        response = client.messages.create(
            model=MODEL, system=SYSTEM_PROMPT,
            tools=TOOLS, messages=messages,
        )

        # 2. 判断是否结束
        if response.stop_reason != "tool_use":
            return extract_text(response.content)

        # 3. 行动：本地执行工具
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                fn = AVAILABLE_FUNCTIONS[block.name]      # 路由表调度
                result = fn(**block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,               # Tool ID 精确匹配
                    "content": result,
                })
        # 4. 感知：把结果作为 user 消息追加，下一轮大模型就能看到
        messages.append({"role": "user", "content": tool_results})

    return "[错误] 超过最大循环次数"
```

**循环四步**：决策 → 判停 → 行动 → 感知，对应教学音频里的 **ReAct**（Reasoning + Acting）。

---

## 4. 示例解读循环的运行时序

任务：「统计当前目录下有多少个 Python 文件，并把结果写入 `count.txt`」

### 第 1 轮

```
messages = [
    {system: "你是一个有用的助手，可以通过工具与系统交互..."},
    {user:   "统计当前目录下有多少个 Python 文件，并把结果写入 count.txt"},
]
```

大模型意识到这不是一个文本任务，是一个具体执行任务 → 通过工具说明书知道有 `execute_bash` → 返回 tool_use：

```
[LLM 决策] tool_use: execute_bash({"command": "ls *.py | wc -l"})
```

本地执行，得到 `42`。把 assistant 的 tool_use 块和 user 的 tool_result 一并追加进 messages。

### 第 2 轮

```
messages = [
    {system},
    {user:   "统计当前目录下..."},                    ← 原始任务
    {assistant: [tool_use(execute_bash, "ls *.py | wc -l")]},
    {user:   [tool_result(tool_id=xxx, content="42")]}   ← 工具结果
]
```

大模型看到「42」+ 之前的 system + 原始任务 → 决定调用 `write_file` 把结果写盘：

```
[LLM 决策] tool_use: write_file({"path": "count.txt", "content": "42"})
```

本地写入成功，返回 `[成功] 文件已写入: count.txt (2 字符)`。

### 第 3 轮

```
messages = [
    {system},
    {user:   原始任务},
    {assistant: tool_use execute_bash},
    {user:   tool_result "42"},
    {assistant: tool_use write_file},
    {user:   tool_result "[成功] 文件已写入..."},        ← 写入成功
]
```

大模型看到写入成功 → 判断任务完成 → `stop_reason = "end_turn"` → 返回最终文本：

```
[循环结束] 大模型判断任务完成，退出循环
助手: 已统计完成，当前目录下共有 42 个 Python 文件，结果已写入 count.txt
```

**整个时序的本质**：每一轮都把「完整 messages」重新发给大模型，让它在所有历史里找当前进度、决定下一步。

---

## 5. 深入理解关键设计

### 5.1 为什么要有 `MAX_ITERATIONS`？

默认 30 次（也可设 20 / 50）。原因：

- 大模型可能陷入**死循环**：你给它的某些工具或网络调用，永远不可能成功，但模型会一直重试。
- 设置上限确保程序**最终能停下来**，不会一直在工具调用里打转。

这是最简单的「中止策略」，未来可以设计更复杂的（识别重复模式、错误计数等）。

### 5.2 messages 怎么演化？

第一篇 demo **不涉及 messages 的长期保存**。每次循环都做一件事：**把整个 messages 全部带上，发给大模型**。

大模型在所有 message 里找当前执行到哪、下一步该怎么做。短期记忆由此产生 —— **只在一个任务循环周期内有记忆**。

### 5.3 大模型如何决定使用工具？

大模型并没有真正执行代码。它和客户端约定了一个接口叫「工具说明书」（Function Calling 协议）。大模型经过训练，知道通过这个约定可以**结构化地**调用本地工具。

整个过程是一个**协作协议**：

| 角色 | 职责 |
|---|---|
| **大模型**（大脑） | 思考、决策、生成工具调用指令 |
| **本地 Agent + 工具**（手脚） | 解析指令、执行工具、返回结果 |

大模型告诉 Agent「调哪个函数、用什么参数」；Agent 解析后执行真正的函数，再把结果告诉大模型；大模型判断结果是否符合预期。

### 5.4 Tool ID 的作用

API 协议要求每次工具调用带 `tool_use_id`。它的价值：

- **精确定位**：大模型可能一次性并行调用多个工具，或多次调用同名工具。
- 通过 ID 可以**精确匹配**每次调用与对应的结果，避免歧义。

---

## 6. 总结和展望

### Agent 的本质

Agent 是一个执行的过程，它：

1. 可以**调用本地的工具**（行动）
2. 可以**通过工具感知**本地有什么、能获取到什么（感知）
3. **依赖大模型的大脑**告诉它下一步怎么做（决策）

**感知 / 行动 / 决策** —— 这三个东西合起来就是 **ReAct**，一直在循环中不断迭代，直到大模型判断任务完成。

> 思考：大模型去思考 → 本地 Agent 去行动 → Agent 拿到结果（观察）→ 把整个 messages 提供给大模型去思考 → ...

不管是什么大模型、不管是什么 Agent（Claude Code / Cursor / Devin），**底层都是这个循环**。你看到它们自动搜索代码、修改文件、执行命令，背后都是这样一个循环在驱动。

本 demo 用最少的代码，把这套原理完整展现了出来 —— 非常简洁、非常精致。你可以自己动手执行最简单的 demo，给它一个简单任务，观察它展现的每一步决策和行动、以及与大模型的交互。

### demo1 的局限（demo2+ 的扩展方向）

这个最简 Agent **能干活，但像一条金鱼**：做完搜索任务之后就忘记了。

| 局限 | 说明 | 扩展方向 |
|---|---|---|
| **只有短期记忆** | 只在一个任务循环周期内有记忆，下次任务开始之前的记忆全无 | demo2：在一个 session 内跨任务保留记忆，能看到上一次做了什么、借助之前的结果规划当前任务 |
| **没有规划** | 完全靠大模型当下指令，无法提前把任务分解成多步骤 | 把任务预先分解成多段落，逐步执行（部分可并行、部分串行，提升效率）；同时更好约束大模型的行为方向 |
| **工具硬编码** | 想加一个新工具必须改代码、改说明书 | 工具插件化、动态注册 |
| **无行为约束** | 想执行什么就执行什么（比如 `rm -rf /` 都行） | 加白名单 / 审批 / 沙箱 |

底层最基本的能力，demo1 已经完全实现了。后续扩展都会从这个方向去演进。

---

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单见 `requirements.txt`（仅 `anthropic` SDK）。

### 配置 API Key（两种方式，任选其一）

网关、模型、超时已在代码里写死，**只需配置 API Key**：

```python
# agent.py Part 1
API_KEY         = ""                                         # ← 只改这一行
BASE_URL        = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel
MODEL           = "glm-5.2[1m]"
API_TIMEOUT_MS  = 3000000                                    # 50 分钟
```

**方式 1：改代码（最简单）**

打开 `agent.py`，把 Part 1 顶部的 `API_KEY = ""` 改成你的 Key：

```python
API_KEY = "sk-your-key-here"
```

**方式 2：首次运行交互式提示**

代码里 `API_KEY` 为空时直接运行 `python agent.py`，会提示你输入（仅本次运行有效，不持久化）：

```
============================================================
检测到尚未配置 API Key，请输入（仅本次运行有效）
如需持久化：请改 agent.py 顶部的 API_KEY 变量
============================================================

请输入 API Key: _
```

> 也支持用 `ANTHROPIC_API_KEY` 环境变量临时覆盖（优先级：环境变量 > 代码变量）。

### 启动 Agent

```bash
python agent.py
```

进入交互模式后，输入任意任务（如「统计当前目录下有多少个 Python 文件，并把结果写入 count.txt」、「读 README.md 并总结要点」等），观察每一轮 ReAct 循环的决策、行动、感知。输入 `quit` / `exit` 退出。

> **注**：`verbose=True` 默认开启，打印每一轮的完整决策与工具调用，便于教学观察。
> 第 4 章「示例解读循环的运行时序」里的「统计 .py 文件」三轮流示，是教学讲解用的**经典示例**——实际运行时你可以用任意任务，循环时序会按同样规律演化。
