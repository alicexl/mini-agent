# Mini Agent 设计思路

## 一、什么是 Agent？

### 1.1 定义

Agent（智能代理）是一个能够**自主感知环境、做出决策并执行动作**的系统。在 AI 领域，Agent 特指能够与 LLM（大语言模型）配合，通过调用工具来完成复杂任务的程序。

### 1.2 核心特征

```
┌─────────────────────────────────────────────────────┐
│                      Agent                          │
│  ┌─────────┐    ┌─────────┐    ┌─────────────┐     │
│  │  感知   │───▶│  决策   │───▶│    执行     │     │
│  │ (输入)  │    │  (LLM)  │    │  (工具调用) │     │
│  └─────────┘    └─────────┘    └─────────────┘     │
│       ▲                              │              │
│       └──────────────────────────────┘              │
│                    反馈循环                          │
└─────────────────────────────────────────────────────┘
```

- **感知**：接收用户输入和工具执行结果
- **决策**：LLM 分析情况，决定下一步行动
- **执行**：调用工具完成具体操作
- **反馈**：将执行结果反馈给 LLM，继续决策

### 1.3 与普通 LLM 对话的区别

| 特性 | 普通 LLM 对话 | Agent |
|------|--------------|-------|
| 能力 | 只能生成文本 | 能执行实际操作 |
| 知识 | 截止训练时间 | 可获取实时信息 |
| 交互 | 单轮或多轮对话 | 自主循环直到完成 |
| 输出 | 文本回复 | 文本 + 工具调用 |

---

## 二、LLM 与 Agent 的交互

### 2.1 工具调用机制

现代 LLM（如 Claude、GPT-4）支持 **Tool Use / Function Calling**，允许模型在回复中请求调用外部工具：

```json
// LLM 响应示例
{
  "stop_reason": "tool_use",
  "content": [
    {
      "type": "text",
      "text": "我来帮你读取这个文件"
    },
    {
      "type": "tool_use",
      "name": "read_file",
      "input": {
        "path": "/path/to/file.txt"
      }
    }
  ]
}
```

### 2.2 交互流程

```
用户: "读取 config.json 文件"
        │
        ▼
┌───────────────────┐
│  1. 发送消息给 LLM │
│  (包含工具定义)    │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  2. LLM 决策      │
│  stop_reason:     │
│  "tool_use"       │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  3. 执行工具      │
│  read_file()      │
│  → 返回文件内容   │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  4. 发送工具结果  │
│  给 LLM           │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│  5. LLM 生成回复  │
│  stop_reason:     │
│  "end_turn"       │
└─────────┬─────────┘
          │
          ▼
用户: "文件内容是..."
```

### 2.3 关键概念

| 概念 | 说明 |
|------|------|
| **Tool Definition** | 告诉 LLM 有哪些工具可用，包括名称、描述、参数 schema |
| **Tool Use** | LLM 请求调用工具，包含工具名和参数 |
| **Tool Result** | 工具执行的结果，需要返回给 LLM |
| **Stop Reason** | LLM 停止生成的原因：`tool_use` 或 `end_turn` |

---

## 三、为什么这样设计？

### 3.1 架构分层

```
┌─────────────────────────────────────────┐
│              run.py (入口)              │  用户交互层
├─────────────────────────────────────────┤
│              agent.py                   │  核心循环层
├─────────────────────────────────────────┤
│     llm_client.py    │    tools/        │  能力层
│     (LLM 封装)       │  (工具实现)      │
└─────────────────────────────────────────┘
```

**分层的好处**：
- 每层职责单一，易于理解和修改
- 可以单独替换 LLM 或工具，不影响其他部分
- 方便测试

### 3.2 循环设计

```python
while True:
    response = llm.chat(messages, tools)

    if response.stop_reason == "tool_use":
        # 执行工具，继续循环
        results = execute_tools(response)
        messages.append(results)

    elif response.stop_reason == "end_turn":
        # 任务完成，退出循环
        return response.text
```

