# Demo5 — 多 Agent 轴：Subagent 后台分包

> 把相互独立的子任务各开一份 context 后台分包——**subagent**（派一次性 Subagent 在后台执行，完成通知带回最终报告）+ **jobs**（后台任务机制：shell / agent 两种 job 共用注册表、日志落盘、`<task-notification>` 通知注入）+ 兜底工具 **job_kill**（任务跑飞 / 不再需要时强停），对齐 Claude Code 的 Task 工具。
> 本目录是系列第五站（`demo5 = demo4 底座 × 多 Agent`）：完整继承 demo4 九文件底座（规划轴三件 + 记忆 / 会话，机制一行未动），新增多 Agent / 后台任务 / TUI 三层，成十二文件结构——`agent.py`（主入口）+ `tools.py` / `plan.py` / `skill.py` / `ask.py` / `subagent.py` / `jobs.py` / `tui.py` / `render.py` / `memory.py` / `session.py` / `commands.py` + `skills/review.md`，文件分工见 [§1.1](#11-代码层的变化)。

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`anthropic` + `rich` + `prompt_toolkit`。

### 配置 API Key

网关、模型、超时已在代码里写死，**只需配置 API Key**（按优先级）：

1. 环境变量（推荐，可持久化）：

   ```bash
   export ANTHROPIC_API_KEY=你的智谱Key    # Key 格式 id.secret，在 bigmodel.cn 生成
   ```

2. 未设环境变量直接运行，会提示输入（不持久化，每次运行都要重输）。

### 启动

```bash
python -X utf8 agent.py
```

进入交互模式后输入任意任务。斜杠命令：`/help` 可用命令 / `/status` 会话状态 / `/tools` 工具清单 / `/skills` 已加载 Skills / `/memory` 项目级记忆 / `/resume` 恢复历史会话 / `/new` 开新会话（顺带清理运行中的后台 job）/ `/compact` 手动压缩 / `/quit` 退出（`/exit` 同）。

演示建议（三个场景的实测轨迹见 [§3](#3-真实演示案例)）：

- **后台命令**（[§3.1](#31-场景-a耗时命令自动放后台)）：输入「跑一下 python -c "import time; time.sleep(20); print('BUILD SUCCESS')"，结束后告诉我输出了什么」——LLM 自动判断耗时命令放后台，派完即回，完成后通知自动触发汇报（不用 sleep 命令——Windows cmd 没有）
- **Subagent**（[§3.2](#32-场景-b三个独立子任务--三个后台-subagent)）：输入「帮我做三件事，每件都派一个专门的 Subagent 完成：1) 统计当前目录下每个 py 文件的行数，找出行数最多的一个；2) 写一个脚本计算斐波那契数列第 20 项并运行；3) 查询北京明天的天气并给出穿衣建议」——主 Agent 并行派 3 个后台 Subagent（嵌套轨迹 + 日志落盘），完成后三条通知注入、自动汇总
- **job_kill 强杀**（[§3.3](#33-场景-c终止后台任务jobkill-强杀)）：先输入「跑一下 python -c "import time; time.sleep(300)"，放后台就行，不用等它」，等 job 派出后再输入「这个不用跑了，停掉吧」——job_kill 走 taskkill 杀整棵进程树；tool_return 即终态送达（kill 标记 notified，不触发唤醒轮）

> **注 1**：`-X utf8` 防止 GBK Windows 控制台中文乱码。
>
> **注 2**：TUI 仅在真终端（TTY）下启用；管道环境自动降级——输入由裸 input 线程投递、输出直通终端，`printf '任务\n/quit\n' | python -X utf8 agent.py` 可自动跑完。

### 可调参数（`memory.py` 顶部，同 demo2/3/4）

| 参数 | 默认 | 含义 |
|---|---|---|
| `MEMORY_WINDOW_LINES` | 50 | MEMORY.md 加载进 system prompt 的行数上限（防止上下文无限增长） |
| `COMPACT_KEEP_RECENT` | 4 | `/compact` 时保留最近 N 条原始消息 |
| `USE_CACHE_CONTROL` | True | 是否启用 prompt caching |

> 另有 `USE_THINKING` 思考模式开关（`subagent.py` 顶部，默认关闭）。

---

## 1. 结论：demo5 多了什么

demo4 的规划轴解决了"怎么拆任务、怎么问、怎么复用"——但拆出来的子任务，仍由主 Agent 在同一条 messages 流水里逐个执行。demo5 要解决的就是**拆出来的任务独立执行**：「统计文件数 + 算斐波那契 + 查天气」这类相互独立的子任务，各开一份 context 自己跑。两个好处：

1. **并发**——互不依赖的子任务同时跑，主 Agent 不必排队等前一个做完；
2. **不污染主 messages**——子任务的工具调用、报错、重试全留在各自的 context 里；主 Agent 像个包工头：不需要知道细节，只需要知道**是什么任务、得到了什么结论**。

为什么只要结论就够？因为拆分和澄清在 demo4 的规划轴已经做完了——subagent 接到的只是执行，不会再有方向上的偏差，中间过程自然不值得占主 Agent 的上下文。

所以 demo5 = demo4 底座 × 多 Agent，机制两条：**subagent**（独立子任务分包）+ **jobs**（后台任务机制——subagent 后台化的底座），外加配套的兜底工具 **job_kill**——任务跑飞 hang 住、或不再需要时把它停掉（新开会话 / 退出时的清理也走它）。

demo5 与 demo4 的轴覆盖对照：

| 维度 | demo4 | demo5 |
|---|---|---|
| **本地工具集** | 7 个 | 9 个（+ **subagent** + **job_kill**） |
| **记忆 / 会话模型** | ✅ | ✅ **不变**（全继承；无自动压缩同 demo3/4，压缩只由 `/compact` 手动发起） |
| **规划轴三件** | ✅ | ✅ **不变**（plan / Skill / ask 全继承） |
| **多 Agent 轴** | ❌ | ✅ subagent 后台分包 + jobs 后台任务机制（日志 + 通知） |

底座取舍的判据：**自包含的机制照带，依赖外部服务的机制不带**。demo4 的规划轴三件全部自包含——plan 是纯本地函数、skills/*.md 是文件、ask 是终端 UI，带上零成本、不触发也不影响；demo3 的 MCP 不带——它依赖一个模拟 mcp_server。

### 1.1 代码层的变化

demo4 九文件 → demo5 十二文件：**新增三个层，微调四个文件，其余原样继承**。

| 文件 | demo4 | demo5 的变化 |
|---|---|---|
| `subagent.py` ★新增 | — | **多 Agent 层**：subagent 工具（后台执行）+ 主/子共用的 ReAct 循环 |
| `jobs.py` ★新增 | — | **后台任务层**：Job 注册表（shell / agent 两种）+ 日志落盘 + `<task-notification>` 通知 + job_kill |
| `tui.py` ★新增 | — | **TUI 层**：行内渲染界面——输出直通终端滚动区、输入行原位重绘、ask 内联弹层（仅 TTY） |
| `agent.py` | 主入口：客户端 + 主循环 | 工具合并为 9 个；主循环走 `subagent.run_react_loop` 共用循环 + 通知注入 + 自动唤醒；TTY 分支进 TUI（控制器线程） |
| `tools.py` | 工具层（demo1 四件套） | execute_bash 加 `run_in_background` 参数（默认同步，行为不变） |
| `plan.py` | 规划层：plan 工具 | 原样继承（逐字节相同） |
| `skill.py` | Skill 层：Skill 加载器 + use_skill | 原样继承（逐字节相同） |
| `ask.py` | 提问层：ask_user_question 方向键 UI + 非 TTY 降级 | 加 TUI 弹层钩子 |
| `render.py` | 渲染层（demo2 版） | 代码未动，仅补 docstring——TUI 重定向由 agent.py 在 import 前替换 sys.stdout 实现，render 的输出自动流进 TUI 日志区 |
| `memory.py` | 记忆层（demo2 版减自动压缩） | 原样继承（逐字节相同） |
| `session.py` | 会话层：`memory/<会话ID>.jsonl` 持久化 + `/resume` | 原样继承（逐字节相同） |
| `commands.py` | 命令层：`/help` `/status` `/tools` `/skills` `/memory` `/resume` `/new` `/compact` `/quit` | `/new` 补 job 清理（其余命令不变） |
| `skills/review.md` | 示例 Skill——代码审查工作流 | 原样继承 |
| `jobs/` | —（运行时生成） | 后台任务日志目录（已 gitignore；启动时自动清理上次遗留，被其他实例占用的跳过） |
| `memory/` | 运行时生成（同 demo2） | 双层记忆目录（已 gitignore，同 demo2） |

微调与继承的边界很清楚——**demo4 的底座只加了几处小钩子，机制一行未动**（tools.py 加参数；ask / commands 各一处小改）。多 Agent 轴的全部代码长在 `subagent.py` + `jobs.py`，TUI 长在 `tui.py`。这就是轴式设计：demo5 = demo4 底座 × 多 Agent。

agent.py `__main__` 里把各层工具合并成 9 个，并向 subagent 层注入依赖：

```python
ALL_TOOLS = BASE_TOOLS + plan.PLAN_TOOLS + skill.SKILL_TOOLS + [ask.ASK_TOOL] \
            + [subagent.SUBAGENT_TOOL, jobs.JOB_KILL_TOOL]
AVAILABLE_FUNCTIONS = {**BASE_FUNCTIONS, **plan.PLAN_FUNCTIONS, **skill.SKILL_FUNCTIONS,
                       "ask_user_question": ask.ask_user_question,
                       "subagent": subagent.subagent,
                       "job_kill": jobs.job_kill}

# 注入 subagent 层依赖：subagent() 需要全量工具做裁剪 + 分发路由
subagent.ALL_TOOLS = ALL_TOOLS
subagent.FUNCTIONS = AVAILABLE_FUNCTIONS
```

---

## 2. Subagent：独立子任务的后台分包

### 2.1 前置：后台任务机制（jobs.py）

先解决一个更基础的问题：**耗时命令怎么跑**。demo1 起的 execute_bash 有一条写死的规矩——`subprocess.run(..., timeout=60)`，60 秒跑不完就强行终止。对 `ls` 这类命令无所谓，但 dev server、长测试这类耗时命令就会"超时失败"，而且同步等待时整个 Agent 卡死 60 秒。

Claude Code 的做法：**耗时命令放后台执行，立即拿到 job id 继续干别的活；命令结束后一条通知送到模型面前**。demo5 把它实现成 `jobs.py`，核心是一个 Job 注册表：

```python
@dataclass
class Job:
    id: str                     # b 前缀 = shell job，a 前缀 = agent job
    kind: str                   # "shell" / "agent"
    description: str
    log_path: str               # 输出落盘日志文件——边跑边写，read_file 随时可查
    status: str = "running"     # running / completed / failed / killed
    returncode: int | None = None
    result: str | None = None   # agent job 的最终报告（shell job 的输出在日志里）
    notified: bool = False      # 是否已通知——防重复通知
```

四个关键设计：

1. **不等它结束**：shell job 用 `subprocess.Popen`（不 wait）起进程，agent job 把子循环跑在线程里——工具调用立即返回
2. **输出落盘**：stdout/stderr 重定向到 `jobs/<id>.log`——边跑边写，主 Agent 和用户随时 read_file / tail 看进度
3. **状态谁来写——两种 job 不对称**：shell job 靠 watcher 线程盯 `proc.wait()`（wait 顺带是收尸义务——POSIX 下没人 wait 会留僵尸进程），按 exit code 标 completed / failed；agent job 没有 watcher——执行线程自己会说 Python，收尾处自己写状态（正常返回 = completed、抛异常 = failed）
4. **通知注入**：完成后的 `<task-notification>` 就是**一条 user 消息**——塞进下一次发给模型的请求里。工作时搭下一次请求的车；空闲时（主循环结束）由程序等待后自动触发一轮汇报

execute_bash 只加了一个参数：

```python
def execute_bash(command: str, run_in_background: bool = False) -> str:
    """执行 shell 命令（run_in_background=True 时放后台，立即返回 job 信息）"""
    if run_in_background:
        import jobs
        job = jobs.REGISTRY.spawn_shell(command)
        return (f"已放入后台运行，job id: {job.id}\n"
                f"输出日志：{job.log_path}（随时可用 read_file 查看）\n"
                "完成后你会收到 <task-notification> 通知，不要原地等待，也不要反复读日志轮询。")
    ...   # 原同步路径不变
```

system prompt 里配套一段引导（工具描述管"怎么填"，引导管"什么时候用"）：长驻或耗时命令用 run_in_background=True；**不要主动轮询等待**，通知会来。这条"不要轮询"很关键——不写清楚，模型会反复 read_file 读日志把轮次刷满。

### 2.2 subagent 工具定义

有了 job 机制，subagent 就是 kind="agent" 的 job：

```python
SUBAGENT_TOOL = {
    "name": "subagent",
    "description": (
        "委派一个独立的 Subagent 在后台完成子任务。Subagent 有独立的 context（messages），"
        "完成后你会收到 <task-notification> 通知（附最终报告），不要原地等待。\n\n"
        "**使用时机**：相互独立的子任务——每个子任务派一个 Subagent。"
        "链式任务（后一步依赖前一步结果）不要用，Subagent 之间无法传递结果。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "Subagent 的角色，例如「Python 工程师」/「文件统计员」"},
            "task": {"type": "string", "description": "交给 Subagent 完成的具体任务描述"},
        },
        "required": ["role", "task"],
    },
}
```

两种 job 是同一个抽象的两种实例——外围机制（日志 / 通知 / kill / 清理）完全共用，差别只在「活本身」：

| | shell job | agent job |
|---|---|---|
| 活是什么 | 运行一条 OS 命令 | 跑一个子 Agent（LLM 循环） |
| 执行体 | 子进程（Popen，OS 调度） | 线程（Python 调度） |
| 成果放哪 | 命令输出 → 日志文件（result 为 None） | 最终报告 → `job.result`（随 `<result>` 直达） |
| 终态判据 | exit code（0 = completed，非 0 = failed） | 正常返回 = completed，抛异常 = failed |
| 终止方式 | 杀进程树（taskkill /T，强杀） | `stop_event`（协作式，循环每轮检查） |
| id 前缀 | b | a |

和 demo4 的 plan 一个套路——**正向触发**（相互独立的子任务 → 派 Subagent）+ **反向抑制**（链式依赖 → 主 Agent 自己顺序做）。

plan 和 subagent 的关系也顺带理清——**不冲突，是两种粒度的拆任务**：

| | plan | subagent |
|---|---|---|
| 拆什么 | 当前任务内的**步骤** | **独立子任务**的归属 |
| 上下文 | 步骤共享同一份 messages | 每个子任务独立 context |

"这一步做什么"交给 plan，"这活交给谁"交给 subagent——两者可同框出现（plan 列步骤时，某一步就可以是"派个 Subagent"）。

### 2.3 主/子同构：共用 run_react_loop

这是 demo5 最关键的设计——**主 Agent 和 Subagent 跑同一个循环**，差别只在传入的参数：

| 参数 | 主 Agent | Subagent |
|---|---|---|
| `messages` | 会话历史（跨任务累积） | `[{"role":"user","content":task}]`——**全新一份** |
| `system` | 基础 prompt + MEMORY.md + Skills 元信息 + 提问指引 + 后台引导 | 角色化 f-string——**只拼角色** |
| `tools` | 全量 9 个 | **只有基础四件套**——plan / use_skill / ask / job_kill / subagent 全不给 |
| 附加 | — | `log_file`（关键步骤落盘留档）+ `stop_event`（job_kill 协作式终止） |

**核心洞察**：Subagent 的"独立性"不是靠新写一个循环，而是靠**传入独立的 messages / system / tools**。同一个 ReAct 循环，喂不同的 context，就是不同的 Agent。这也是对 demo2 的补位：压缩是**事后减负**（messages 涨到阈值才压），Subagent 是**事前隔离**（独立子任务从一开始就不进主 messages）——中间过程零污染，主 messages 只进通知。

**只留四件套**：不给 plan（不需要拆自己的步骤）、不给 use_skill（用不上工作流）、不给 ask（独立干活，不该回头找用户拿主意）、不给 job_kill 和 subagent 自己（防递归、防越权）；execute_bash 虽然保留，但摘掉了 run_in_background——子任务不起后台 job（全局单注册表下，起了通知也会错送主 Agent）。这和 Claude Code 的 Explore / Plan 子代理同款思路：只给完成任务所需的工具。

### 2.4 subagent() 的实现

实现本体（`subagent.py`）——四个编号对应四个关键设计：

```python
def subagent(role: str, task: str) -> str:
    # ① 工具集裁剪：Subagent 只有基础四件套；execute_bash 再摘掉 run_in_background
    #    （子任务不起后台 job——全局单注册表下通知会错送主 Agent）
    tools = [dict(t) for t in ALL_TOOLS
             if t["name"] in ("execute_bash", "read_file", "write_file", "edit")]
    bash = next(t for t in tools if t["name"] == "execute_bash")
    bash["description"] = "执行任意 shell 命令，可用于文件操作、系统命令等"
    bash["input_schema"] = {"type": "object",
                            "properties": {"command": ...原 command 定义...},
                            "required": ["command"]}

    def _run(job, log_file) -> str:
        # ② 独立 messages：全新一份，只装任务描述
        sub_messages = [{"role": "user", "content": task}]
        # ③ 独立 system prompt：角色化 f-string，只拼角色
        sub_system_prompt = (
            f"你是一个被委派来的 Subagent。你的角色是：**{role}**。\n"
            f"请专注于交给你完成的任务，做完后用一两句话汇报结果。"
        )
        return run_react_loop(messages=sub_messages, tools=tools,
                              local_fns=FUNCTIONS, system=sub_system_prompt,
                              verbose=False,                   # 终端只留主 Agent，细节全写日志
                              log_file=log_file, stop_event=job._stop_event)

    # ④ 后台执行：线程里跑子循环，立即返回——完成后通知机制送回最终报告
    job = jobs.REGISTRY.spawn_agent(description=f"{role}：{task[:50]}", run_fn=_run)
    return (f"[Subagent · {role}] 已放入后台运行，job id: {job.id}\n"
            f"输出日志：{job.log_path}（随时可用 read_file 查看）\n"
            "完成后你会收到 <task-notification> 通知（附最终报告），不要原地等待。")
```

四个关键设计：

1. **四件套裁剪**：Subagent 看不到 plan / ask / subagent——想套娃调不到、想打扰用户没工具；execute_bash 还摘掉 run_in_background——子任务不起后台 job（通知会错送主 Agent）
2. **独立 messages**：局部变量，随子循环结束销毁——主 Agent 拿不到内部对话
3. **独立 system prompt**：角色化 f-string，只拼角色
4. **后台执行 + 通知**：子循环跑在线程里，工具立即返回；每一步都写日志（终端不打印，read_file 可查进度）；完成后最终报告挂 job.result——「挂」就是执行线程往 job 对象上赋属性（Thread 没有返回值通道，而注册表和主线程攥着同一个 job 的引用，赋值即送达）——报告随 `<task-notification>` 送回主 Agent

### 2.5 通知闭环（agent.py）

两条投递路径：

- **工作时通知**——run_agent 每次请求前检查注册表，把待通知的 job 结果作为 user 消息追加进 messages，搭下一次请求的车
- **空闲时通知**——输入等待被改造成事件轮询：主循环同时等「用户输入」和「job 全部完成」。job 完成 → 自动唤醒跑汇报轮；等待期间用户随时可打字继续聊（消息排队，Agent 处理完自动接上；TTY 下用可取消的 prompt_async，管道下用裸 input 线程）

以上两条都是**给模型的**，还有第三条通道给人——终端里 `✔ [后台] 后台命令「xx」执行成功` 那行。它走 `on_complete` 回调：`REGISTRY` 是模块级单例（`jobs.REGISTRY = JobRegistry()`，import 缓存保证各文件拿到同一份），回调槽留空、agent.py 启动时注入 `_job_completed`（与 client 注入同款模式，解反向依赖）。回调里查 `_main_busy` 标志：主循环正跑轮次 → 提示攒进 `_pending_notices`，轮末 finally 补打（不插进输出中间）；空闲 → 直接打。**这条通道不进 messages**——屏幕给人，notification 给模型，同一时刻各走各的。

另一个体验细节：等待期间输入框常驻底部（行内渲染 TUI——输出直通终端滚动区、输入行原位重绘，对齐 Claude Code 的策略；不是机制核心，`tui.py` 职责见 [§1.1](#11-代码层的变化)）。

自动唤醒的轮询骨架（agent.py 主循环）：

```python
# 输入来源：TTY 由 TUI 输入框投递，管道由 input 线程投递
input_queue = Queue()
threading.Thread(target=_input_reader, daemon=True).start()

# 主循环轮询：等用户输入，或等后台 job 全部完成（自动唤醒）
while True:
    try:
        user_input = input_queue.get(timeout=0.5)
        if jobs 全部完成且待通知:            # 完成通知优先于排队输入
            input_queue.put(user_input)      #   输入放回，下一轮处理
            user_input = WAKEUP
        break
    except queue.Empty:
        if jobs.REGISTRY.has_unnotified() and not jobs.REGISTRY.has_running():
            user_input = WAKEUP              # 自动唤醒：通知在 run_agent 开头注入
            break
```

### 2.6 Claude Code 对照：Task 工具、/btw 与 Fork agent

Claude Code 的多 Agent 能力全部是 Subagent 形态，demo5 的 subagent 对标其中的 **Task 工具**——后台派一次性 subagent，独立 context，完成通知带回报告，同构。

干活期间的插话是另一个话题，Claude Code 有两条通道。普通消息：排队后在工具调用间隙**注入当前轮**（进对话历史、影响主任务）；`/btw`：干脆开旁路——一次独立请求，带上全量对话快照（正在写的那条回复除外）、**无工具**、**问完不进对话历史**。demo5 两条都不做，当前只实现了**纯后台模式**——干活期间打字只进队列，当前轮跑完、回到等待才被读取。

`/btw` 的回答上按 `f`，还能把这份问答升级成 **Fork agent**——Claude Code 较新引入的变体：共享上下文的 Subagent。Task 工具的 Subagent 从零开始（独立 context），适合完全独立的子任务；Fork agent 从主 Agent 的当前 context **分叉**出去——共享已有信息，只隔离后续过程，适合"基于当前进展去做探索/验证"的场景。

独立 context 与共享 context 由此构成一条谱系：

| | 独立 context（Task / demo5 subagent） | 共享 context（Fork agent） |
|---|---|---|
| **起始状态** | 从零开始，只装任务描述 | 分叉主 Agent 的当前 messages |
| **适合** | 相互独立的子任务 | 基于当前进展的探索 / 验证 |
| **代价** | 已有信息要重新交代 | 分叉点前的信息复制成本（或按需读） |
| **demo5 实现** | ✅ `subagent()` | ❌ 代码不实现，只讲机制 |

这条谱系的价值在于：**"多 Agent"的本质不是"多个模型对话"，而是"多份 context 的隔离与共享策略"**。

---

## 3. 真实演示案例

### 3.1 场景 A：耗时命令自动放后台

**用户输入**：

> 跑一下 python -c "import time; time.sleep(20); print('BUILD SUCCESS')"，结束后告诉我输出了什么

**关键轨迹**：

```
第 1 轮   ⏺ tool_call   | execute_bash({'command': 'python -c "import time; ..."',
                                           'run_in_background': True})
                        ← LLM 判断耗时命令，自动放后台（后台引导生效）
         ✔ tool_return | 已放入后台运行，job id: bf06dxh15
                          输出日志：jobs/bf06dxh15.log（随时可用 read_file 查看）
                          完成后你会收到 <task-notification> 通知，不要原地等待。

第 2 轮   stop_reason = end_turn
         ● assistant   | 已在后台执行，完成后我会收到通知并告诉你输出内容。

（主循环空闲等待——提示符可用，期间随时可聊）
         ✔ tool_return | [后台] 后台命令执行成功，exit code 0

（job 完成 → 通知注入，第 3-4 轮自动汇报）
         ✔ tool_call   | read_file('jobs/bf06dxh15.log') → BUILD SUCCESS
         ● assistant   | 命令执行完毕（exit code 0），输出为：BUILD SUCCESS
```

**关键观察**：

- **LLM 自己判断放后台**——20 秒延时明显耗时，工具描述 + 系统引导让它自动用 run_in_background=True
- **这轮对话不卡**——派完立即 end_turn，20 秒的等待发生在主循环之外
- **通知就是一条 user 消息**——`<task-notification>` 塞进 messages，LLM 读到后自己 read_file 日志、汇报结果

### 3.2 场景 B：三个独立子任务 → 三个后台 Subagent

**用户输入**：

> 帮我做三件事，每件都派一个专门的 Subagent 完成：1) 统计当前目录下每个 py 文件的行数，找出行数最多的一个；2) 写一个脚本计算斐波那契数列第 20 项并运行；3) 查询北京明天的天气并给出穿衣建议

**关键轨迹（主 Agent 共 4 轮：2 轮派发 + 2 轮自动汇报）**：

```
第 1 轮
  ⏺ tool_call   | subagent(文件统计员) + subagent(Python 工程师) + subagent(天气查询助手)
                 三个 job 并行放入后台（a 前缀 id），各自日志落盘

第 2 轮   stop_reason = end_turn
  ● assistant   | 三个 Subagent 已并行启动，完成后我会汇报结果。

（三个 Subagent 在后台各自干活——中间过程**不打印终端**，全部写进各自 jobs/<id>.log，
  read_file 随时可查：文件统计员 4 轮 → 12 个 .py，行数最多 ask.py（379 行）
  Python 工程师 3 轮 → 6765，脚本已删除
  天气查询助手 6 轮 → 27/18°C 烟霾，降水概率最高 8% + 穿衣建议）

（先完成的 job 陆续在终端冒出 ✔ [后台] 提示——等待期间一轮一轮往上跳）

（三个 job 全部完成 → 三条 <task-notification> 注入 → 第 3-4 轮自动汇报）
  ● assistant   | 三件事全部完成，汇总：文件统计 / 斐波那契 6765 / 天气与穿衣建议
```

**关键观察**：

1. **并行后台派发**——一轮派 3 个 job，立即 end_turn，主 Agent 的等待被完全消除
2. **中间过程零污染**——三个 Subagent 的几十轮工具调用全在独立 context 里，主 messages 只进三条通知
3. **日志留档**——每个 job 一份 `jobs/<id>.log`，随时可查、跑完不丢
4. **通知带回最终报告**——`<result>` 字段直接装报告，主 Agent 不必再读日志
5. **彩蛋：记忆与多 Agent 的协同**——看主 Agent 派发的 task 描述：里面自带「Windows shell 管道会报错，先写 .py 文件再运行」「curl 中文会乱码，先存文件再用 Python 读」——这是 MEMORY.md 里沉淀的环境经验，主 Agent 把它写进了每个 Subagent 的任务说明

### 3.3 场景 C：终止后台任务（job_kill 强杀）

**用户输入**：

> 跑一下 python -c "import time; time.sleep(300)"，放后台就行，不用等它

**关键轨迹（主 Agent 共 4 轮：2 轮派发 + 2 轮终止）**：

```
第 1 轮   ⏺ tool_call   | execute_bash({'command': 'python -c "import time; time.sleep(300)"',
                                           'run_in_background': True})
         ✔ tool_return | 已放入后台运行，job id: btn9pjbwr
                        输出日志：jobs/btn9pjbwr.log
第 2 轮   ● assistant   | 已放后台，job id 是 btn9pjbwr，300 秒后自动结束，到时会有通知。
```

**用户输入**：

> 这个不用跑了，停掉吧

```
第 3 轮   ⏺ tool_call   | job_kill({'job_id': 'btn9pjbwr'})
         ✔ tool_return | job btn9pjbwr 已终止
第 4 轮   ● assistant   | 已停掉，后台任务 btn9pjbwr 已终止。

（kill 走 taskkill /T /F 杀掉整棵进程树；被杀的 shell job 不发完成提示——
  终端从始至终没有 [后台] 行；kill 顺手标记 notified=True——终态已随 tool_return
  送达，不触发唤醒轮，第 4 轮就是最后一句）
```

**关键观察**：

1. **强杀路径**——shell job 的终止是 `taskkill /T /F` 杀整棵进程树（`jobs.py` 的 `_kill_process_tree`），立即生效；watcher 见 status 已是 killed 直接早退，不发完成提示
2. **kill 与自然完成的 notified 分野**——kill 时模型已经知道终态（调用就是它发的，tool_return 即终态），`kill()` 顺手标记 `notified=True`，不触发唤醒轮；自然完成 / 失败相反——派发时的 tool_return 不含结果，通知才是结果的首次送达，`notified` 留到通知发出
3. **两种终止语义**——shell job 杀进程（强杀），agent job 置 stop_event（协作式，子循环每轮检查；正在跑的 LLM 请求 / 工具调用不打断）。进程能杀、线程不能——这是 kill 按 kind 分两条路的原因

---

## 4. 总结 + 视野拓展

demo5 = demo4 底座 × 多 Agent。能力演进：

```
ReAct       = 会行动
  + 记忆    = 会记得过去（demo2）
  + MCP     = 会接外部工具（demo3）
  + 规划    = 会问、会拆、会复用（demo4）
  + Multi   = 会外包（demo5）
```

收束成一句（呼应 [§2.6](#26-claude-code-对照task-工具btw-与-fork-agent) 的谱系）：多 Agent 的本质不是"让多个模型聊天"，而是**多份 context 的隔离与共享策略**——demo5 实现了独立 context 一支（Subagent），共享 context 的 Fork 变体作为视野拓展、代码不实现。理解了 context 的组织方式，就理解了多 Agent 系统的全部花样。

> 轴覆盖的权威矩阵见仓库根目录 [README](../README.md)「轴清单 × 内容矩阵」。
