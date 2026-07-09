# Demo5 — Subagent 的协作与编排（Team 模式 + 事件驱动状态机）

> 教学讲稿见 `讲稿.md`，本文件是技术参考文档。

## 一、Demo5 在系列中的位置

| Demo | 主题 | 关键能力 |
|---|---|---|
| demo1 | LLM × 工具 × 循环 | ReAct、本地工具 |
| demo2 | 记忆 × 规划 | agent_memory.md、独立 plan 命令 |
| demo3 | Rules × MCP | 行为约束、JSON-RPC 远程工具、plan 自动决策 |
| demo4 | Subagent 分工 | 主 Agent 可派生一次性独立 Subagent |
| **demo5** | **Team 协作 + 事件驱动** | **持久 Agent + 状态机调度 + 质检员持续监听** |

demo5 在 demo4 基础上做**一减三加**：

- **减法**：去掉 demo4 的 subagent 工具（临时工模式演不下去了）
- **加法 1**：新增 `Agent` 类——身份 + 记忆 + inbox + chat
- **加法 2**：新增 `Team` 类——招募 / 通信（一对一 + 群发）/ 解散 / run_team
- **加法 3**：新增**任务状态机 + 事件驱动调度**——`pending/reviewing/redoing/passed/failed` 五态；质检员持续监听，任务一完成立即质检；单任务最多 3 次质检；依赖了 failed 的任务自动级联 failed

## 二、核心概念：Agent + Team + 任务状态机

### 2.1 Agent：从函数升级为持久化对象

| 维度 | demo4 `_run_subagent`（函数） | demo5 `Agent`（类） |
|---|---|---|
| 身份 | 临时 role 字符串 | **固定 name + role** |
| messages | 函数局部变量，返回即丢 | **self.messages 实例属性，跨多次 chat 累积** |
| 通信 | 无 | **self.inbox 接收其他 agent 消息** |
| 生命周期 | 一次性 | **持续整个 team，多次 chat** |

### 2.2 Team：Agent 协调器

四个核心动作：

| 动作 | 方法 | 用途 |
|---|---|---|
| 招募 | `recruit(name, role)` | 创建一个新 Agent |
| 一对一通信 | `send(sender, receiver, message)` | A 给 B 的 inbox 塞一条消息（依赖注入 / 质检反馈打回） |
| 群发 | `broadcast(sender, message)` | 给所有其他成员塞消息（任务完成后通报全员） |
| 解散 | `dismiss()` | 项目结束，所有成员销毁 |

加上一个**事件驱动协作入口**：`run_team(user_input)`——LLM 当项目经理拆任务（强制含质检员），编排器跑事件循环。

### 2.3 任务状态机（demo5 的核心抽象）

```
pending ──依赖全 passed──> reviewing ──质检通过──> passed (终态)
                              │
                              ├──质检不过 + attempts<3──> redoing ──> (回 reviewing)
                              │
                              └──质检不过 + attempts=3──> failed (终态)

pending ──依赖 failed──> failed (级联，终态)
```

```python
TASK_PENDING   = "pending"    # 等待执行（依赖未满足）
TASK_REVIEWING = "reviewing"  # 执行完，待质检员验收
TASK_REDOING   = "redoing"    # 质检未过，待重做
TASK_PASSED    = "passed"     # 质检通过（终态）
TASK_FAILED    = "failed"     # 3 次质检不过，或依赖失败（终态）

MAX_REVIEW_ATTEMPTS = 3       # 单任务最多质检 3 次
```

## 三、文件结构

```
demo5/
├── agent.py            ← 全部实现（单文件，约 950 行）
├── .agent/
│   └── rules.md        ← Rules 规范文件（Team 级注入到项目经理 prompt）
├── 讲稿.md             ← 教学讲稿
├── README.md           ← 本文件
└── requirements.txt    ← 仅 anthropic
```

`agent.py` 按 7 个 Part 组织：

| Part | 内容 |
|---|---|
| 1 | LLM 客户端初始化（与 demo1-4 一致） |
| 2 | 本地工具定义 + 实现（6 个：bash/read/write/add/multiply/weather，**去掉 subagent**） |
| 3 | Rules 加载器（Team 级，注入到项目经理 prompt） |
| 4 | 记忆系统（沿用 demo2，只保留 append） |
| 5 | **Agent 类（核心新增）** |
| 6 | **Team 类 + 任务状态机 + 事件循环（核心新增）** |
| 7 | 交互式入口 |

