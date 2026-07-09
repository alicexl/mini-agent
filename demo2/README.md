# Demo2 — 记忆与规划

> 目标：在 demo1（LLM × 工具 × 循环）基础上增加 **记忆** 和 **规划** 两个能力。
> 让 Agent 记得过去、规划未来，不再像金鱼。

本文件配套 `agent.py`（单文件实现）使用，按照教学音频整理为 7 章。

---

## 1. 结论：demo2 vs demo1

**demo1 的两个遗憾：**

1. **金鱼记忆** —— 任务结束 messages 全清空，下次启动什么都不记得
2. **走一步看一步** —— 整个任务丢给 LLM，没有全局规划，复杂任务容易迷失

**demo2 的解法：**

| 维度 | demo1 | demo2 |
|---|---|---|
| **工具集** | execute_bash / read_file / write_file | **不变** |
| **任务执行** | 单步 ReAct 循环 | 单步 ReAct **不变**（不考虑并发） |
| **跨任务记忆** | ❌ 任务结束 messages 清空 | ✅ `agent_memory.md` 文件持久化 |
| **任务规划** | ❌ 整个任务丢给 LLM | ✅ Plan 模式先拆 3-5 步再执行 |
| **多步上下文** | ❌ messages 是 task 内部局部变量 | ✅ messages 在 step 之间共享 |

**新增的两个能力 × 一个结构调整：**

1. **记忆系统** —— 用 Markdown 文件做跨任务长期记忆，滑动窗口加载
2. **规划系统** —— 让大模型先把任务拆成多个 step，再逐步执行
3. **messages 共享** —— 从 task 内部局部变量变成 step 之间共享的上下文

> **demo2 = demo1 × 记忆 × 规划**

---

## 2. 全局架构

```
┌──────────────────────────────────────────────────────┐
│  Part 1: LLM 客户端初始化（与 demo1 一致）              │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 2: 工具定义（与 demo1 一致，三个工具不变）         │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 3: 工具实现 + 路由表（与 demo1 一致）              │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 4: 记忆系统（新增）                               │
│   ─ agent_memory.md 文件持久化                          │
│   ─ 滑动窗口（最后 50 行）作为 Progressive Context       │
│   ─ append_memory / load_memory / build_system_prompt  │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 5: 规划系统（新增）                               │
│   ─ get_plan: 让大模型用 submit_plan 工具拆任务         │
│   ─ 防御性降级: 异常或解析失败 → 单步执行               │
└──────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────┐
│  Part 6: Agent 主循环（重构）                           │
│   ─ run_agent_step: 单个 step 的 ReAct 循环            │
│       (messages 外部传入/传出)                          │
│   ─ run_agent: 编排层（可选规划 + 多步串联 + 写记忆）    │
└──────────────────────────────────────────────────────┘
```

---

## 3. 记忆系统

### 3.1 记忆的本质

大模型有上下文窗口限制，**没有真正的持久记忆**。记忆 = 把外部存储的信息有选择地搬运进 prompt。

| 角色 | 能力 | 限制 |
|---|---|---|
| **大模型** | 有一定上下文窗口、推理能力 | 窗口有限，无持久记忆 |
| **本地 Agent** | 读写外部存储 | 选择搬运什么、搬多少 |

所有记忆方案的区别都是「**存在哪、怎么存、搬多少**」三个问题的不同答案。

### 3.2 存储方案：Markdown 文件

```python
MEMORY_FILE = "agent_memory.md"

def append_memory(task: str, result: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result_preview = result[:500]  # 限长防膨胀
    entry = (
        f"\n## [{timestamp}]\n"
        f"**任务**: {task}\n"
        f"**结果**: {result_preview}\n"
    )
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)
```

**记忆文件示例：**

```markdown
## [2026-06-29 14:23:11]
**任务**: 统计当前目录下有多少个 Python 文件
**结果**: 已统计完成，当前目录下共有 12 个 Python文件...

## [2026-06-29 14:25:42]
**任务**: 创建一个 hello world 的 py 文件
**结果**: 已创建 hello.py，内容为 print("Hello, World!")
```

每次任务结束追加：**时间 + 任务 + 结果摘要**。

### 3.3 加载方案：滑动窗口

```python
MEMORY_WINDOW_LINES = 50  # 滑动窗口

def load_memory() -> str:
    if not os.path.exists(MEMORY_FILE):
        return ""
    with open(MEMORY_FILE, encoding="utf-8") as f:
        lines = f.readlines()
    return "".join(lines[-MEMORY_WINDOW_LINES:])
```