**为什么用循环**：
- LLM 可能需要多次调用工具才能完成任务
- 例如："读取 A，然后根据 A 的内容修改 B"
- 循环让 Agent 能自主完成多步骤任务

### 3.3 消息历史

```python
messages = [
    {"role": "user", "content": "读取 config.txt"},
    {"role": "assistant", "content": [tool_use_block]},
    {"role": "user", "content": [tool_result_block]},
    {"role": "assistant", "content": "文件内容是..."}
]
```

**保留历史的原因**：
- LLM 是无状态的，需要通过消息历史理解上下文
- 工具调用和结果需要关联（通过 tool_use_id）
- 支持多轮对话

---

## 四、源码解读

本节逐层解读项目源码，从底层到上层依次讲解。

### 4.1 LLM 客户端 (`src/llm_client.py`)

LLM 客户端是对 Anthropic API 的封装，提供统一的调用接口。

```python
import os
from anthropic import Anthropic


class LLMClient:
    """LLM 客户端，封装 API 调用"""

    def __init__(self, api_key: str = None, model: str = "claude-sonnet-4-6"):
        """
        初始化 LLM 客户端

        Args:
            api_key: Anthropic API Key，如果为空则从环境变量读取
            model: 模型名称
        """
        # 优先使用传入的 api_key，否则从环境变量读取
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("请设置 ANTHROPIC_API_KEY 环境变量或传入 api_key 参数")

        # 初始化 Anthropic 客户端
        self.client = Anthropic(api_key=self.api_key)
        self.model = model
```

**要点**：
- `api_key` 支持两种方式传入：参数或环境变量
- 环境变量名 `ANTHROPIC_API_KEY` 是约定俗成的
- 默认使用 `claude-sonnet-4-6` 模型，性价比高

```python
    def chat(self, messages: list, tools: list = None, system: str = None) -> object:
        """
        发送消息，支持 tool_use

        Args:
            messages: 消息列表
            tools: 工具定义列表
            system: 系统提示

        Returns:
            API 响应对象
        """
        # 构建请求参数
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4096,
        }

        # 可选参数：只有提供了才添加
        if tools:
            kwargs["tools"] = tools
        if system:
            kwargs["system"] = system

        # 调用 API
        return self.client.messages.create(**kwargs)
```

**要点**：
- `messages` 是对话历史，包含 user/assistant 消息
- `tools` 是工具定义列表，让 LLM 知道有哪些工具可用
- `system` 是系统提示，定义 Agent 的角色和行为
- `max_tokens` 限制响应长度

**响应对象结构**：
```python
response = {
    "id": "msg_xxx",
    "stop_reason": "tool_use" | "end_turn",
    "content": [
        {"type": "text", "text": "..."},
        {"type": "tool_use", "name": "read_file", "input": {...}, "id": "toolu_xxx"}
    ]
}
```

---

### 4.2 工具定义 (`src/tools/definitions.py`)

工具定义告诉 LLM 有哪些工具可用，以及如何使用它们。

```python
TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可以用于文件操作、系统命令等",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令"
                }
            },
            "required": ["command"]
        }
    },
    # ... 其他工具定义
]
```

**工具定义的三个要素**：

| 字段 | 说明 | 重要性 |
|------|------|--------|
| `name` | 工具名称，LLM 调用时使用 | 必须唯一 |
| `description` | 工具描述，LLM 理解工具用途 | 决定 LLM 何时调用 |
| `input_schema` | 参数的 JSON Schema | 决定 LLM 如何传参 |

**input_schema 详解**：

```python
"input_schema": {
    "type": "object",                    # 固定为 object
    "properties": {                      # 定义所有参数
        "path": {
            "type": "string",            # 参数类型
            "description": "文件路径"    # 参数描述（重要！）
        },
        "content": {
            "type": "string",
            "description": "文件内容"
        }
    },
    "required": ["path", "content"]      # 必填参数
}
```

**为什么 description 很重要**：
- LLM 只能通过 description 理解参数含义
- 描述越清晰，LLM 传参越准确
- 可以包含示例、注意事项等

