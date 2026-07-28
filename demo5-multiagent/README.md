# Demo5 — 多 Agent 轴

> 在 demo1-react（base）上独立叠加「多 Agent 轴」：两条机制对照讲解——`agent_sub.py`（Subagent 一次性外包，70% 权重） + `agent_team.py`（Team 持久项目组，30% 权重）。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（4 章 + 附录）
  1. demo5 干了什么（两条机制 Subagent + Team）
  2. 机制一：Subagent（一次性外包）—— 共用 `_react_loop` + 递归防护 + 真实案例
  3. 机制二：Team（持久项目组）—— Agent 持久对象 + 消息队列 + `[send:]` 路由
  4. Subagent vs Team 对比与总结（对照表 + 终端截断 + 能力演进）

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent_sub.py` | Subagent 一次性外包（Part 1-4：客户端 / 工具定义（含 subagent）/ 工具实现 + 路由表 / 主循环 + Subagent 循环共用 `_react_loop`） |
| `agent_team.py` | Team 持久项目组（Part 1-5：客户端 / 工具定义（含 get_weather + plan_team）/ 工具实现 + Team 基础设施（**Agent 类** + **Team 类** + 消息队列 + `[send:]` 路由）/ 主循环 / 交互式入口） |
| `讲稿.md` | 教学讲稿（对照 Subagent vs Team） |

## 设计要点

### Subagent（agent_sub.py）

- demo1 的 4 件套（execute_bash / read_file / write_file / edit）保留不变
- 新增 `subagent` 本地工具——主 Agent 遇到相互独立的子任务时派一个一次性 Subagent
- **关键设计**：
  - **独立 context**：Subagent 有自己的 messages，与主 Agent 完全隔离
  - **无状态**：不注入 Rules / 不注入记忆
  - **结束即销毁**：循环结束返回结果摘要，messages/prompt 全部丢弃
  - **工具集去 subagent**：子循环看不到 subagent 工具，防无限递归
- **共用 `_react_loop`**：主 Agent 和 Subagent 跑同一个 ReAct 循环，差别只在传入的 messages/tools/system_prompt 是否独立——这就是"独立性"的本质
- **subagent 在路由表里**：和其它本地工具一样被 `_react_loop` 路由分发，唯一特殊逻辑在 `subagent()` 内部启动子循环时过滤自身防递归

### Team（agent_team.py）

- demo1 的 4 件套保留不变；新增 `get_weather`（教学模拟天气数据，不联网）+ `plan_team` 本地工具——**不含 subagent 工具**，Team 的协调靠 Team 类的消息队列，不靠 LLM 递归调 subagent
- **`plan_team` 工具**：LLM **只拆角色**（每个成员的 name + role），任务由用户原始输入整体驱动、传给第一个被唤醒的成员
- **Agent 类**：持久化对象（对比 Subagent 的"一次性函数"）
  - `self.name` / `self.role`：固定身份（Subagent 是临时拼角色）
  - `self.messages`：长期记忆，跨多次 `chat()` 累积
  - `self.system_prompt`：含完整团队名册 + 两条规则
  - `chat(event)`：把收到的消息追加进 messages → 走 ReAct 循环（工具集去掉 plan_team 防递归）
- **Team 类 4 个核心动作**：`recruit`（招募·只注册）/ `initialize`（初始化·registry 完整后统一构建 system_prompt）/ `send`（消息入队）/ `run`（事件循环）。recruit 与 initialize 拆成两阶段——保证拼 prompt 时团队名册一定完整，不在 `Agent.__init__` 里拼残缺名册
- **`[send: 成员名]` 路由协议**：Agent 不能完成时，在回复里写 `[send: 合适的人]`，`Team.run` 用 `re.search` 扫描全文解析（兼容 LLM 不把标记写在开头，常先分析再转交）后转交目标成员——路由决策交 LLM，路由执行交协调器
- **`plan_team` 是终端委派工具**：主 Agent 调用后，`run_agent` 直接 `return` 团队结果、不进下一轮——用控制流而非 prompt 保证主 Agent 不越权重做团队的活（subagent 不截断，因为派完还要汇总多个子结果）
- **路由稳定性依赖模型能力**：成员「做完本职主动 `[send:]` 转交剩余」靠 LLM 自觉——指令遵循弱的模型（如 glm-5.2）偶尔断链（口头说"请查收"却不写标记），机制本身没错，更强模型会更稳
- **随机起手**：`plan_team` 随机挑一个成员接收任务，不匹配则 `[send:]` 转交，用于展示任务在成员间流转

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

启动后输入任意需要多角色协作的任务（如 `用 Python 写一个猜数字小游戏并审查代码质量`），观察：
- LLM 调 `plan_team` 拆出多个角色（name + role）
- 消息队列启动、随机挑一个成员起手
- `[send: 成员名]` 路由：起手成员转交给更合适的成员
- Agent 持久 `self.messages`：被 `[send:]` 唤醒的成员带着自己的 context 工作

`quit` / `exit` / `q` 退出。

### 运行时产物

- 无固定产物——Team 不自动落盘，最终结果由 `plan_team` 作为字符串返回主 Agent（若 LLM 在执行中调 `write_file` 则按其意愿产生文件）