## 四、本地工具集

demo5 沿用 demo4 的 6 个本地工具，**去掉 subagent**——协调工作交给 Team 类外部编排，不再靠 LLM 调 subagent 工具。

| 工具名 | 来源 | 用途 |
|---|---|---|
| `execute_bash` | 沿用 | 执行 shell 命令 |
| `read_file`    | 沿用 | 读文件 |
| `write_file`   | 沿用 | 写文件 |
| `add`          | 沿用 demo4 | 加法 |
| `multiply`     | 沿用 demo4 | 乘法 |
| `weather`      | 沿用 demo4 | 天气查询 |

## 五、关键代码片段

### 5.1 Agent 类的属性

```python
class Agent:
    def __init__(self, name, role, tools, local_fns, verbose=True):
        self.name        = name        # ← 固定身份
        self.role        = role        # ← 固定角色
        self.tools       = tools       # ← 可用工具
        self.local_fns   = local_fns   # ← 本地函数字典
        self.indent      = "    "      # ← 打印缩进（固定）

        self.inbox: list[tuple[str, str]] = []   # ← 收件箱（其他 agent 发来的消息）
        self.messages: list[dict]       = []     # ← 长期记忆
        self.system_prompt = build_agent_system_prompt(name, role)
```

### 5.2 Agent.chat 的三段式

```python
def chat(self, task=None) -> str:
    # 1) 消化 inbox：把所有未读消息包装成 user message 灌进 messages
    if self.inbox:
        for sender, msg in self.inbox:
            wrapped = f"[来自 {sender} 的消息] {msg}"
            self.messages.append({"role": "user", "content": wrapped})
        self.inbox.clear()

    # 2) 追加本次任务
    if task:
        self.messages.append({"role": "user", "content": task})

    # 3) 走 ReAct 循环（与 demo4 的 _react_loop 同构）
    return self._react_loop()
```

### 5.3 Team 的四个核心动作

```python
def recruit(self, name, role):
    agent = Agent(name=name, role=role, tools=self.tools, ...)
    self.agents[name] = agent

def send(self, sender, receiver, message):
    """一对一通信（依赖注入 / 质检反馈打回）"""
    self.agents[receiver].receive(sender, message)

def broadcast(self, sender, message):
    """群发（成员完成任务后通报全员）"""
    for name, agent in self.agents.items():
        if name == sender:
            continue
        agent.receive(sender, message)

def dismiss(self):
    self.agents.clear()
```

### 5.4 run_team 的事件驱动 5 步流程

```python
def run_team(self, user_input):
    # Step 1: 项目经理拆任务（强制含 1 名质检员 + 注入 team 级 rules）
    plan = self._plan_team(user_input)
    reviewer = self._find_reviewer(plan["members"])  # 扫 members 找 role 为「质检员」
    if not reviewer:
        raise RuntimeError("项目经理未分配质检员")

    # Step 2: recruit
    for m in plan["members"]:
        self.recruit(m["name"], m["role"])

    # Step 3: 事件循环——核心
    tasks = [_Task(t) for t in plan["tasks"]]
    self._event_loop(tasks, task_by_assignee, reviewer)

    # Step 4: 统计项目状态（passed N / failed M）
    # Step 5: dismiss
```

### 5.5 事件循环——单线程模拟"质检员一直在监听"

