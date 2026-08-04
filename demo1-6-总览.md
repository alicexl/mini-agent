# Demo1–6 总览：从 0 到 1 拆解 Agent 的底层原理

> 本系列用 6 个递进的最简 demo（每个 `agent.py` 约 400–720 行）把 Claude Code / Cursor / Devin 这类"自动干活"工具的底层机制完全拆开。
>
> 每个目录下一份 `讲稿.md`（口播+屏显，配合视频讲解）+ 一份 `agent.py`（可直接 `python agent.py` 跑通）。本文件是**顶层索引**——一张图看完 6 个 demo 的能力轴与解决的核心问题。

---

## 一、一张图看完整脉络

```
                    Agent = LLM × 工具 × 循环 × 状态      （demo1 = base）
                                │
            ┌───────────────────┴───────────────────┐
            ▼                                       ▼
        能力（demo2-5）                          约束（demo6）
            │                                       │
   ┌────────┼────────┐                两层 Control Plane
   ▼        ▼        ▼                · Permission（allow/deny/ask 规则）
 记忆      工具     规划                · Hook（PreToolUse/PostToolUse 回调）
（demo2）（demo3）（demo4）
            │
            ▼
       多 Agent（demo5）
   Subagent + Team
```

**每个 demo 拆一条正交的能力轴**，公式不链式叠加，而是 `demo(N) = base × 轴N`——base 就是 demo1（ReAct 心跳），后续每个 demo 在 base 上独立加一条轴。读者可以按任意顺序学 demo2-6。

| Demo | 轴 | 一句话公式 | 比喻 | 解决的核心问题 |
|---|---|---|---|---|
| **demo1** | 循环 | `LLM × 工具 × 循环 × 状态` | 给它**双手** | Agent 最小心跳——ReAct 循环跑通（base） |
| **demo2** | 记忆 | `base × 记忆` | 给它**长期记忆 + 动态压缩** | 任务结束就忘；多轮 ReAct 把 messages 撑爆上下文窗口 |
| **demo3** | 工具 | `base × 工具扩展` | 给它**更多手脚 + 远程工具箱** | 工具只有 read/write/bash/edit；想接外部协议 |
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
| **MCP**（外部工具协议） | demo3 | client-server + JSON-RPC 风格 round-trip，挂外部 server |
| **Plan 模式**（手动 + 自动） | demo4 | 手动开 plan 模式 / LLM 自动决策 plan 与否；TodoWrite 风格 step 列表 |
| **Skill** | demo4 | SKILL.md 预消化的工作流，description 匹配后注入 prompt |
| **Subagent**（一次性） | demo5 | 独立 context、无状态、结束即销毁；适合**相互独立**的子任务 |
| **Team**（持久 + 消息队列） | demo5 | 独立累积 messages + 消息队列 + `[send:]` 路由；适合**需多角色分工协作**的任务 |
| **Permission** | demo6 | allow/deny/ask 规则匹配（如 `Bash(rm:*)`），工具调用前的访问控制 |
| **Hook** | demo6 | PreToolUse / PostToolUse 事件回调（agent.py 内函数：Pre 可拦/改 input，Post 可改/补 output） |

---

## 三、五个核心视角

### 视角 A：轴公式（每条轴独立，不链式叠加）

```
demo1  base = LLM × 工具 × 循环 × 状态
demo2  = base × 记忆            （短期 messages + 长期文件 + compact + caching）
demo3  = base × 工具扩展        （MCP 外部工具协议）
demo4  = base × 规划            （手动 Plan + 自动 Plan + Skill 预消化）
demo5  = base × 多 Agent        （Subagent 一次性 + Team 持久 + 消息队列）
demo6  = base × 约束            （Permission 规则 + Hook 回调）
```

### 视角 B：三种"拆任务"机制对比

| 机制 | 出现的 demo | messages | 适合 |
|---|---|---|---|
| **Plan**（step 列表） | demo4 | 所有 step **共享**一份 | 后续 step 要用前面 step 的结果（有依赖） |
| **Subagent**（一次性） | demo5 `agent_sub.py` | 每个 Subagent **独立**一份，结束即销毁 | 多个**相互独立**的子任务 |
| **Team**（持久 Agent） | demo5 `agent_team.py` | 每个 Agent **独立累积** + 消息队列路由 | 需多角色分工协作的任务 |

### 视角 C：能力 vs 约束

