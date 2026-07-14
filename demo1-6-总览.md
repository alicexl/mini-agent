# Demo1–6 总览：从 0 到 1 拆解 Agent 的底层原理

> 本系列用 6 个递进的最简 demo（每个 `agent.py` 在 200–600 行之间）把 Claude Code / Cursor / Devin 这类"自动干活"工具的底层机制完全拆开。
>
> 每个目录下一份 `讲稿.md`（口播+屏显，配合视频讲解）+ 一份 `agent.py`（可直接 `python agent.py` 跑通）。本文件是**顶层索引**——一张图看完 6 个 demo 的能力轴与解决的核心问题。

---

## 一、一张图看完整脉络

```
                    Agent = LLM × 工具 × 循环            （demo1 = base）
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                       ▼
        能力（demo2-5）                          约束（demo6）
            │                                       │
   ┌────────┼────────┐                三层安全栈
   ▼        ▼        ▼                · Permission（allow/deny/ask 规则）
 记忆      工具     规划                · Sandbox（Bash 执行隔离）
（demo2）（demo3）（demo4）              · Hook（PreToolUse/PostToolUse 回调）
            │
            ▼
       多 Agent（demo5）
   Subagent + Team
```

**每个 demo 拆一条正交的能力轴**，公式不链式叠加，而是 `demo(N) = base × 轴N`——base 就是 demo1（ReAct 心跳），后续每个 demo 在 base 上独立加一条轴。读者可以按任意顺序学 demo2-6。

| Demo | 轴 | 一句话公式 | 比喻 | 解决的核心问题 |
|---|---|---|---|---|
| **demo1** | 循环 | `LLM × 工具 × 循环` | 给它**双手** | Agent 最小心跳——ReAct 循环跑通（base） |
| **demo2** | 记忆 | `base × 记忆` | 给它**长期记忆 + 动态压缩** | 任务结束就忘；多轮 ReAct 把 messages 撑爆上下文窗口 |
| **demo3** | 工具 | `base × 工具` | 给它**更多手脚 + 远程工具箱** | 工具只有 read/write/bash 三件套；想接外部协议 |
| **demo4** | 规划 | `base × 规划` | 给它**纸笔 + 套路手册** | 走一步看一步容易跑偏；常见任务每次重新想 |
| **demo5** | 多 Agent | `base × 多 Agent` | 给它**一次性助手 + 项目团队** | 单 Agent 上下文膨胀；不能外包/协作独立子任务 |
| **demo6** | 约束 | `base × 约束` | 给它**手脚的安全防护** | 工具太自由（`rm -rf /`、`dd of=/dev/sda`），且安全逻辑硬编码不可配置 |

---

## 二、轴清单 × 内容矩阵

> 看清每个轴覆盖了什么、哪条轴解决什么问题。

| 轴 | 所属 Demo | 核心机制 |
|---|---|---|
| **ReAct 循环** | demo1 | `messages.append(user) → LLM → tool_use → tool_result → ... → stop_reason="end_turn"` |
| **短期记忆**（messages） | demo2 base | 默认就有；问题在撑爆上下文窗口 |
| **长期记忆**（落盘文件） | demo2 | `agent_memory.md`，跨任务持久化 |
| **上下文压缩** | demo2 | `compact_messages` 滚动摘要，防 messages 撑爆 |
| **Prompt caching** | demo2 | `cache_control` breakpoint + 5/60min TTL，长 prompt 不爆成本 |
| **本地工具扩展** | demo3 | edit（string replacement）/ glob / grep——比 read+write 整文件覆盖更精细 |
| **MCP**（外部工具协议） | demo3 | client-server + JSON-RPC 风格 round-trip，挂外部 server |
| **Plan 模式**（手动 + 自动） | demo4 | 手动开 plan 模式 / LLM 自动决策 plan 与否；TodoWrite 风格 step 列表 |
| **Skill** | demo4 | SKILL.md 预消化的工作流，description 匹配后注入 prompt |
| **Subagent**（一次性） | demo5 | 独立 context、无状态、结束即销毁；适合**相互独立**的子任务 |
| **Team**（持久 + 状态机） | demo5 | 独立累积 messages + inbox 通信 + 状态机驱动；适合**有依赖 + 需通信 + 要质检** |
| **Permission** | demo6 | allow/deny/ask 规则匹配（如 `Bash(rm:*)`），工具调用前的访问控制 |
| **Sandbox** | demo6 | Bash 执行隔离（read-only / write-only / none），危险命令拦截 |
| **Hook** | demo6 | PreToolUse / PostToolUse 事件回调（外部脚本 via JSON in/out + exit code） |