```python
def _event_loop(self, tasks, task_by_assignee, reviewer):
    while not all(t.terminal for t in tasks):
        # 优先级 ①：质检员一直在监听——拿到 reviewing 立即质检
        reviewable = [t for t in tasks if t.status == TASK_REVIEWING]
        if reviewable:
            self._review_one_task(reviewable[0], reviewer)
            continue

        # 优先级 ②：重做任务
        redoing = [t for t in tasks if t.status == TASK_REDOING]
        if redoing:
            t = redoing[0]
            t.result = self.agents[t.assignee].chat("请按质检反馈重做")
            t.status = TASK_REVIEWING
            continue

        # 优先级 ③：可启动的新任务（依赖全 passed）
        runnable = [t for t in tasks
                    if t.status == TASK_PENDING
                    and all(task_by_assignee[dep].status == TASK_PASSED
                            for dep in t.depends_on if dep in task_by_assignee)]
        if runnable:
            t = runnable[0]
            for dep in t.depends_on:  # 注入依赖结果
                self.send(dep, t.assignee, f"{dep} 的结果：{task_by_assignee[dep].result}")
            t.result = self.agents[t.assignee].chat(t.task_text)
            self.broadcast(t.assignee, f"{t.assignee} 完成任务：{t.result}")
            t.status = TASK_REVIEWING
            continue

        # 优先级 ④：死锁检查——依赖 failed 的 pending → 标记 failed（级联）
        for t in tasks:
            if t.status == TASK_PENDING:
                failed_deps = [dep for dep in t.depends_on
                               if task_by_assignee[dep].status == TASK_FAILED]
                if failed_deps:
                    t.status = TASK_FAILED
                    t.result = f"[依赖失败] 依赖的 {failed_deps} 未通过质检"
```

**为什么单线程**：`Agent.self.messages` 不是线程安全的——两个线程同时调 `chat()` 会把对话历史写乱。事件循环用单线程模拟"质检员一直在监听"的语义，又避开锁的复杂度。

### 5.6 质检员：事件驱动的单任务级质检

```python
def _review_one_task(self, task, reviewer):
    task.review_attempts += 1

    # 质检员接收完成通知
    self.agents[reviewer].receive(task.assignee,
        f"[待验收任务（第 {task.review_attempts} 次）]\n"
        f"任务：{task.task_text}\n执行结果：{task.result}")

    # reviewer 走完整 ReAct——可用 read_file / execute_bash 实际复查
    verdict_text = self.agents[reviewer].chat(
        "请严格验收上条任务。严格只输出 JSON：\n"
        '{"pass": true|false, "feedback": "若不通过，说明怎么改"}'
    )

    # 直接 json.loads——prompt 已要求纯 JSON
    try:
        v = json.loads(verdict_text.strip())
        passed = bool(v.get("pass") is True)
        feedback = v.get("feedback") or ""
    except json.JSONDecodeError:
        passed = False
        feedback = f"[质检员输出不是合法 JSON] 原文：{verdict_text}"

    if passed:
        task.status = TASK_PASSED
        self.broadcast(reviewer, f"✅ {task.assignee} 通过质检")
    elif task.review_attempts >= MAX_REVIEW_ATTEMPTS:  # = 3
        task.status = TASK_FAILED
    else:
        # 单独 send 给 assignee 反馈，让其重做
        self.send(reviewer, task.assignee, f"质检反馈：{feedback}")
        task.status = TASK_REDOING
```

**关键设计点**：

| 设计 | 说明 |
|---|---|
| 质检员是普通 Agent | 通过 `recruit("Q1", "质检员")` 创建，有 messages/inbox/tools，走完整 `_react_loop` |
| 质检员可用工具复查 | 比如用 `read_file` 打开 `result.txt` 真实复查 B1 的写入结果，用 `execute_bash` 验算 |
| 单任务级 3 次上限 | `MAX_REVIEW_ATTEMPTS = 3`，每个任务独立计质检次数，3 次不过即 failed |
| 不通过用 send 单独打回 | `send(reviewer, assignee, feedback)` 一对一，不污染其他人 |
| 执行者重做时有上下文 | `self.messages` 跨 chat 累积——能看到自己上一版 + 质检反馈（demo4 一次性 subagent 做不到） |
| 级联失败自动 | 依赖了 failed 的 pending 任务，下一轮循环优先级 ④ 自动标记 failed，无需启动 chat |

### 5.7 项目经理 prompt：两类角色 + 强制质检员 + team 级 rules 注入

```python
planning_prompt = (
    "你是一个项目规划师。请把用户的任务拆解成多个角色（成员）和子任务序列，输出严格 JSON。\n\n"
    "规则：\n"
    "1) 每个成员有简短的 name 和 role。\n"
    "   role 只能取两种值：「执行者」（承担具体任务，A1/A2/B1...）"
    "   或「质检员」（承担验收任务，只设 1 名，name 建议 Q1）\n"
    "2) 每个任务标注 assignee 和 task\n"
    "3) 依赖通过 depends_on 显式声明\n"
    "4) 任务应覆盖用户问题所有需要完成的部分\n"
    "5) **必须**分配且仅分配 1 名质检员——role 写「质检员」，"
    "   质检员**不接 task**，由编排器在事件循环中统一调起\n"
)

# team 级 rules 注入——让项目经理拆任务时守规矩
if self.rules:
    planning_prompt += f"\n---\n以下是项目级行为规范：\n{self.rules}"
```