- demo1–5 都在**加能力**：循环 → 记忆 → 工具 → 规划 → 多 Agent
- **demo6 是唯一的转弯**——不加能力，而是在 LLM 和 execute_bash / read_file / write_file 这些"手脚"之间插入**两层 Control Plane**（Permission / Hook）
- 真正的智能体 = 能力与约束的平衡

### 视角 D：六轴最终都落在 Agent Runtime 层

前面三个视角把 6 个 demo 看成「6 条独立的能力/约束轴」。还有一个更高的视角：**这 6 条轴最终都落在同一个东西上——Agent Runtime（也叫 harness）**。

Agent Runtime 是包裹 LLM 的那层工程代码——本系列每个 demo 的 `agent.py` 就是一个最简 Runtime。LLM 只负责「想」，Runtime 负责「把想法变成行动并管住行动」：循环、记忆、工具、规划、多 Agent、约束，全是 Runtime 的职责。Claude Code / Cursor / Devin 这些产品的差异，本质上是各自 Runtime 的工程化深度不同。

demo6 引入的几个概念，在产品级 Runtime 里有更通用的名字：

| demo6 / 系列里的叫法 | Runtime 层的通用概念 | 干什么 |
|---|---|---|
| Permission | **Runtime Policy** | 工具调用的准入策略（allow / deny / ask） |
| Hook | **Lifecycle Extension** | 在生命周期点（Pre/Post/SessionStart…）插扩展逻辑 |
| demo2 compact / caching | **Runtime Optimization** | 管理上下文窗口、压成本 |
| Sandbox（demo6 第 4 章演进方向） | **Execution Isolation** | 限制工具执行的爆炸半径 |

> 这个视角不改 demo6 的定位——demo6 仍是「base × 约束」这条单轴。Runtime 是把 6 个 demo 串起来的**整合视角**：学完 6 轴，你脑子里就拼出了一个完整 Agent Runtime 的骨架。

### 视角 E：持续进化闭环（从经验中学习）

再换一个视角：demo2、demo4、demo6 这三条轴，能串成同一条主线——**Agent 怎么从经验里学习**。

Agent 每次任务结束，踩过的坑、试出来的窍门不该随上下文一起丢掉，而该沉淀成下次能复用的东西。沉淀有四种载体，本系列恰好踩中前三种：

| 载体 | 含义 | 本系列对应的轴 |
|---|---|---|
| **知识** | 把经验写成可检索的事实 | demo2 `agent_memory.md`——任务结果摘要落盘，下次启动加载 |
| **指令** | 把经验写成可执行的工作流模板 | demo4 `skills/review.md`——description 匹配后注入 prompt |
| **程序** | 把经验写死成 Harness 代码层约束 | demo6 `PERMISSION_RULES` / Hook——不靠 prompt 引导，LLM 绕不过去 |
| 参数 | 把经验训练进模型权重 | 超出教学范围（见第八节） |

一个巧妙的呼应：**Claude Code 的 `MEMORY.md`** 就是这套机制的工业版——本系列 demo2 的 `agent_memory.md`，正是它的最简教学版。

**诚实的边界**：本系列给的是「沉淀的三种载体」，还不是完整的「持续进化闭环」。完整的闭环要把沉淀物**验证 → 发布 → 回滚**（改坏了能退回来），这套管理机制是工业级 harness 的事，不在 6 个 demo 内（见第八节）。但理解了 demo2/4/6 提供的三种载体，再看闭环就只剩「怎么管这些沉淀物」一层工程问题。

---

## 四、各 demo 文件清单

| Demo | 入口 | 核心新增文件 | 讲稿 |
|---|---|---|---|
| demo1 | `demo1-react/agent.py` | — | `demo1-react/讲稿.md` |
| demo2 | `demo2-memory/agent.py` | `agent_memory.md`（运行时生成） | `demo2-memory/讲稿.md` |
| demo3 | `demo3-tools/agent.py` + `demo3-tools/mcp_server.py` | — | `demo3-tools/讲稿.md` |
| **demo4** | `demo4-plan/agent.py` | `skills/review.md`（示例 Skill——代码审查工作流） | `demo4-plan/讲稿.md` |
| **demo5** ✅ | `demo5-multiagent/agent_sub.py` + `demo5-multiagent/agent_team.py` | —（两份 agent 入口，一份讲稿对照讲） | `demo5-multiagent/讲稿.md` |
| demo6 | `demo6-safety/agent.py` | —（两层 Control Plane 全在 agent.py 单文件内） | `demo6-safety/讲稿.md` |

> 每个目录下还有一份 `README.md`——精简的**设计方案 + 运行说明**（安装/配置/启动命令），深度讲解看 `讲稿.md`。

