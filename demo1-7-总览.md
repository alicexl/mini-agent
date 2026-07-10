# Demo1–7 总览：从 0 到 1 拆解 Agent 的底层原理

> 本系列用 7 个递进的最简 demo（每个 `agent.py` 都在 200–600 行之间）把 Claude Code / Cursor / Devin 这类"自动干活"工具的底层机制完全拆开。
>
> 每个目录下一份 `讲稿.md`（口播+屏显，配合视频讲解）+ 一份 `agent.py`（可直接 `python agent.py` 跑通）。本文件是**顶层索引**——一张图看完 7 个 demo 的能力叠加与解决的核心问题。

---

## 一、一张图看完整脉络

```
                      Agent = LLM × 工具 × 循环      （demo1）
                                │
                ┌───────────────┴───────────────┐
                ▼                               ▼
        加能力（demo2-6）                   加约束（demo7）
                │                               │
        ┌───────┴───────┐               三道防线
        ▼               ▼               · 黑名单（regex 拦截）
     记忆/规划        协作/压缩          · 用户确认（y/n/a）
     (demo2-3)       (demo4-6)           · 输出截断（头尾保留）
```

| Demo | 一句话公式 | 比喻 | 解决的核心问题 |
|---|---|---|---|
| **demo1** | `LLM × 工具 × 循环` | 给它**双手** | Agent 最小心跳——ReAct 循环跑通 |
| **demo2** | `demo1 × 记忆 × 规划` | 给它**长期记忆 + 纸笔** | 任务结束就忘；走一步看一步容易跑偏 |
| **demo3** | `demo2 × Rules × MCP`（plan 自动决策） | 给它**远程工具箱 + 规则意识** | 工具硬编码；无行为约束；plan 要手动开 |
| **demo4** | `demo3 × Subagent`（分工合作） | 给它**一次性助手** | 单 Agent 上下文膨胀；不能外包独立子任务 |
| **demo5** | `demo4 × Team × 事件驱动状态机` | 组建**正式项目团队** | Subagent 没记忆不能通信；无质检机制 |
| **demo6** | `demo5 × compact_messages` | 给它**短期记忆的动态压缩** | 多轮 ReAct 把 messages 撑爆上下文窗口 |
| **demo7** | （不加能力）+ **三道防线** | 给它**手脚的安全防护** | 工具能力太自由（`rm -rf /`、`dd of=/dev/sda`） |

---

## 二、能力 × 约束矩阵

> 看清每个 demo 引入了什么、保留了什么、去掉了什么。

| 能力 / 约束 | demo1 | demo2 | demo3 | demo4 | demo5 | demo6 | demo7 |
|---|---|---|---|---|---|---|---|
| LLM × 工具 × 循环（ReAct） | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 跨任务长期记忆（`agent_memory.md`） | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Plan 模式 | ❌ | ✅（手动） | ✅（LLM 自动决策） | — | — | ✅（沿用） | — |
| 行为约束 Rules | ❌ | ❌ | ✅ | ✅ | ✅（Team 级注入项目经理） | ✅ | — |
| 外部工具协议 MCP | ❌ | ❌ | ✅ | ❌（搬回本地） | ❌ | ❌ | ❌ |
| 多 Agent 分工（Subagent 一次性） | ❌ | ❌ | ❌ | ✅ | —（升级为 Team） | — | — |
| 多 Agent 协作（Team 持久 + 状态机） | ❌ | ❌ | ❌ | ❌ | ✅ | — | — |
| 上下文压缩 `compact_messages` | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | — |
| 安全边界（黑名单/确认/截断） | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |

**符号说明**：✅ = 有；❌ = 没有；— = 该 demo 基于的代码基线里没有（被前面 demo 引入后又主动去掉）。

---

## 三、三个核心视角

### 视角 A：Agent 公式链（每一节的"新增量"）

```
demo1  Agent = LLM × 工具 × 循环
demo2  = demo1 × 记忆 × 规划
demo3  = demo2 × Rules × MCP（plan 自动决策）
demo4  = demo3 × Subagent（一次性分工）
demo5  = demo4 × Team × 事件驱动状态机（持久协作）
demo6  = demo5 × compact_messages（上下文压缩）
demo7  = （不加能力）× 三道防线（黑名单 / 用户确认 / 输出截断）
```

### 视角 B：三种"拆任务"机制对比

| 机制 | 出现的 demo | messages | 适合 |
|---|---|---|---|
| **Plan**（step 列表） | demo2 手动 / demo3 自动 | 所有 step **共享**一份 | 后续 step 要用前面 step 的结果（有依赖） |
| **Subagent**（一次性） | demo4 | 每个 Subagent **独立**一份，结束即销毁 | 多个**相互独立**的子任务 |
| **Team**（持久 Agent） | demo5 | 每个 Agent **独立累积** + inbox | 有依赖 + 需通信 + 多次唤起 + 要质检 |

### 视角 C：能力 vs 约束