**为什么不全部加载？** 一个 Agent 跑几十次任务，文件会越长越大，最终撑爆大模型上下文窗口。

**滑动窗口** = 窗口大小固定（50 行），旧记忆随新记忆累积被挤出窗口。

50 行是演示用的占位值。真实场景取决于单条记忆大小和大模型上下文剩余空间。**重要的是「限制 + 截取」的模式本身**。

### 3.4 Progressive Context：注入 System Prompt

```python
def build_system_prompt() -> str:
    memory = load_memory()
    if not memory.strip():
        return SYSTEM_PROMPT_BASE
    return SYSTEM_PROMPT_BASE + "\n\n## 历史任务记忆（最近）\n\n" + memory
```

System Prompt 分两层：

```
┌─────────────────────────────────────────────┐
│  System Prompt                              │
│  ┌───────────────────────────────────────┐  │
│  │  基础 Prompt（不变）                    │  │
│  └───────────────────────────────────────┘  │
│  ┌───────────────────────────────────────┐  │
│  │  Progressive Context（动态）            │  │  ← demo2 新增
│  │  最近 50 行历史任务记忆                  │  │
│  └───────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

**重要设计：System Prompt 在整个 task 内构建一次不变。** 任务进行中不重读记忆文件——因为任务还没结束，记忆不会更新。只有任务结束 → `append_memory` → 下个任务开始才会读到新记忆。

### 3.5 记忆方案揭示的原理

不管用什么方案，底层都是「**存在哪、怎么存、搬多少**」：

| 方案 | 存在哪 | 怎么存 | 搬多少 |
|---|---|---|---|
| **demo2（演示）** | Markdown | 追加 | 最后 50 行 |
| **向量数据库** | Chroma / Pinecone | 嵌入向量 | 语义相关 top-K |
| **记忆压缩** | Markdown / SQLite | 大模型压缩 | 关键事实摘要 |
| **Memory 工具** | 外部存储 | Agent 自主存取 | 按需检索 |
| **分层记忆** | 多个文件 | 系统/项目/用户级 | 按层级叠加 |

---

## 4. 规划系统

### 4.1 为什么需要规划

demo1 把整个任务丢给 LLM 让它一步步摸索。简单任务没问题，复杂任务有**三大风险**：

| 风险 | 描述 |
|---|---|
| **迷失细节** | 任务太大，大模型陷入局部细节 |
| **走偏方向** | 完成局部目标但偏离整体目标 |
| **迟迟不收敛** | 没有全局视角，可能死循环或达不到预期 |

demo2 引入可选规划阶段：让大模型**先有全局视角**，把任务拆成清晰步骤，再逐步执行。

### 4.2 get_plan：让大模型拆任务

规划本身是**一次独立的大模型调用**——用专用 System Prompt 和专用工具：

```python
PLAN_TOOL = {
    "name": "submit_plan",
    "description": "提交任务执行规划",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": 10,
            }
        },
        "required": ["steps"],
    },
}

PLAN_SYSTEM_PROMPT = """你是任务规划助手。把用户任务拆成 3-5 个有序步骤。
必须通过 submit_plan 工具返回规划，不要直接输出文本。"""

