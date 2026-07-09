# Demo6 — 上下文压缩（Context Compression）

> 教学讲稿见 `讲稿.md`，本文件是技术参考文档。

## 一、Demo6 在系列中的位置

| Demo | 主题 | 关键能力 |
|---|---|---|
| demo1 | LLM × 工具 × 循环 | ReAct、本地工具 |
| demo2 | 记忆 × 规划 | agent_memory.md、独立 plan 命令 |
| demo3 | Rules × MCP | 行为约束、JSON-RPC 远程工具、plan 自动决策 |
| demo4 | Subagent 分工 | 主 Agent 可派生一次性独立 Subagent |
| demo5 | Team 协作 + 事件驱动 | 持久 Agent + 状态机调度 + 质检员持续监听 |
| **demo6** | **上下文压缩** | **`compact_messages` 动态压缩对话历史，让多 step 任务不撞顶** |

demo6 与 demo1–5 的方向**反过来**——前 5 个 demo 都在「加能力」，demo6 转而解决「加能力带来的副作用」：**多轮 ReAct 会让 messages 越攒越多，撞顶即崩**。

## 二、核心问题：messages 为什么会爆

每一轮 ReAct 至少产生 2 条消息：

```
[assistant] tool_use (大模型决定调工具)         ← +1 条
[user]      tool_result (本地/MCP 执行结果)     ← +1 条
```

一个 5-step 的 Plan 任务，每步 2 轮工具调用 = 20 条；再加上 system prompt 注入、rules、plan tool_result 等，轻松破 30 条。任何大模型的 context window 都有上限，撞顶即崩。

## 三、四个解决思路

| 方案 | 思路 | 优缺点 |
|---|---|---|
| ① 扩窗口 | 换更大 context 的模型（如 GLM-5.2 的 1M） | ✅ 零成本 ❌ 治标不治本，超长任务仍会撞顶 |
| ② 限循环 | 限制 step 次数，爆了就重启 | ✅ 实现简单 ❌ 粗暴，失败重试代价高 |
| ③ 阶段化 | 只保留最近 N 条（demo2 滑动窗口思想） | ✅ 实现简单 ❌ 丢弃关键信息，大模型"失忆" |
| ④ **压缩** | 旧消息让大模型做成摘要，保留最近几条原文 | ✅ 保留要点 ❌ 多一次 LLM 调用，可能丢细节 |

**demo6 选方案 ④**——它是质量与成本的最佳平衡点，也是生产级 Agent（如 Claude Code）的核心做法。

## 四、实现：`compact_messages`

### 4.1 压缩参数（演示调低，生产请调大）

```python
COMPACT_THRESHOLD = 8    # messages 达到 8 条触发压缩
KEEP_RECENT       = 4    # 保留最近 4 条不动
```

### 4.2 压缩流程

```
触发条件：len(messages) ≥ COMPACT_THRESHOLD
                    │
                    ▼
┌─────────────────────────────────────────────┐
│ messages = [                                │
│   (0) user: 首条任务                        │  ← 旧消息区
│   (1) assistant: tool_use                   │
│   (2) user: tool_result                     │
│   ...                                       │
│   (n-keep-1) ...                            │  ← 理想切点
├─ ─ ─ ─ 安全边界调整 ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┤
│   (n-keep) ...                              │  ← 最近区（保留）
│   ...                                       │
│   (n-1) user: 最新一条                      │
│ ]                                           │
└─────────────────────────────────────────────┘
                    │
                    ▼
旧消息 → _summarize() → summary 文本
                    │
                    ▼
重组后的 messages = [
  user: "[对话历史摘要] ..."    ← 摘要
  assistant: "好的，我已读取..." ← 过渡消息
  ... (最近 KEEP_RECENT 条原文)
]
```

### 4.3 安全边界（safe boundary）

切点不能落在 `tool_use` / `tool_result` 对子中间——Anthropic API 要求二者紧邻，切断会直接报错。

`_find_safe_boundary` 从理想切点向前回溯，跳过两类危险位置：
- **以 `user(tool_result)` 结尾**：它的 `assistant(tool_use)` 配对被切走会孤立
- **以 `assistant(tool_use)` 结尾**：它的 `tool_result` 在最近区，前半孤立

### 4.4 过渡消息的作用

重组后直接把摘要当首条 user 消息会让大模型疑惑（"我怎么突然凭空有了一段历史？"）。所以在摘要后插一条 `assistant` 文本回应，伪装成正常的对话衔接：

```python
{"role": "user",      "content": "[对话历史摘要]\n..."},
{"role": "assistant", "content": "好的，我已读取历史摘要，了解了之前的进展。请给我最新的任务..."},
```

## 五、在 Plan step 循环中触发压缩

`run_agent` 遍历 plan 的 steps 时，**每个 step 开头先调 `compact_messages`**：

```python
for i, step in enumerate(steps, 1):
    messages = compact_messages(messages, verbose=verbose)  # ★ 核心钩子
    messages.append({"role": "user", "content": step_msg})
    run_agent_steps(messages, step_tools, ...)
```

这样 5-6 个 step 的任务会在执行中触发 2-3 次压缩，messages 始终维持在阈值附近，不会爆炸。

## 六、demo 实现 vs 生产级实现

| 维度 | demo6（教学版） | 生产级（如 Claude Code） |
|---|---|---|
| **触发指标** | 固定条数（`COMPACT_THRESHOLD=8`） | 基于 token 数，按实际上下文窗口占比触发（如 80%） |
| **压缩粒度** | 一次性把旧消息压成一个 summary | **分层**：最近原文、稍远摘要、更远压得更细 |
| **保留策略** | 最近 N 条 + 安全边界 | 智能选择：关键词、关键决策、文件路径、代码片段等 |
| **prompt 定制** | 通用压缩 prompt | 按场景定制（coding 场景保留路径/决策原因；research 场景保留事实引用等） |
| **失败处理** | 摘要失败直接抛异常 | 重试 + 降级（保留原文） |

## 七、与 demo2 滑动窗口的区别

| 维度 | demo2（滑动窗口 memory） | demo6（压缩） |
|---|---|---|
| 数据载体 | `agent_memory.md` 外部文件 | messages 数组本身 |
| 保留内容 | 任务级摘要（一次任务一条记录） | 对话级摘要（多轮工具调用压缩成一段） |
| 触发时机 | 任务结束时 append | 任务执行中动态触发 |
| 信息来源 | 用户输入 + 最终结果 | 全部 messages（含工具调用细节） |
| 解决的问题 | 跨任务记忆 | 单任务内不撞顶 |

## 八、文件结构

```
demo6/
├── agent.py              ← 主程序（6 个 Part）
├── .agent/
│   └── rules.md          ← 行为规范（注入 system prompt）
├── 讲稿.md               ← 教学讲稿
├── README.md             ← 本文件
├── requirements.txt      ← 依赖（anthropic）
├── demo6.m4a             ← 原始讲解音频
├── demo6_transcript.txt  ← 音频转录
└── transcribe_demo6.py   ← 转录脚本
```

## 九、启动

```bash
# 1. 配置 API Key（改 agent.py 顶部的 API_KEY 变量）
# 2. 启动
python agent.py
# 3. 输入一个多步骤任务，例如：
# > 在 demo6 目录下创建一个 hello.txt 写入"hello demo6"，再读取它，最后告诉我文件大小
```

观察日志中 `[compact]` 开头的行——这就是压缩被触发的时刻。