- demo1–6 都在**加能力**：工具 → 记忆 → 规划 → 外部协议 → 分工 → 团队 → 压缩
- **demo7 是唯一的转弯**——不加能力，而是给 execute_bash / read_file / write_file 这些"手脚"加**安全边界**
- 真正的智能体 = 能力与约束的平衡

---

## 四、各 demo 文件清单

| Demo | 入口 | 核心新增文件 | 讲稿 |
|---|---|---|---|
| demo1 | `demo1/agent.py` | — | `demo1/讲稿.md` |
| demo2 | `demo2/agent.py` | `agent_memory.md`（运行时生成） | `demo2/讲稿.md` |
| demo3 | `demo3/agent.py` + `demo3/mcp_server.py` | `.agent/rules.md` | `demo3/讲稿.md` |
| demo4 | `demo4/agent.py` | — | `demo4/讲稿.md` |
| demo5 | `demo5/agent.py` | —（Agent 类 + Team 类 内置在 agent.py） | `demo5/讲稿.md` |
| demo6 | `demo6/agent.py` | —（`compact_messages` 内置） | `demo6/讲稿.md` |
| demo7 | `demo7/agent.py` | `_test_guards.py`（三道防线单测） | `demo7/讲稿.md` |

> 每个目录下还有一份 `README.md`——精简的**设计方案 + 运行说明**（安装/配置/启动命令），深度讲解看 `讲稿.md`。

> demo1 是所有后续 demo 的基线——demo2-7 的 `agent.py` 都从 demo1 的 4 个 Part 扩展而来（Part 1 LLM 客户端 / Part 2 工具 schema / Part 3 工具实现 / Part 4 ReAct 主循环）。

---

## 五、推荐学习路径

### 路径 1：按编号顺序（最推荐）

demo1 → demo2 → demo3 → demo4 → demo5 → demo6 → demo7

每个 demo 都在前一个的基础上做"一减一加"（去掉一些、加上一些），代码 diff 量小，演进逻辑清晰。

### 路径 2：按主题

| 想学 | 看这几个 |
|---|---|
| Agent 最小心跳 | demo1 |
| 记忆系统 / 上下文管理 | demo2（长期）+ demo6（短期压缩） |
| 规划与多步串联 | demo2（手动 plan）+ demo3（自动 plan） |
| 外部工具接入 | demo3（MCP + Rules） |
| 多 Agent 系统 | demo4（Subagent）+ demo5（Team） |
| Agent 安全 | demo7（三道防线） |

### 路径 3：看真实运行

每个 demo 都有"真实运行回显"——`讲稿.md` 里贴了实测日志（不是虚构），直接看：

- demo1 §4 — 统计 .py 文件数 + 写 count.txt（3 轮 ReAct）
- demo2 §5 — 找 TODO 整理到 todo.md（grep→findstr→PowerShell 三次试错）
- demo3 §7 — 5-step plan：加法→乘法→天气→写文件→验证
- demo4 §3 — 4 个独立任务并行派 Subagent
- demo5 §4 — 4 个有依赖的任务 + 质检员实时验收
- demo6 §5 — 4-step 任务触发 4 次动态压缩
- demo7 §7 — 黑名单拦截 + 用户确认 + 大文件截断

---

## 六、demo1–7 系列回顾（讲稿交叉引用）

每个 demo 的讲稿结尾都有一张"进度回顾表"。为了避免表格在 7 个文件里各自漂移，本总览的「能力 × 约束矩阵」是**唯一权威表**——如发现任何讲稿里的进度表与本页不一致，以本页为准。

各讲稿结尾的公式链也应与上文「视角 A」对齐：

```
demo1: Agent = LLM × 工具 × 循环
demo2: demo2 = demo1 × 记忆 × 规划
demo3: demo3 = demo2 × Rules × MCP（plan 自动决策）
demo4: demo4 = demo3 × Subagent
demo5: demo5 = demo4 × Team × 事件驱动状态机
demo6: demo6 = demo5 × compact_messages
demo7: （不加能力，加三道防线）
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
- 平台：Windows 10 + Git Bash（demo2/6 讲稿提到 `grep` 在 Windows 不存在、`python3` 找不到等真实跨平台坑）

---

## 八、后续演进方向（本系列未实现）

留给读者探索的方向，每个都对应业界成熟框架的做法：

| 方向 | 涉及 demo | 业界做法 |
|---|---|---|
| **Skills（技能化）** | demo2 §6.2 提及但未实现 | 把常见任务的 Plan 预先固化，相似任务直接复用 |
| **向量记忆** | demo2 §6.1 | Chroma / Pinecone 语义检索 top-K |
| **沙箱隔离** | demo7 §8 | Docker / chroot / 只读挂载 |
| **pre-check / post-check hook 架构** | demo7 §9 | 把三道防线从硬编码改为可配置 hook |
| **token 级压缩** | demo6 §6.1 | 按上下文窗口占比（如 80%）触发，而非按条数 |

---

*本系列是教学用最简实现，目的是让你一眼看懂原理——真实生产级 Agent（Claude Code、Cursor、Devin）的所有"高级特性"都是这些核心机制的工程化增量。*