**read_file 定义示例**：
```python
{
    "name": "read_file",
    "description": "读取文件内容，返回文件的文本内容",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要读取的文件路径"
            }
        },
        "required": ["path"]
    }
}
```

---

### 4.3 工具实现 (`src/tools/executor.py`)

工具实现是实际执行操作的代码。

**入口函数 - 工具分发**：

```python
def execute_tool(name: str, params: dict) -> str:
    """
    执行工具并返回结果

    Args:
        name: 工具名称
        params: 工具参数

    Returns:
        工具执行结果（字符串形式）
    """
    if name == "execute_bash":
        return execute_bash(params["command"])
    elif name == "read_file":
        return read_file(params["path"])
    elif name == "write_file":
        return write_file(params["path"], params["content"])
    else:
        return f"错误：未知工具 '{name}'"
```

**要点**：
- 这是一个分发函数，根据工具名调用对应的实现
- 返回值必须是字符串（LLM 只能理解文本）
- 未知工具返回错误信息（而不是抛异常）

**execute_bash 实现**：

```python
def execute_bash(command: str) -> str:
    """执行 shell 命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,           # 通过 shell 执行（支持管道等）
            capture_output=True,  # 捕获 stdout 和 stderr
            text=True,            # 返回字符串而不是 bytes
            timeout=60            # 超时限制
        )

        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(f"[stderr] {result.stderr}")

        if result.returncode != 0:
            output.append(f"[exit code: {result.returncode}]")

        return "\n".join(output) if output else "[命令执行成功，无输出]"

    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时（60秒）"
    except Exception as e:
        return f"[错误] 命令执行失败: {str(e)}"
```

**要点**：
- `shell=True` 让命令支持管道、通配符等 shell 特性
- 同时返回 stdout 和 stderr，让 LLM 能看到完整信息
- 超时保护，避免长时间运行的命令卡住
- 所有异常都转换为字符串返回，不抛出

**read_file 实现**：

```python
def read_file(path: str) -> str:
    """读取文件内容"""
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 限制返回内容长度（避免响应过大）
        max_length = 10000
        if len(content) > max_length:
            content = content[:max_length] + f"\n\n... [内容已截断，共 {len(content)} 字符]"

        return content

    except UnicodeDecodeError:
        return "[错误] 文件不是有效的文本文件或编码不支持"
    except Exception as e:
        return f"[错误] 读取文件失败: {str(e)}"
```

**要点**：
- 先检查文件是否存在，给出清晰的错误信息
- 限制返回长度，避免大文件导致响应过大
- 处理编码问题，非 UTF-8 文件会报错

**write_file 实现**：

```python
def write_file(path: str, content: str) -> str:
    """写入文件内容"""
    try:
        # 自动创建父目录
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        return f"[成功] 文件已写入: {path} ({len(content)} 字符)"

    except Exception as e:
        return f"[错误] 写入文件失败: {str(e)}"
```

**要点**：
- 自动创建父目录，提升易用性
- 返回操作确认信息，让 LLM 知道操作结果

---

### 4.4 Agent 循环 (`src/agent.py`)

Agent 是核心，负责协调 LLM 和工具。

**系统提示**：

```python
SYSTEM_PROMPT = """你是一个有用的 AI 助手，可以通过工具来帮助用户完成任务。

你有以下工具可以使用：
1. execute_bash: 执行 shell 命令
2. read_file: 读取文件内容
3. write_file: 写入文件内容

请根据用户的需求，选择合适的工具来完成任务。执行完工具后，请总结结果并回复用户。"""
```

**Agent 类结构**：

```python
class Agent:
    """Agent 主类，处理与 LLM 的交互循环"""

    def __init__(self, llm_client: LLMClient):
        """
        初始化 Agent

        Args:
            llm_client: LLM 客户端实例
        """
        self.llm = llm_client
        self.messages = []  # 消息历史
```

**核心循环 - run 方法**：