---

## 三、三个核心视角

### 视角 A：轴公式（每条轴独立，不链式叠加）

```
demo1  base = LLM × 工具 × 循环
demo2  = base × 记忆            （短期 messages + 长期文件 + compact + caching）
demo3  = base × 工具            （本地扩展 edit/glob/grep + MCP 外部协议）
demo4  = base × 规划            （手动 Plan + 自动 Plan + Skill 预消化）
demo5  = base × 多 Agent        （Subagent 一次性 + Team 持久 + 状态机）
demo6  = base × 约束            （Permission 规则 + Sandbox 隔离 + Hook 回调）
```

**和旧版「链式叠加」的区别**：旧版 `demo(N) = demo(N-1) × 轴N` 让读者以为每条新轴必须叠加在前一个 demo 上；新版明示「demo1 是 base，demo2-6 各自是 base 上的一条独立轴」。这也意味着读者学完 demo1 后，demo2-6 的学习顺序可以自由调换。

### 视角 B：三种"拆任务"机制对比

| 机制 | 出现的 demo | messages | 适合 |
|---|---|---|---|
| **Plan**（step 列表） | demo4 | 所有 step **共享**一份 | 后续 step 要用前面 step 的结果（有依赖） |
| **Subagent**（一次性） | demo5 `agent_sub.py` | 每个 Subagent **独立**一份，结束即销毁 | 多个**相互独立**的子任务 |
| **Team**（持久 Agent） | demo5 `agent_team.py` | 每个 Agent **独立累积** + inbox | 有依赖 + 需通信 + 多次唤起 + 要质检 |

### 视角 C：能力 vs 约束

- demo1–5 都在**加能力**：循环 → 记忆 → 工具 → 规划 → 多 Agent
- **demo6 是唯一的转弯**——不加能力，而是给 execute_bash / read_file / write_file 这些"手脚"加**三层安全栈**（Permission / Sandbox / Hook）
- 真正的智能体 = 能力与约束的平衡

---

## 四、各 demo 文件清单

| Demo | 入口 | 核心新增文件 | 讲稿 |
|---|---|---|---|
| demo1 | `demo1-react/agent.py` | — | `demo1-react/讲稿.md` |
| demo2 | `demo2-memory/agent.py` | `agent_memory.md`（运行时生成） | `demo2-memory/讲稿.md` |
| demo3 | `demo3-tools/agent.py` + `demo3-tools/mcp_server.py` | — | `demo3-tools/讲稿.md` |
| **demo4** | `demo4-plan/agent.py` | `skills/review.md`（示例 Skill——代码审查工作流） | `demo4-plan/讲稿.md` |
| **demo5** | `demo5-multiagent/agent_sub.py` + `demo5-multiagent/agent_team.py` | —（两份 agent 入口，一份讲稿对照讲） | `demo5-multiagent/讲稿.md` |
| demo6 | `demo6-safety/agent.py` | `hooks/`（hook 脚本目录）+ `_test_guards.py` | `demo6-safety/讲稿.md` |

> 每个目录下还有一份 `README.md`——精简的**设计方案 + 运行说明**（安装/配置/启动命令），深度讲解看 `讲稿.md`。

> demo1 是所有后续 demo 的基线——demo2-6 的 `agent.py` 都从 demo1 的 4 个 Part 扩展而来（Part 1 LLM 客户端 / Part 2 工具 schema / Part 3 工具实现 / Part 4 ReAct 主循环）。

### demo5 的特殊结构

demo5 一个目录下有**两个 agent.py**：

- **`agent_sub.py`**（主线）：Subagent 一次性分工。代码精简（独立 context、无状态、结束即销毁），讲稿权重 70%。对应 Claude Code 的 Task tool / Cursor 的 agent / Devin 的子任务派发。
- **`agent_team.py`**（实战案例）：研究报告生产流水线——Researcher → Writer → Reviewer 三角色 + inbox 通信 + 事件驱动状态机（`researching → writing → reviewing → done`）。讲稿权重 30%，定位为「Subagent 在需要通信/记忆/质检时的升级版」，对应 AutoGen / CrewAI 范式。

讲稿先用 agent_sub.py 演示同一任务的痛点（Subagent 干完结果就丢了），再用 agent_team.py 演示 inbox + 状态机如何解决——同一真实任务上的对照。

---

## 五、推荐学习路径

### 路径 1：按轴顺序（最推荐）

demo1 → demo2 → demo3 → demo4 → demo5 → demo6

