# Demo5 — 多 Agent 轴

> 在 demo4（base × 规划）基础上叠加多 Agent 轴——**subagent（独立子任务分包：派一次性 Subagent 在后台执行，独立 context，完成通知带回最终报告）** + **jobs（后台任务机制：shell/agent 两种 job 共用注册表，日志落盘 + `<task-notification>` 通知注入，对齐 Claude Code）**。继承规划轴三件（plan / Skill / ask_user_question，全部自包含）+ 记忆能力（无自动压缩，同 demo3/4）。底座取舍判据：**自包含的机制照带，依赖外部服务的机制不带**（demo3 的 MCP 依赖模拟 server，不带）。
> 拓展视野：共享上下文的 Fork agent 变体在讲稿里讲机制、代码不实现——demo5 只实现独立 context 的 Task 同款。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（4 章）
  1. 结论：demo5 比 demo2 多了什么
  2. Subagent：独立子任务的分包（主/子同构 + Claude Code 对照 + Fork agent 谱系）
  3. 真实演示案例
  4. 总结 + 另一条多 Agent 路线

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 主入口：客户端 + 主循环（depth=0 调 `subagent.run_react_loop`）+ 通知注入 + 交互入口 |
| `tools.py` | 工具层（demo1 四件套；execute_bash 加 `run_in_background` 参数） |
| `plan.py` | 规划层：plan 工具（继承 demo4） |
| `skill.py` | Skill 层：Skill 加载器 + use_skill（继承 demo4） |
| `ask.py` | 提问层：ask_user_question 方向键 UI + 非 TTY 降级（继承 demo4） |
| `subagent.py` | **多 Agent 层**：subagent 工具（后台执行）+ 主/子共用 ReAct 循环（demo5 新增） |
| `jobs.py` | **后台任务层**：Job 注册表（shell / agent 两种）+ 日志 + 通知 + job_kill（demo5 新增） |
| `tui.py` | **TUI 层**：全屏界面——日志区滚动 + 底部常驻输入框 + ask 弹层（demo5 新增，仅 TTY） |
| `render.py` | 渲染层（demo2 版；TUI 模式下输出经重定向进日志区） |
| `memory.py` | 记忆层（demo2 版减自动压缩） |
| `session.py` | 会话层：`memory/<会话ID>.jsonl` 持久化 + `/resume` |
| `commands.py` | 命令层：`/help` `/status` `/tools` `/skills` `/memory` `/resume` `/new` `/compact` `/quit`（/new 补 job 清理） |
| `skills/review.md` | 示例 Skill——代码审查工作流（继承 demo4） |
| `jobs/` | 运行时生成的后台任务日志目录（已 gitignore） |
| `memory/` | 运行时生成的双层记忆目录（已 gitignore，同 demo2） |

## 设计要点

### 与 demo4 的差异

- **新增 `subagent.py`**（多 Agent 层）+ **新增 `jobs.py`**（后台任务层）+ **agent.py 微调**（工具合并为 9 个、通知注入 + 空闲等待汇报轮）+ **tools.py 微调**（run_in_background 参数）+ **render.py 微调**（indent 参数）
- **无自动压缩**（同 demo3/4）：压缩只由 `/compact` 手动发起

### jobs：后台任务机制

- 耗时命令 / Subagent 一律可放后台：起进程（Popen）/ 线程不等它结束，立即返回 job id + 日志路径
- 输出落盘 `jobs/<id>.log`——边跑边写，read_file 随时可查
- 完成后 `<task-notification>` 通知注入下一次请求的 messages；输入等待为事件轮询——job 完成自动唤醒汇报，等待期间用户随时可聊（消息排队；TTY 用可取消的 prompt_async，管道用裸 input 线程）
- `job_kill` 工具：shell job 终止进程树（taskkill /T），agent job 协作式停止
- 退出 / /new 时清理全部运行中 job

### Subagent：独立子任务分包（后台执行）

- LLM 判断任务相互独立时，调 `subagent(role, task)` 派后台 Subagent（描述里写了正向触发 + 反向抑制）
- **主/子同构**：主 Agent 与 Subagent 共用 `run_react_loop`，差别只在传入的 messages / system / tools
- **工具集只有基础四件套**：plan / use_skill / ask / job_kill / subagent 全不给——防递归、独立干活不回头问用户
- **Claude Code 对照**：对标 Task 工具；Fork agent（共享上下文变体）在讲稿里讲机制、代码不实现

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`anthropic` + `rich` + `prompt_toolkit`。

### 配置 API Key

环境变量 `ANTHROPIC_API_KEY`（推荐，可持久化）；未设时启动交互式输入。

### 启动 Agent

```bash
python -X utf8 agent.py
```

演示建议：

- **后台命令**：输入「跑一下 python -c "import time; time.sleep(20); print('BUILD SUCCESS')"，结束后告诉我输出了什么」——LLM 自动判断耗时命令放后台，派完即回，完成后通知自动触发汇报（不用 sleep 命令——Windows cmd 没有）
- **Subagent**：输入「帮我做三件事，每件都派一个专门的 Subagent 完成：1) 统计当前目录下每个 py 文件的行数，找出行数最多的一个；2) 写一个脚本计算斐波那契数列第 20 项并运行；3) 查询北京明天的天气并给出穿衣建议」——主 Agent 并行派 3 个后台 Subagent（嵌套轨迹 + 日志落盘），完成后三条通知注入、自动汇总

> **管道自动跑**：`printf '任务\n/quit\n' | python -X utf8 agent.py`

### 可调参数（`memory.py` 顶部，同 demo2/3/4）

| 参数 | 默认 | 含义 |
|---|---|---|
| `MEMORY_WINDOW_LINES` | 50 | MEMORY.md 加载进 system prompt 的行数上限（防止上下文无限增长） |
| `COMPACT_KEEP_RECENT` | 4 | `/compact` 时保留最近 N 条原始消息 |
| `USE_CACHE_CONTROL` | True | 是否启用 prompt caching |

> 另有 `USE_THINKING` 思考模式开关（`subagent.py` 顶部，默认关闭）。