> demo1 是所有后续 demo 的基线——demo2-6 的 `agent.py` 都从 demo1 的 4 个 Part 扩展而来（Part 1 LLM 客户端 / Part 2 工具 schema / Part 3 工具实现 / Part 4 ReAct 主循环）。

### demo5 的特殊结构

demo5 一个目录下有**两个 agent.py**：

- **`agent_sub.py`**（主线）：Subagent 一次性分工。代码精简（独立 context、无状态、结束即销毁），讲稿权重 70%。对应 Claude Code 的 Task tool / Cursor 的 agent / Devin 的子任务派发。
- **`agent_team.py`**（实战案例）：多角色团队协作——LLM 拆角色 + 消息队列 + `[send: 成员名]` 路由 + Agent 持久 `self.messages`。讲稿权重 30%，定位为「Subagent 在需要多角色协作/任务流转时的升级版」，对应 AutoGen / CrewAI 范式。

讲稿先用 agent_sub.py 演示一次性 Subagent 的能力边界，再用 agent_team.py 演示持久对象 + 消息队列如何让任务在角色间流转——两种多 Agent 范式的对照。

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
| Agent 安全 | demo6（两层 Control Plane） |

### 路径 3：看真实运行

每个 demo 都有"真实运行回显"——`讲稿.md` 里贴了实测日志（不是虚构），直接看：

- demo1 §4 — 统计 .py 文件数 + 写 count.txt（3 轮 ReAct）
- demo2 §5 — 案例 1（统计 .py 文件，3 轮 ReAct + caching 命中）+ 案例 2（5 步串行任务，7 轮 ReAct + compact 触发）
- demo3 §7 — MCP 远程调用 + edit 精细修改对照
- demo4 §6 — Plan 自动决策 + Skill 匹配触发
- demo5 §2 / §3 — Subagent 派发独立任务 / Team 跑通多角色团队协作
- demo6 §2 / §3 — Permission deny 拦截 + Hook Pre/Post 回调

---

## 六、系列回顾（讲稿交叉引用）

每个 demo 的讲稿结尾都有一张"轴覆盖回顾表"。为了避免表格在 6 个文件里各自漂移，本总览的「轴清单 × 内容矩阵」是**唯一权威表**——如发现任何讲稿里的进度表与本页不一致，以本页为准。

各讲稿结尾的公式也应与上文「视角 A」对齐：

```
demo1: base = LLM × 工具 × 循环 × 状态
demo2: = base × 记忆
demo3: = base × 工具扩展
demo4: = base × 规划
demo5: = base × 多 Agent
demo6: = base × 约束
```

---

## 七、运行环境

- Python 3.9+
- 依赖：`anthropic` SDK（兼容网关）+ `requests`（demo3 MCP Client）
- **网关 / 模型**：所有 demo 默认走**智谱 BigModel 的 Anthropic 兼容网关**（`https://open.bigmodel.cn/api/anthropic`）+ `glm-5.2` 模型——接口与 Anthropic SDK 完全兼容，换官方 API 或别的兼容网关只需改 `BASE_URL` / `MODEL`
- **API Key 三级回退**（实际优先级：env 覆盖代码变量，代码是 `os.environ.get("ANTHROPIC_API_KEY") or API_KEY`）：
  1. 设环境变量 `ANTHROPIC_API_KEY`（优先级最高）
  2. 改 `agent.py` Part 1 顶部的 `API_KEY = ""`（持久化；env 未设时才生效）
  3. 都没设 → 首次运行时交互式输入（仅本次有效）
- 平台：Windows 10 + Git Bash（demo2 讲稿提到 `[ -f hello.txt ]` 在 Windows cmd 报错、LLM 自动切换 `set /a` cmd 内置算术等真实跨平台坑）

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
| **仓库地图**（repo map） | 工具 | tree-sitter 解析全仓 → PageRank 排名选重要符号 → 压进 ~1k token 的折叠地图（Aider），让 LLM 不读全文掌握代码结构 |
| **执行环境隔离** | 约束 | firejail / Docker / microVM（Firecracker）——Control Plane 之外的 Execution Environment 层 |
| **可观测性** | 循环 | Token 消耗追踪、cost tracker、`--debug` 模式、进度条/spinner |

这些优化点不在本系列 6 个 demo 的范围内，但理解了 demo1-6 的核心机制，再看这些工业优化就是「工程化增量」——原理你已经懂了。

---

*真正的智能体 = 能力与约束的平衡。demo1-5 加能力，demo6 加约束，二者缺一不可。*