demo1 是所有后续 demo 的代码基线。学完 demo1 后，demo2-6 可以按兴趣调整顺序，但建议先记忆（demo2）再工具（demo3）——记忆轴轻量内闭环，工具轴涉及 MCP 协议认知门槛更高。

### 路径 2：按主题

| 想学 | 看这几个 |
|---|---|
| Agent 最小心跳 | demo1 |
| 记忆系统 / 上下文管理 | demo2（短期 + 长期 + 压缩 + caching） |
| 工具扩展 / MCP | demo3 |
| 规划 / Skills | demo4 |
| 多 Agent 系统 | demo5（Subagent + Team 对照） |
| Agent 安全 | demo6（三层安全栈） |

### 路径 3：看真实运行

每个 demo 都有"真实运行回显"——`讲稿.md` 里贴了实测日志（不是虚构），直接看：

- demo1 §4 — 统计 .py 文件数 + 写 count.txt（3 轮 ReAct）
- demo2 §5 — 找 TODO 整理到 todo.md + 演示 compact 触发 + caching 命中
- demo3 §7 — MCP 远程调用 + edit 精细修改对照
- demo4 §6 — Plan 自动决策 + Skill 匹配触发
- demo5 §3 / §4 — Subagent 派发独立任务 / Team 跑通研究报告流水线
- demo6 §7 — Permission 拦截 + Sandbox 隔离 + Hook 回调三连

---

## 六、系列回顾（讲稿交叉引用）

每个 demo 的讲稿结尾都有一张"轴覆盖回顾表"。为了避免表格在 6 个文件里各自漂移，本总览的「轴清单 × 内容矩阵」是**唯一权威表**——如发现任何讲稿里的进度表与本页不一致，以本页为准。

各讲稿结尾的公式也应与上文「视角 A」对齐：

```
demo1: base = LLM × 工具 × 循环
demo2: = base × 记忆
demo3: = base × 工具
demo4: = base × 规划
demo5: = base × 多 Agent
demo6: = base × 约束
```

---

## 七、运行环境

- Python 3.9+
- 依赖：`anthropic` SDK（兼容网关）+ `requests`（demo3 MCP Client）
- **网关 / 模型**：所有 demo 默认走**智谱 BigModel 的 Anthropic 兼容网关**（`https://open.bigmodel.cn/api/anthropic`）+ `glm-5.2` 模型——接口与 Anthropic SDK 完全兼容，换官方 API 或别的兼容网关只需改 `BASE_URL` / `MODEL`
- **API Key 三级回退**（优先级递减）：
  1. 改 `agent.py` Part 1 顶部的 `API_KEY = ""`（持久化，推荐）
  2. 设环境变量 `ANTHROPIC_API_KEY`
  3. 都没设 → 首次运行时交互式输入（仅本次有效）
- 平台：Windows 10 + Git Bash（demo2 讲稿提到 `grep` 在 Windows 不存在、`python3` 找不到等真实跨平台坑）

---

## 八、工业级 Agent 还做了哪些优化

本系列是教学用最简实现，目的是让你一眼看懂原理。下面这些是真实生产级 Agent（Claude Code、Cursor、Devin、Replit、Codex CLI）在 harness 层面做的工程化优化，每个都对应工业界成熟做法：

| 优化点 | 涉及轴 | 工业做法 |
|---|---|---|
| **中断恢复**（--resume / --continue） | 记忆 | 会话状态（messages）落盘到本地，下次启动加载接着干；不是知识记忆而是执行状态 |
| **会话级 hook** | 约束 | 除 PreToolUse/PostToolUse 外，还有 `SessionStart` / `UserPromptSubmit` / `PreCompact` 等会话级事件，最常用于环境信息注入 |
| **并发工具调用** | 循环 | `parallel_tool_use=true`，LLM 一次 turn 可以并行调多个独立工具（如同时 read_file 三个文件） |
| **Token 级压缩触发** | 记忆 | compact 不按条数触发，按上下文窗口占比（如 80%）触发，更精确 |
| **向量记忆** | 记忆 | Chroma / Pinecone 语义检索 top-K，比文件全量加载更省 token |
| **沙箱进阶** | 约束 | 真 chroot / Docker 容器 / 只读挂载，不只是命令模式拦截 |
| **可观测性** | 循环 | Token 消耗追踪、cost tracker、`--debug` 模式、进度条/spinner |

这些优化点不在本系列 6 个 demo 的范围内，但理解了 demo1-6 的核心机制，再看这些工业优化就是「工程化增量」——原理你已经懂了。

---

*真正的智能体 = 能力与约束的平衡。demo1-5 加能力，demo6 加约束，二者缺一不可。*
