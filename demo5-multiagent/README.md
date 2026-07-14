# Demo5 — 多 Agent 轴

> 在 demo1-react（base）上独立叠加「多 Agent 轴」：两条机制对照讲解——`agent_sub.py`（Subagent 一次性外包，70% 权重） + `agent_team.py`（Team 持久项目组，30% 权重）。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（6 章）
  1. 结论：demo5 vs demo1（两条机制 Subagent + Team）
  2. 为什么需要多 Agent（单 Agent 上下文膨胀痛点 / 独立 vs 有依赖场景）
  3. 机制一：Subagent（一次性外包）—— 共用 `_react_loop` + 递归防护 + 真实案例
  4. 机制二：Team（持久项目组）—— Agent 类升级 + 状态机流水线 + 质检总闸门 + 真实案例
  5. Subagent vs Team 何时用（对照表）
  6. 总结与下一节预告（demo6 约束/安全）

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent_sub.py` | Subagent 一次性外包（Part 1-4：客户端 / 工具定义（含 subagent）/ 工具实现 + 路由表 / 主循环 + Subagent 循环共用 `_react_loop`） |
| `agent_team.py` | Team 持久项目组（Part 1-6：客户端 / 工具定义 / 工具实现 / **Agent 类** / **Team 类 + 状态机** / 交互式入口） |
| `讲稿.md` | 教学讲稿（对照 Subagent vs Team） |

## 设计要点

### Subagent（agent_sub.py）

- demo1 的 3 件套（execute_bash / read_file / write_file）保留不变
- 新增 `subagent` 本地工具——主 Agent 遇到相互独立的子任务时派一个一次性 Subagent
- **关键设计**：
  - **独立 context**：Subagent 有自己的 messages，与主 Agent 完全隔离
  - **无状态**：不注入 Rules / 不注入记忆
  - **结束即销毁**：循环结束返回结果摘要，messages/prompt 全部丢弃
  - **工具集去 subagent**：子循环看不到 subagent 工具，防无限递归
- **共用 `_react_loop`**：主 Agent 和 Subagent 跑同一个 ReAct 循环，差别只在传入的 messages/tools/system_prompt 是否独立——这就是"独立性"的本质
- **subagent 不在路由表**：它需要"启动一个独立 ReAct 循环"的特殊逻辑，在 `_react_loop` 里单独拦截
- 对应 Claude Code 的 Task tool / Cursor 的 agent / Devin 的子任务派发

### Team（agent_team.py）

- demo1 的 3 件套保留不变；**不含 subagent 工具**——Team 的协调全靠外部编排器（Team 类），不靠 LLM 调 subagent 工具
- **Agent 类升级**：从"一次性函数"升级为"持久化对象"
  - `self.name` / `self.role`：固定身份（Subagent 是临时拼角色）
  - `self.messages`：长期记忆，跨多次 `chat()` 累积
  - `self.inbox`：收件箱，可被其他 Agent 塞消息
  - `chat(task)`：消化 inbox → 追加 task → 走 ReAct 循环
- **Team 类 4 个核心动作**：`recruit` / `send` / `broadcast` / `dismiss`
- **固定三角色流水线**（不靠 LLM 动态规划）：
  - Researcher（研究员）：调研主题，输出要点列表
  - Writer（撰稿人）：基于要点写结构化研究报告（markdown）
  - Reviewer（质检员）：验收报告，不通过则打回重做（最多 3 次）
- **状态机**（任务级，不是 Agent 级）：
  ```
  researching → writing → reviewing → ┬→ passed (终态)
                                     └→ redoing → writing → reviewing → ...
                                         (3 次不过 = failed)
  ```
- **质检员 JSON 解析**：Reviewer 必须输出严格 JSON `{"pass": true|false, "feedback": "..."}`；解析失败默认不通过，原文塞进 feedback
- 对应 AutoGen / CrewAI 范式

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单（`anthropic`）。

### 配置 API Key

**推荐：环境变量**（避免 Key 进 git 历史）

```bash
# Git Bash
export ANTHROPIC_API_KEY="你的智谱 BigModel Key"
python agent_sub.py    # 或 python agent_team.py
```

或者改 `agent_sub.py` / `agent_team.py` Part 1 顶部的 `API_KEY = ""`（不推荐——会被 git track）。

默认走智谱 BigModel 的 Anthropic 兼容网关（`https://open.bigmodel.cn/api/anthropic`）+ `glm-5.2` 模型，换官方 API 或其他兼容网关只需改 `BASE_URL` / `MODEL`。

### 启动 Subagent 演示

```bash
python agent_sub.py
```

启动后进入交互模式。建议输入两个独立子任务：

```
请完成下面两个相互独立的子任务：
1) 统计 demo5-multiagent 目录下 .py 文件的数量
2) 读 demo5-multiagent/agent_sub.py 文件第 1 行注释
```

观察主 Agent 派 2 个 Subagent——各自独立 messages、结束即销毁。

### 启动 Team 演示

```bash
python agent_team.py
```

启动后输入任意研究主题（如 `Python 的 GIL 是什么`），观察：
- 三角色招募（Researcher / Writer / Reviewer）
- inbox 通信（Researcher → Writer → Reviewer）
- 状态机流转（researching → writing → reviewing → passed）
- 质检 JSON 解析（通过 / 打回重做）
- 报告落盘到 `<主题>.md`

`quit` / `exit` / `q` 退出。

### 运行时产物

- `<主题>.md` —— Team 流水线生成的研究报告（主题作为文件名，特殊字符替换为 `_`）