> **为什么 role 只分两类**：在 demo5 的调度逻辑里，role 字段只有"质检员 vs 非质检员"的区别（`_find_reviewer` 关键字命中质检员），其它角色名（加法专家/写手/工程师...）对行为**零影响**——每个 Agent 做什么完全由它接到的 task 文本决定。收敛到两类让 plan 更整齐，也省去项目经理发明花式角色名的开销。

## 六、设计决策

### 6.1 为什么 Agent 要持久化（而不是 demo4 的一次性）

| 场景 | demo4 subagent（一次性） | demo5 Agent（持久化） |
|---|---|---|
| 同一角色被多次唤起 | 每次都是全新的，没记忆 | **同一对象多次 chat，messages 累积** |
| 跨多轮对话 | 不可能 | **天然支持** |
| 纠正重做 | 做错了只能整个重来 | **发消息让它看到错误，重新做** |

### 6.2 为什么需要 inbox 而不是直接共享 messages

- **解耦**：每个 Agent 看不到别人的 messages 全文，只看到别人**主动发来的内容**
- **可控**：发送方决定"哪条信息值得告诉对方"——避免噪音
- **异步**：inbox 可以累积多条消息，chat 时一起消化——更接近真实"消息队列"

### 6.3 为什么 send / broadcast 是 Team 的方法而不是 Agent 的

- **路由是 Team 的职责**：找目标 agent、遍历成员清单——这是容器的事
- **Agent 只管 receive**：别人怎么找到我，是 Team 的事
- **类比**：你只管收信，邮政编码、邮箱地址是邮局（Team）的事

### 6.4 为什么用事件循环而不是顺序派发

最直觉的 Team 实现是"按 tasks 列表顺序派发，全跑完再统一 review"——但这不对：

| 问题 | 顺序派发 | 事件循环 |
|---|---|---|
| 质检时机 | 所有任务跑完后**统一 review** | 任务一完成**立即质检** |
| 依赖启动 | 按列表顺序硬启动 | 依赖**全 passed** 才启动 |
| 单任务卡住 | 阻塞整个流水线 | 单任务 failed 自动级联，不阻塞 |
| 重做 | 全局重启 | 单任务状态机 redoing |

事件循环让质检员始终处于"待命 → 立即响应"的状态，新任务在依赖 passed 后才启动——这是真正接近真实项目组的协作模式。

> 工程约束：`Agent.messages` 不是线程安全的，所以 demo5 用**单线程事件循环**模拟"质检员一直在监听"的语义，不引入真并发的锁复杂度。

### 6.5 为什么用 run_team 入口而不是 LLM 自己决定

- **演示简化**：把"协作机制"这一件事讲透，不被"何时组建 team"的决策分心
- **可预测**：用户输入直接进 team 流程，输出可复现
- **真实场景可扩展**：可以做一个 `team_tool`，让 LLM 自己决定是否走 team

## 七、真实运行示例

### 7.1 有依赖的多步任务

```
用户: 帮我完成下面一组任务：1) 算 35+47；2) 把第 1 步的结果乘以 8；
      3) 查北京的天气；4) 把前面所有结果写入 result.txt

项目经理拆解（强制含质检员）：
  成员（👑 = 质检员）：
    · A1（执行者）/ A2（执行者）/ A3（执行者）/ B1（执行者）
    · Q1（质检员）  👑
  任务：
    · [A1] 算 35+47
    · [A2] 把上一步结果乘以 8 ← depends_on: ['A1']
    · [A3] 查北京天气
    · [B1] 写入 result.txt ← depends_on: ['A1','A2','A3']

事件循环执行：
  [loop 1] 🚀 启动 [A1] → "35 + 47 = 82" → broadcast → Q1 inbox 收到
  [loop 2] 📨 质检员捕获 A1 完成 → 走 ReAct → {pass: true} → A1 → passed
  [loop 3] 🚀 启动 [A2]（A1 passed，依赖满足）
           → send(A1, A2, "结果：82") → A2 消化 inbox → "82 × 8 = 656" → broadcast
  [loop 4] 📨 质检 A2 → passed
  ...（A3 类似）
  [loop N] 🚀 启动 [B1]（A1/A2/A3 全 passed）
           → send 三条依赖 → B1 消化 inbox → write_file
  [loop N+1] 📨 质检 B1 → Q1 用 read_file 实际打开 result.txt 复查 → passed

项目结束：✅ 通过 4 个 / ❌ 失败 0 个 → dismiss
```

