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

## 四、如何扩展？

### 4.1 添加新工具

只需两步：

**1. 在 definitions.py 添加定义**
```python
TOOLS.append({
    "name": "search_web",
    "description": "搜索互联网",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"}
        },
        "required": ["query"]
    }
})
```

**2. 在 executor.py 添加实现**
```python
def search_web(query: str) -> str:
    # 实现搜索逻辑
    return "搜索结果..."

def execute_tool(name: str, params: dict) -> str:
    # 添加分发逻辑
    if name == "search_web":
        return search_web(params["query"])
```

### 4.2 支持更多 LLM

创建新的 Client 类，实现相同的接口：

```python
class OpenAIClient:
    def chat(self, messages, tools):
        # 适配 OpenAI API
        pass
```

### 4.3 增强功能方向

| 功能 | 说明 |
|------|------|
| 流式输出 | 实时显示 LLM 响应 |
| 多模态 | 支持图片输入 |
| 记忆系统 | 持久化对话历史 |
| 任务规划 | 复杂任务分解 |
| 人工确认 | 敏感操作前确认 |