```python
    def run(self, user_input: str) -> str:
        """运行 Agent，处理用户输入"""

        # 1. 添加用户消息
        self.messages.append({
            "role": "user",
            "content": user_input
        })

        # 2. 主循环
        while True:
            # 调用 LLM
            response = self.llm.chat(
                messages=self.messages,
                tools=TOOLS,
                system=SYSTEM_PROMPT
            )

            # 3. 检查停止原因
            if response.stop_reason == "tool_use":
                # 需要执行工具
                print(f"\n[调用工具...]")

                # 执行工具
                tool_results = self._execute_tools(response.content)

                # 添加 assistant 消息（包含 tool_use）
                self.messages.append({
                    "role": "assistant",
                    "content": response.content
                })

                # 添加 tool_result 消息
                self.messages.append({
                    "role": "user",
                    "content": tool_results
                })

                # 继续循环，让 LLM 处理工具结果

            elif response.stop_reason == "end_turn":
                # 对话结束，返回结果
                return self._extract_text(response.content)

            else:
                # 其他情况（如 max_tokens）
                return self._extract_text(response.content) + f"\n[停止原因: {response.stop_reason}]"
```

**循环流程图**：

```
user_input
    │
    ▼
┌──────────────────────┐
│ messages.append(user)│
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   llm.chat(messages) │◄─────────────────┐
└──────────┬───────────┘                  │
           │                              │
           ▼                              │
┌──────────────────────┐                  │
│   stop_reason?       │                  │
└──────────┬───────────┘                  │
           │                              │
     ┌─────┴─────┐                        │
     │           │                        │
  tool_use   end_turn                     │
     │           │                        │
     ▼           ▼                        │
┌─────────┐  ┌─────────┐                  │
│执行工具 │  │return   │                  │
└────┬────┘  └─────────┘                  │
     │                                    │
     ▼                                    │
┌──────────────────────┐                  │
│ messages.append(     │                  │
│   assistant + user)  │──────────────────┘
└──────────────────────┘
```

**工具执行 - _execute_tools 方法**：

```python
    def _execute_tools(self, content: list) -> list:
        """执行工具调用"""
        results = []

        for block in content:
            if block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                print(f"  - {tool_name}({tool_input})")

                # 执行工具
                result = execute_tool(tool_name, tool_input)

                # 构造 tool_result
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,  # 关联到对应的 tool_use
                    "content": result
                })

        return results
```

**要点**：
- `tool_use_id` 必须与 `tool_use` 块的 `id` 一致，用于关联
- 一次响应可能包含多个 tool_use，需要逐个执行

**文本提取 - _extract_text 方法**：

```python
    def _extract_text(self, content: list) -> str:
        """从响应内容中提取文本"""
        texts = []
        for block in content:
            if block.type == "text":
                texts.append(block.text)
        return "\n".join(texts)
```

---

### 4.5 完整案例：创建文件

让我们用一个完整案例来理解整个流程。

**用户输入**：
```
创建一个 hello.txt 文件，内容是 Hello World
```

**执行过程**：

```
========== 第 1 轮 ==========

[用户消息]
messages = [
    {"role": "user", "content": "创建一个 hello.txt 文件，内容是 Hello World"}
]

[LLM 响应]
stop_reason: "tool_use"
content: [
    {"type": "text", "text": "我来帮你创建这个文件"},
    {
        "type": "tool_use",
        "name": "write_file",
        "id": "toolu_01",
        "input": {"path": "hello.txt", "content": "Hello World"}
    }
]

[执行工具]
write_file({"path": "hello.txt", "content": "Hello World"})
→ "[成功] 文件已写入: hello.txt (11 字符)"

[更新消息]
messages.append({"role": "assistant", "content": [上面的 content]})
messages.append({
    "role": "user",
    "content": [{
        "type": "tool_result",
        "tool_use_id": "toolu_01",
        "content": "[成功] 文件已写入: hello.txt (11 字符)"
    }]
})

========== 第 2 轮 ==========

[LLM 响应]
stop_reason: "end_turn"
content: [
    {"type": "text", "text": "文件 hello.txt 已成功创建，内容为 \"Hello World\"，共 11 个字符。"}
]

[返回结果]
"文件 hello.txt 已成功创建，内容为 \"Hello World\"，共 11 个字符。"
```