def get_plan(user_input: str) -> list:
    try:
        response = client.messages.create(
            model=MODEL,
            system=PLAN_SYSTEM_PROMPT,
            tools=[PLAN_TOOL],   # ← 只暴露规划工具
            messages=[{"role": "user", "content": user_input}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_plan":
                steps = block.input.get("steps", [])
                if isinstance(steps, list) and 1 <= len(steps) <= 10:
                    return steps
        return [user_input]   # ← 未调工具 → 降级
    except Exception:
        return [user_input]   # ← 异常 → 降级
```

**三个设计点：**

| 设计点 | 为什么 |
|---|---|
| **专用 System Prompt** | 规划阶段角色是「规划助手」，不是「执行助手」 |
| **结构化返回** | Function Calling 强制 json 返回，避免自由文本解析 |
| **防御性降级** | 大模型不可靠，任何异常都退回 demo1 单步模式 |

**防御性编程是大模型应用必备**——大模型调用是不可靠的外部依赖，必须有兜底。

### 4.3 两种执行范式

| 维度 | 直接 ReAct（demo1） | Plan 模式（demo2） |
|---|---|---|
| **流程** | task → ReAct 循环 | task → **规划** → step1 ReAct → step2 ReAct → ... |
| **视角** | 局部，走一步看一步 | 先全局，再逐步执行 |
| **优势** | 简单灵活 | 不容易迷失 |
| **劣势** | 复杂任务容易走偏 | 规划可能不准，多一次 LLM 调用 |
| **适用** | 简单任务 | 复杂任务 |

**Plan 模式不是万能的**——大模型拆出来的步骤不一定准确。但**有全局视角，总比走一步看一步强**。

---

## 5. 多步执行：Message 的演化

### 5.1 demo1 的结构

```python
def run_agent(user_input: str) -> str:
    messages = [{"role": "user", "content": user_input}]   # ← 局部变量
    for loop_idx in range(1, MAX_ITERATIONS + 1):
        ...
    return final_text   # ← messages 丢弃
```

`messages` 是 `run_agent` 的局部变量，task 结束丢弃。demo1 一个 task 就是一个完整 ReAct 循环，没问题。

### 5.2 demo2 的结构

一个 task 被拆成多个 step，**messages 必须在 step 之间共享**：

```python
def run_agent_step(
    step: str,
    messages: list,         # ← 外部传入
    system_prompt: str,
) -> tuple:                 # ← 返回 (result, messages)
    messages = messages + [{"role": "user", "content": step}]
    for loop_idx in range(1, MAX_ITERATIONS + 1):
        response = client.messages.create(
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            result = extract_text(response.content)
            return result, messages   # ← 累积的 messages 带出去
        messages.append({"role": "assistant", "content": response.content})
        ...
        messages.append({"role": "user", "content": tool_results})
```

**三个结构变化：**

| 变化 | demo1 | demo2 |
|---|---|---|
| **messages 来源** | 函数内部新建 | 外部传入 |
| **messages 去向** | 函数内丢弃 | 通过返回值传出 |
| **返回值** | `str` | `tuple[str, list]` |

### 5.3 编排层

```python
def run_agent(user_input: str, use_plan: bool = False) -> str:
    system_prompt = build_system_prompt()
    steps = get_plan(user_input) if use_plan else [user_input]

    messages = []                            # ← task 级共享上下文
    final_result = ""
    for step in steps:
        final_result, messages = run_agent_step(
            step=step,
            messages=messages,               # ← 接力
            system_prompt=system_prompt,
        )
    append_memory(user_input, final_result)
    return final_result
```

### 5.4 三层记忆（重要区分）

| 层级 | 范围 | 载体 | 生命周期 |
|---|---|---|---|
| **step 内消息** | 单次 step | `messages` 列表 | step 结束仍保留 |
| **task 级共享上下文** | 多个 step 之间 | 同一个 `messages` 列表 | task 结束丢弃 |
| **跨 task 长期记忆** | 跨 task、跨会话 | `agent_memory.md` 文件 | 持久（滑动窗口） |

**task 级共享上下文 ≠ 跨 task 长期记忆**：
- 前者是 step1/step2/step3 共享的 `messages`，task 结束丢弃
- 后者是所有 task 结束后写入 `agent_memory.md`，下次启动能读

---

## 6. 示例解读：找所有 todo 整理到 todo.md

任务：**「找到代码里所有的 todo，整理到 todo.md 文件中」**，用 `python agent.py --plan` 启动。

### 第 0 阶段：构建带记忆的 System Prompt

读 `agent_memory.md` 最后 50 行作为 Progressive Context。首次运行为空。

### 第 1 阶段：规划

```
[规划] 拆解出 3 个步骤:
   Step 1: 用 grep 递归搜索代码里所有的 todo 注释
   Step 2: 把搜索结果按文件整理成清单
   Step 3: 把清单写入 todo.md
```

### 第 2 阶段：Step 1 — 递归搜索

```
Step 1 ReAct:
  Loop 1: user → LLM → tool_use(grep) → tool_result(找到 todo)
  Loop 2: LLM → end_turn（step 完成）

messages 累积 3 条: [user, assistant(tool_use), user(tool_result)]
```

### 第 3 阶段：Step 2 — 整理清单（无需工具）

```
Step 2 ReAct:
  Loop 1: user → LLM → end_turn（直接整理，没调工具）

关键：LLM 从 step1 留下的 messages 里就能整理，不用重新搜索
```

### 第 4 阶段：Step 3 — 写入 todo.md

```
Step 3 ReAct:
  Loop 1: user → LLM → tool_use(write_file) → tool_result(成功)
  Loop 2: LLM → end_turn（step 完成）
```

### 第 5 阶段：写入长期记忆

```markdown
## [2026-06-29 14:30:15]
**任务**: 找到代码里所有的 todo，整理到 todo.md 文件中
**结果**: 已找到 3 个 todo，整理后写入 todo.md
```

### 整个任务的 messages 演化

```
任务开始: messages = []
   ↓
[规划]   → steps = [搜索, 整理, 写入]
   ↓
Step 1:  messages 累积到 3 条
   ↓
Step 2:  messages 累积到 5 条（不调工具，直接从历史整理）
   ↓
Step 3:  messages 累积到 9 条
   ↓
[task 结束] → 写入 agent_memory.md
   ↓
         messages 丢弃（记忆已落盘）
```

**核心价值：信息在 step 之间累积，避免重复劳动。** step2 没调工具，是因为大模型从 step1 留下的历史里就能整理。

---

## 7. 局限与演进方向

### 7.1 长期记忆会撑爆窗口

滑动窗口（50 行）可能太少（丢重要信息）或太多（单条记忆长）。

| 方向 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **向量数据库** | Chroma / Pinecone，语义检索 top-K | 智能检索 | 复杂；可能漏掉不相似但重要的细节 |
| **记忆压缩** | 接近上限时调大模型压缩 | 自适应窗口 | 多一次调用；压缩可能丢信息 |
| **Memory 工具** | 给 Agent 一个 search_memory 工具 | 按需取用 | 需学会何时用 |
| **分层记忆** | 系统/项目/用户级（Claude Code 风格） | 减少重复 | 多文件管理 |

### 7.2 规划质量不可控

演进方向：
- **规划校验**：拆完后让另一个大模型审视、修正
- **失败重规划**：某 step 失败后重新规划剩余部分
- **技能化**（Skills）：把常见任务的规划预先固化 → demo3 主题

### 7.3 自动判断"是否需要规划"

目前 Plan 模式靠用户手动开启。演进方向：加轻量分类器，让 Agent 自动判断任务复杂度。

---

## 8. 下一节预告：MCP / Rules / Skills

| 能力 | demo1 | demo2 |
|---|---|---|
| LLM × 工具 × 循环 | ✅ | ✅ |
| 跨任务长期记忆 | ❌ | ✅ |
| 规划 + 多步串联 | ❌ | ✅ |

**三个未解决问题 → demo3 三个主题：**

| # | 未解决问题 | demo3 主题 |
|---|---|---|
| 1 | **工具硬编码** —— 想接外部能力怎么办？ | **MCP**：标准化接入外部服务的协议 |
| 2 | **无行为约束** —— 想执行什么就执行什么 | **Rules**：行为规范、命名/审批等约束 |
| 3 | **规划靠手动触发** —— Agent 不知道何时该规划 | **Skills**：把规划能力预先固化 |

> **demo2 = demo1 × 记忆 × 规划**
> **demo3 = demo2 × MCP × Rules × Skills**

---

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单见 `requirements.txt`（仅 `anthropic` SDK）。

### 配置 API Key

网关、模型、超时已在代码里写死，**只需配置 API Key**：

```python
# agent.py Part 1
API_KEY         = ""                                         # ← 只改这一行
BASE_URL        = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel
MODEL           = "glm-5.2"
API_TIMEOUT_MS  = 3000000                                    # 50 分钟
```

**方式 1：改代码（最简单）**

打开 `agent.py`，把 Part 1 顶部的 `API_KEY = ""` 改成你的 Key。

**方式 2：首次运行交互式提示**

`API_KEY` 为空时直接运行 `python agent.py`，会提示输入（仅本次运行有效，不持久化）。

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖。

### 启动 Agent

```bash
python agent.py            # 直接 ReAct 模式（demo1 风格）
python agent.py --plan     # Plan 模式（先规划再执行）
```

进入交互模式后输入任意任务。**REPL 内可随时切换：**

| 命令 | 作用 |
|---|---|
| `/plan` 或 `/p` | 切换 Plan 模式（开↔关） |
| `/no-plan` 或 `/np` | 关闭 Plan 模式 |
| `/memory` 或 `/m` | 查看当前记忆文件内容 |
| `quit` / `exit` / `q` | 退出 |

> **注**：`verbose=True` 默认开启，打印每个 step 的完整决策与工具调用。
> 运行时会在当前目录生成 `agent_memory.md`（已加入 `.gitignore`），每个用户的记忆不同，不应提交。
