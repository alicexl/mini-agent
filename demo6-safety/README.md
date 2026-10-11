# Demo6 — 约束轴

> 在 demo5 底座（= demo4 × 多 Agent，一路继承到 demo1，只不带 demo3 的 MCP）上叠加「约束轴」：在 LLM 决策和原始工具之间插入**两层 Runtime Control**——可配置、可观测、可拦截，而不是硬编码在某个工具函数里。
>
> 公式：`demo6 = demo5 底座 × 约束`

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（5 章 + 附录）
  1. 结论：demo6 多了什么（危险从哪来 + dispatch_tool 四步流程 + 主/子一体受控）
  2. Permission：声明式规则引擎（allow / deny / ask + ask 层弹层）
  3. Hook：PreToolUse / PostToolUse 事件回调（改 input / 改 output）
  4. 审批冒泡：约束 × 多 Agent 的交叉点（挂起 + 队列 + 空闲弹层）
  5. 局限、演进和总结（配置化 / 审批自动化 / Sandbox / Pi 流派）

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 主入口：客户端 + 主循环（调 `subagent.run_react_loop`）+ 轮询循环（通知唤醒 + 审批冒泡） |
| `tools.py` | 工具层（demo1 四件套；execute_bash 加 `run_in_background`，继承 demo5） |
| `plan.py` / `skill.py` / `ask.py` | 规划轴三件（继承 demo4；ask.py 抽出 `collect()` 供确认层复用） |
| `subagent.py` | 多 Agent 层：subagent 工具 + 主/子共用 ReAct 循环（继承 demo5；工具执行改走 `dispatch_tool`） |
| `jobs.py` | 后台任务层：Job 注册表 + 日志 + 通知 + job_kill（继承 demo5） |
| `tui.py` | TUI 层：行内渲染界面 + ask 弹层（继承 demo5） |
| `permission.py` | **约束层（★ demo6 新增）**：Permission 规则引擎 + Hook + `dispatch_tool` + 审批冒泡 |
| `render.py` / `memory.py` / `session.py` / `commands.py` | 渲染 / 记忆 / 会话 / 命令层（继承 demo2-5） |
| `test_dir/` | 演示材料（启动时自动生成 5 个文件，含明文凭证 db.conf，已 gitignore） |
| `memory/` / `jobs/` | 运行时生成（已 gitignore；jobs/ 启动时自动清理上次遗留） |

## 设计要点

### 两层 Runtime Control（dispatch_tool 串接）

每次工具调用按顺序过两层，任一层阻断都返回错误给大模型：

| 层 | 抽象 | 实现 | 示例 |
|---|---|---|---|
| **Hook**（扩展层 / Lifecycle Extension） | 事件回调 | `HOOKS` 注册表 + Python callable | Pre 注入（写 `.sh` → 补 shebang，改 input）；Post 脱敏（命中密钥 → `[xxxxx]`） |
| **Permission**（策略层） | 准入决策 | `PERMISSION_RULES` + fnmatch，first-match | `rm -rf *` → deny；`ls *` → allow；其余 → ask |

接入点只有一处：**共用 ReAct 循环的工具执行点**——主 Agent 的 9 个工具和 Subagent 的四件套走同一个安检口，不存在子 Agent 旁路。

### 审批冒泡（Subagent 的 ask）

| 触点 | 线程 | ask 行为 |
|---|---|---|
| 主 Agent 前台命令 / 起后台命令 | 主循环 | 同线程直接弹确认层（复用 ask 层 UI） |
| **Subagent 内部工具** | **后台线程** | 入队 + `Event` 挂起 → 主循环空闲心跳弹层 → 结果回填唤醒（对齐 Claude Code） |
| 后台 job 进程执行 | 进程 | 无触点——权限只管入口 |

边界：挂起中被 `job_kill` → 醒来按拒绝；管道模式（无交互 UI）→ 自动拒绝；「本会话全允许」由确认层第三选项置位，`/new` 重置。

> 真正的执行隔离（Docker / microVM）属于 Execution Environment，在调用链路之下，不在本 demo 范围——见讲稿第 5 章。

### 与 demo5 的差异

- **新增 `permission.py`**（约束层）+ **subagent.py 一处改动**（工具执行走 dispatch_tool，`run_react_loop` 加 `origin` 参数）+ **agent.py**（心跳加审批分支）+ **ask.py**（抽 `collect()`）+ **commands.py**（/new 重置全允许标记）——其余八个文件逐字节相同
- 工具集不变（9 个）——约束不是新工具，是工具的安检口

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`anthropic` / `rich` / `prompt_toolkit`。

### 配置 API Key（同 demo2-5）

- **环境变量** `ANTHROPIC_API_KEY`（优先，可持久化）
- 未设 → 启动时交互式输入（仅本次有效）

> 注意：与 harness 等其他 provider 共存时需 `env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL ...` 清掉冲突变量（见工作区 CLAUDE.md）。

### 启动

```bash
python -X utf8 agent.py
```

启动时自动生成 `test_dir/`（5 个文件）并打印 Permission / Hook 概览。命令：`/help` 查看全部，`/quit` 退出（同 demo5）。

### 演示场景

| 演示 | 演示什么 | 操作 |
|---|---|---|
| **1 Permission deny** | 命中 deny 直接拦 | 「把 test_dir 目录删了」 → `rm -rf` 被拦，LLM 收到拒绝文本换路子 |
| **2 Permission allow** | 白名单免确认 | 「看看 test_dir 目录下有什么文件」 → `ls` 静默放行 |
| **3 Permission ask（主）** | 前台弹层确认 | 「跑一下 python -c "print(123)"」 → 三选项弹层（允许 / 拒绝 / 本会话全允许） |
| **4 审批冒泡** | 约束 × 多 Agent | 「派一个 Subagent 去跑 python -c "print(456)" 并告诉我结果」 → 主 Agent 派发后，子 Agent 挂起等审批 → **空闲时弹层**（标题带 Subagent 角色）→ 允许则子 Agent 跑完通知回传；拒绝则它把拒绝写进报告，主 Agent 读报告后可提议接管（接管时再弹一次） |
| **5 Hook 注入** | Pre 改 input | 「在 test_dir 写个 hi.sh，内容是 echo hello」 → 文件实际多了 `#!/bin/bash`，LLM 浑然不觉 |
| **6 Hook 脱敏** | Post 改 output | 「读一下 test_dir/db.conf」 → 密钥被替换成 `[xxxxx]` + 已脱敏提示，真值不进 LLM 视野 |

> **管道自动跑**：`printf '任务\n/quit\n' | python -X utf8 agent.py`——注意管道模式下审批冒泡**自动拒绝**（无交互 UI），演示 deny / allow / Hook 均可，演示 3 / 4 需真终端。

### 可调参数（permission.py）

| 参数 | 默认 | 含义 |
|---|---|---|
| `PERMISSION_RULES` | 见文件 | 声明式规则表，first-match wins |
| `DEFAULT_POLICY` | `"ask"` | 无规则命中时的默认 action |
| `HOOKS` | Pre×1 + Post×1 | PreToolUse / PostToolUse 注册表 |
| `_REDACT_RE` | password/api_key/secret | Post 脱敏识别的 key 名（大小写不敏感） |