**消息历史最终状态**：

```python
messages = [
    # 第 1 轮 - 用户输入
    {"role": "user", "content": "创建一个 hello.txt 文件，内容是 Hello World"},

    # 第 1 轮 - LLM 响应（包含 tool_use）
    {"role": "assistant", "content": [
        {"type": "text", "text": "我来帮你创建这个文件"},
        {"type": "tool_use", "name": "write_file", "id": "toolu_01", "input": {...}}
    ]},

    # 第 1 轮 - 工具结果
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01", "content": "[成功] ..."}
    ]},

    # 第 2 轮 - LLM 最终回复
    {"role": "assistant", "content": [
        {"type": "text", "text": "文件 hello.txt 已成功创建..."}
    ]}
]
```

**关键理解**：

1. **tool_result 的 role 是 user**：从 LLM 视角，工具结果就像用户的回复
2. **消息历史持续累积**：每轮交互都追加到历史中
3. **LLM 是无状态的**：每次调用都需要完整的历史消息
4. **id 用于关联**：tool_use_id 让 LLM 知道哪个结果对应哪个调用

---

### 4.6 Verbose 模式

Verbose 模式用于教学和调试，可以展示 Agent 与 LLM 交互的完整过程。

**启用方式**：

```bash
# 默认开启 verbose
python run.py "创建一个文件"

# 使用 -q 关闭 verbose
python run.py -q "创建一个文件"
```

**Verbose 模式显示内容**：

```
==================================================
第 1 轮循环
==================================================

[API 请求参数]:                       # 仅第一轮显示完整结构
   system: 你是一个有用的 AI 助手...
   tools: [
     - execute_bash(command)
     - read_file(path)
     - write_file(path, content)
   ]

[messages 对话历史]:
   消息数量: 1
   [0] user: 创建 hello.txt 文件

[LLM 响应]:
   stop_reason: tool_use             # 关键：决定下一步
   content blocks: 2
   [0] text: 我来帮你创建...
   [1] tool_use: write_file({'path': 'hello.txt', ...})

[调用工具...]
  - write_file({'path': 'hello.txt', 'content': 'Hello World'})

[工具执行结果]:
   tool_use_id: call_xxx              # 与 tool_use 关联
   content: [成功] 文件已写入...

==================================================
第 2 轮循环
==================================================

[发送给 LLM 的消息]:
   消息数量: 3                        # 注意：消息在累积
   [0] user: 创建 hello.txt 文件
   [1] assistant: [复杂内容块 x2]     # 上一轮的 tool_use
   [2] user: [复杂内容块 x1]          # tool_result

[LLM 响应]:
   stop_reason: end_turn             # 对话结束
   content blocks: 1
   [0] text: 文件已成功创建...

[对话结束]
```

**代码实现要点**：

```python
class Agent:
    def __init__(self, llm_client, verbose=True):  # 默认开启
        self.verbose = verbose

    def run(self, user_input):
        loop_count = 0
        while True:
            loop_count += 1

            if self.verbose:
                # 第一轮显示 API 请求参数结构
                if loop_count == 1:
                    print("[API 请求参数]:")
                    print(f"   system: {SYSTEM_PROMPT[:50]}...")
                    print("   tools: [")
                    for tool in TOOLS:
                        print(f"     - {tool['name']}(...)")
                    print("   ]")

                # 显示对话历史
                print("[messages 对话历史]: ...")
                print("[LLM 响应]: stop_reason=...")

            # ... 处理逻辑
```

**Verbose 模式的教学价值**：

| 展示内容 | 学习价值 |
|----------|----------|
| API 请求参数结构 | 理解 system/tools/messages 是独立参数 |
| tools 列表 | 理解 LLM 如何"知道"有哪些工具可用 |
| stop_reason | 理解 LLM 的决策（继续 vs 结束）|
| content blocks | 理解响应结构（text + tool_use）|
| messages 累积 | 理解对话历史如何增长 |
| tool_use_id | 理解请求-响应的关联机制 |