**与 demo4 subagent 的对比**：demo4 subagent 之间完全隔离，A2 看不到 A1 的结果——team 模式通过 send + inbox + 状态机解决了这个问题。

### 7.2 级联失败场景

如果 A1 三次质检都不过：

```
  [loop 1] 🚀 启动 [A1] → 第 1 版 → broadcast
  [loop 2] 📨 质检 → 不通过 → A1 redoing
  [loop 3] 🔁 A1 重做 → 第 2 版 → broadcast
  [loop 4] 📨 质检 → 不通过 → A1 redoing
  [loop 5] 🔁 A1 重做 → 第 3 版 → broadcast
  [loop 6] 📨 质检 → 不通过 + attempts=3 → A1 → failed
  [loop 7] ⚠️ 死锁检查——A2 依赖 A1（failed） → A2 自动标记 failed（calls=0，从未启动 chat）

项目结束：✅ 通过 0 个 / ❌ 失败 2 个
```

**关键**：A2 的 chat 从未被调过——不浪费 token，状态机自动传播失败。

## 八、Plan vs Subagent vs Team 决策树

```
任务复杂吗？
├─ 否 → 直接 ReAct
└─ 是 → 子任务之间什么关系？
        ├─ 严格按步骤、有依赖 → Plan（共享 messages）
        ├─ 相互独立 → Subagent（独立 messages）
        └─ 有依赖 + 需要通信 + 需要质检 → Team（事件驱动 + 状态机）
```

| 场景 | 选择 |
|---|---|
| 「算 35+47，结果乘 8，写文件，验证」 | **plan** |
| 「四件独立的事：加法 / 乘法 / 天气 / 写文件」 | **subagent** |
| 「5 个角色协作完成一个有依赖的项目，需要质检员把关」 | **team** |
| 「查一下上海天气」 | **直接 ReAct** |

## 九、运行

### 9.1 依赖

```bash
pip install -r requirements.txt
```

### 9.2 配置 API Key

编辑 `agent.py` 顶部的 `API_KEY` 变量，或设置环境变量：

```bash
export ANTHROPIC_API_KEY=...
```

### 9.3 启动

```bash
cd demo5
python agent.py
```

REPL 内输入任务即可（例如「帮我完成下面一组任务：1) 算 35+47；2) 把第 1 步的结果乘以 8；3) 查北京天气；4) 把所有结果写入 result.txt」），`quit` / `exit` 退出。质检员的分配由项目经理 prompt 强制约束，用户不必再提。

## 十、demo 进度回顾

| 能力 | demo1 | demo2 | demo3 | demo4 | demo5 |
|---|---|---|---|---|---|
| LLM × 工具 × 循环 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 跨任务长期记忆 | ❌ | ✅ | ✅ | ✅ | ✅ |
| 规划 + 多步串联（plan） | ❌ | ✅ | ✅（自动决策） | — | — |
| 行为约束（Rules） | ❌ | ❌ | ✅ | ✅ | ✅（Team 级注入项目经理） |
| 外部工具协议（MCP） | ❌ | ❌ | ✅ | ❌ | ❌ |
| 多 Agent 分工（Subagent） | ❌ | ❌ | ❌ | ✅ | — |
| **多 Agent 协作（Team）** | ❌ | ❌ | ❌ | ❌ | ✅ |
| **事件驱动 + 任务状态机** | ❌ | ❌ | ❌ | ❌ | ✅ |

## 十一、下一节预告

demo6 会讲**Agent 的上下文压缩**：

- 累积的长期记忆如何**摘要 + 压缩**——让 Agent 跑得再久也不撑爆上下文窗口
- 滑动窗口、分级摘要、关键信息抽取等技术
- 这才是真正能跑在生产环境的 Agent
