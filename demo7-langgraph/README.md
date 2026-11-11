# Demo7 — 框架对照篇：把 demo1 交给 LangGraph

> demo(N) = base × 轴N；demo7 = base × **框架**。它不新增能力——同一个 base（demo1 的四件套工具 + ReAct 心跳），换 [LangGraph](https://langchain-ai.github.io/langgraph/) 重新表达一遍，看清框架**帮你做了什么、藏了什么、没接管什么**。
>
> 代码实现三条轴的对照（循环 / 约束 / 记忆），其余三条（工具 / 规划 / 多 Agent）给概念映射。前置：先学 [demo1](../demo1-react/README.md)（base）。

---

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt    # langgraph + langchain-anthropic + checkpoint-sqlite
```

### 配置 API Key（三级回退，本 demo 新增 .env 文件）

1. **`.env` 文件（推荐，本 demo 新增）**：复制 `.env.example` 为 `.env`，填入 Key——文件已被 .gitignore 覆盖，不会进 git

   ```bash
   cp .env.example .env    # 然后编辑 .env：ANTHROPIC_API_KEY=你的智谱Key
   ```

2. 环境变量（优先级更高，可覆盖 .env）：`export ANTHROPIC_API_KEY=xxx`
3. 都没配则启动时交互式输入（仅本次有效）

Key 格式 `id.secret`，在 bigmodel.cn 生成；网关与模型已写死（`BASE_URL` / `MODEL` 常量）。

### 启动

```bash
python -X utf8 agent.py
```

进入交互模式后输入任意任务。命令：`/help` 可用命令 / `/status` 会话状态 / `/resume` 接续历史会话 / `/quit` 退出。

> **注 1**：`-X utf8` 防止 GBK Windows 控制台中文乱码；管道（非终端）下输入自动降级 `input()`、输出自动去色，`printf '任务\n/quit\n' | python -X utf8 agent.py` 可自动跑完。
>
> **注 2**：管道运行时审批门**自动拒绝**（deny）——安全优先，恰好完整演示 deny 分支。
>
> **注 3**：与 demo1-6 相同的坑：若环境里残留 Claude Code harness 注入的 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`，会与 `.env` 的 Key 形成**双 auth header** 冲突（401）。demo 外环境运行须 `env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY` 前缀清掉，详见仓库 CLAUDE.md。

---

## 1. 为什么有对照篇

demo1–6 手搓了一个完整的 Agent Runtime：循环、记忆、工具、规划、多 Agent、约束，六条轴全是自己写的 `while` / `open()` / `if`。工业界走 Agent 路线的另一条路是**用框架**——LangChain 生态的 LangGraph 是当下最主流的一个。

对照篇回答三个问题：

1. **手搓的东西在框架里叫什么**——`run_agent` 的 while 循环变成一张 StateGraph；demo2 手搓的 jsonl 落盘变成 Checkpointer；demo6 的 ask 冒泡变成 `interrupt()`。
2. **框架包了你哪些活**——状态合并、每步落盘、暂停/恢复、递归上限……这些原本要自己写（或根本没写）的工程件，框架全管了。
3. **哪些活框架不管**——上下文压缩、prompt caching、审批规则表……手搓轴里沉淀的经验并没有作废。

一个诚实的先行结论（先亮出来，§5 有数据）：**框架并没有让代码变少**——demo7 的图核心 98 行 ≈ demo1 主循环 95 行，行数打平。换来的是：这 98 行里同时装进了循环（demo1）、审批门（demo6）、每步自动落盘（demo2 只做了会话级）三件事，且全部经过框架的工程化测试。

---

## 2. LangGraph 心智模型：图是循环的推广

五个概念就够了：

| 概念 | 是什么 | 对应手搓版的什么 |
|---|---|---|
| **State** | 一张随执行不断更新的状态表（本 demo：`messages` + `gate_decision`） | demo1 的 `history` 列表 |
| **Node** | 一个普通函数：读 state、返回 state 的增量 | while 循环体里的一步（决策 / 执行工具） |
| **Edge** | 节点间的连线；**条件边**按 state 决定去哪个节点（含 `END`） | `stop_reason != "tool_use"` 的判停 `if` |
| **Checkpointer** | 每个超级步结束自动把 state 快照落盘，按 `thread_id` 隔离 | demo2 的 `memory/<会话ID>.jsonl`（手搓、会话级） |
| **interrupt** | 节点内调 `interrupt(payload)` → 整张图**暂停落盘**，等人用 `Command(resume=值)` 回答后原地继续 | demo6 的 ask 审批冒泡（REPL 里 `input()` 硬等） |

demo7 的整张图：

```
START → llm ──(无 tool_calls)──→ END              ← 判停出口（对照 stop_reason）
          └─(有)→ gate ──(allow)→ tools ──→ llm    ← 循环回环（对照 while）
                   └─(deny)──→ llm                ← 拒绝也回环（模型换路子）
```

关键转念：**循环是图的特例**。demo1 的 while 只有一条回环路径；图把「下一步去哪」从代码位置（循环体顺序）改成显式声明（条件边），于是加节点、加分支、加暂停点都不再需要改循环结构——demo7 的审批门就是往现有图里**插了一个节点**，主流程一行没动。

---

## 3. 六轴映射总表

代码实现 ★ 三条轴；其余三条给概念映射（框架侧的对应物都存在，但教学价值低于前三条，不写代码）。

| 轴 | demo | 手搓版 | LangGraph 对应 | demo7 |
|---|---|---|---|---|
| 循环 | demo1 | `while` + `stop_reason` 判停 + `messages.append` | StateGraph + 条件边回环 + `MessagesState` 的 add_messages reducer | ★ agent.py `build_graph()` |
| 约束 | demo6 | Permission 规则表 + ask 审批冒泡（REPL `input()`） | `interrupt()` + `Command(resume=)`——暂停点/落盘/恢复值全由框架管理 | ★ agent.py `gate()` |
| 记忆 | demo2 | `memory/<会话ID>.jsonl` 手搓落盘 + `/resume` 扫目录 | Checkpointer 每步自动快照 + `thread_id` 隔离 | ★ agent.py SqliteSaver |
| 工具 | demo3 | MCP client-server（JSON-RPC round-trip） | 框架外——ToolNode 只吃 LangChain Tool；MCP 要接 `langchain-mcp-adapters` 转接 | 概念映射 |
| 规划 | demo4 | plan 一次性工具 + Skill 渐进披露 | Plan-and-Execute 是官方示例**模式**（planner / executor 两节点），无预置件；Skill 机制框架无对应 | 概念映射 |
| 多 Agent | demo5 | subagent 后台执行 + jobs 注册表 | `Send` API（动态派发）/ 子图 / `langgraph-supervisor` 库 | 概念映射 |

> demo2 的另外两件——compact 压缩、prompt caching——**框架不接管**（见 §5 诚实边界）。

---

## 4. 循环轴对照：while 循环 → StateGraph

demo1 的主循环骨架（~95 行，节选）：

```python
MAX_ITERATIONS = 30

for loop_idx in range(1, MAX_ITERATIONS + 1):
    # 1. 决策
    response = client.messages.create(model=MODEL, system=SYSTEM_PROMPT,
                                      tools=TOOLS, messages=messages)
    # 2. 判停
    if response.stop_reason != "tool_use":
        return "".join(b.text for b in response.content if b.type == "text")
    # 3. 行动：执行工具
    messages.append({"role": "assistant", "content": response.content})
    for block in response.content:
        if block.type == "tool_use":
            result = str(AVAILABLE_FUNCTIONS[block.name](**block.input))
    # 4. 感知：结果追加回 messages
    messages.append({"role": "user", "content": tool_results})
```

demo7 的同一件事（`build_graph()`，核心 ~30 行）：

```python
builder = StateGraph(AgentState)
builder.add_node("llm", call_model)                  # 决策
builder.add_node("gate", gate)                       # （demo6 的审批门，见 §6）
builder.add_node("tools", ToolNode(tools_lc))        # 行动+感知
builder.add_edge(START, "llm")
builder.add_conditional_edges("llm", route_after_llm, ["gate", END])   # 判停
builder.add_conditional_edges("gate", route_after_gate, ["tools", "llm"])
builder.add_edge("tools", "llm")
graph = builder.compile(checkpointer=checkpointer)
```

逐项对照：

| while 循环里的手艺 | 图上的对应 | 谁写的 |
|---|---|---|
| `messages.append(...)` 两处 | `MessagesState` 自带 add_messages reducer，节点返回增量即合并 | **框架** |
| 执行工具 + 生成 tool_result + ID 配对 | `ToolNode` 一个现成节点 | **框架** |
| `stop_reason != "tool_use"` 判停 | `route_after_llm` 条件边（等价判据：AIMessage 有无 `tool_calls`） | 自己（一行函数） |
| `MAX_ITERATIONS = 30` | `recursion_limit`（注意口径差：数的是**超级步**，一轮 ReAct ≈ 3 步，demo7 配 60） | **框架**（配置一下） |
| 每轮全量 messages 重发 | 不变——llm 节点照样把全量 state["messages"] 发给模型，Agent 贵的根源框架也改不掉 | — |

两点框架没有替你做的：

1. **工具要包一层壳**：demo1 的工具函数零改动，但要过 `StructuredTool.from_function()` 包成 LangChain Tool 才能进 `ToolNode`——框架侧唯一要付的「壳税」。
2. **LLM 客户端换**：裸 `anthropic.Anthropic` 换成 `ChatAnthropic`（langchain-anthropic）。实测走智谱 Anthropic 兼容网关 + glm-5.2 直接可用（`base_url` / `api_key` 同款参数），双 auth 环境变量的坑也照旧。

> 框架还送一个预置件：`create_react_agent(llm, tools)` 一行等价于上面整张不带审批门的图。教学上先手写节点看清结构，再知道预置件包了什么——顺序不能反。

---

## 5. 记忆轴对照：jsonl 手搓落盘 → Checkpointer

demo2 用 `memory/<会话ID>.jsonl` 手搓会话持久化：一行一条只追加，`/resume` 扫目录恢复。demo7 换成 SqliteSaver：

```python
with SqliteSaver.from_conn_string("memory/checkpoints.sqlite") as saver:
    graph = builder.compile(checkpointer=saver)
    # 会话 = thread_id；同一 thread 下次 stream 自动带全部历史
    config = {"configurable": {"thread_id": thread_id}}
```

对照：

| | demo2 手搓 | demo7 框架 |
|---|---|---|
| 落盘粒度 | 会话结束 / 每轮 messages（jsonl 追加） | **每个超级步**一份完整 state 快照（含暂停现场） |
| 恢复 | `/resume` 读文件重放 | 换 `thread_id` 即恢复（含**暂停中**的图，见 §6） |
| 短期记忆 | messages 数组跨任务增长 | 同——state["messages"] 一直长大 |
| 压缩 / caching | `compact_messages` + `cache_control`（手搓实现） | **没有**，仍要自己做 |

最后一行是**诚实边界**：LangGraph 管「状态的存取」，不管「上下文窗口装不装得下、token 贵不贵」。demo2 的 compact 滚动摘要与 prompt caching 在框架版里照样得自己写（往 llm 节点里加预处理）——手搓轴的经验不作废，反而说明这两件事本质是 **LLM 调用层**的优化，不是 Runtime 状态管理的事。

两个实测踩到的真坑（框架粒度更细的代价，都已处理，详见 §7）：

1. `saver.list()` 枚举的是**检查点**不是会话——同一 thread 出现 N 次，`/resume` 列表要去重（手搓 jsonl 一会话一文件天然没这问题）。
2. 恢复历史会话后问「刚才那个问题你答的什么」，glm-5.2 自发输出了 thinking 块，`AIMessage.content` 从字符串变成**内容块列表**——渲染层必须按块类型挑 `text`，只认字符串就整轮哑火。

---

## 6. 约束轴对照：ask 冒泡 → interrupt()

demo6 的审批是 REPL 里 `input()` 硬等——人不在场，程序就停在那。LangGraph 的 `interrupt()` 是图的原生能力：

```python
def gate(state: AgentState) -> dict:
    dangerous = needs_approval(state["messages"][-1].tool_calls)
    if not dangerous:
        return {"gate_decision": "allow"}          # 安全工具直通
    decision = interrupt({                         # ← 整张图在这暂停
        "type": "approval",
        "calls": [{"name": c["name"], "args": c["args"]} for c in dangerous],
    })
    return apply_gate_decision(state["messages"][-1], decision)
```

暂停后 REPL 收到 `__interrupt__` 事件，弹 `y/n`，用 `Command(resume="allow"/"deny")` 传回答案——`interrupt()` 调用处**原地**收到返回值，图继续走。

与 demo6 手搓版的三点差异：

| | demo6 手搓 | interrupt() |
|---|---|---|
| 暂停时状态在哪 | REPL 内存里挂着 | **已落盘**（checkpointer 存了暂停现场）——进程死了也能恢复接着问 |
| 恢复值怎么传 | 函数返回值 / 队列 | `Command(resume=值)`，interrupt 调用处原地收 |
| 适用范围 | 主循环 + 审批冒泡写死了通路 | 图里任意节点任意位置，审批 / 确认 / 补充信息都同一机制 |

deny 分支有个协议细节：拒绝也要给这条 AIMessage 上的**每个** tool_call 配一条拒绝 `ToolMessage`（tool_use/tool_result 严格配对是 Anthropic API 的硬要求），模型看到拒绝会自己换路子（§7 实测：两次 shell 被拒后改用 write_file 完成任务）。

教学最小版只拦 `execute_bash`；扩展成 demo6 的 `(工具, fnmatch 模式, action)` 规则表只需替换 `needs_approval()` 的判定，**图结构一行不改**——这就是「约束是插进图里的一个节点」的含义。

---

## 7. 真实运行回显

以下均为实测日志（glm-5.2，Windows 10 + Git Bash），非虚构；复跑轨迹可能不同（见 demo1 §3 的说明）。

### 7-1 循环轴：读 → 写 → 验证（3 轮，无审批触发）

任务：「读取 tools.py 的开头注释第一行，把内容写进 first_line.txt」

```
⏺ tool_call   | read_file({'path': 'tools.py'})
✔ tool_return | #!/usr/bin/env python # -*- coding: utf-8 -*- """ Demo7 工具层 — 与 demo1-react/tools.py 逐字相同...
⏺ tool_call   | write_file({'content': '#!/usr/bin/env python\n', 'path': 'first_line.txt'})
✔ tool_return | [成功] 文件已写入: first_line.txt (22 字符)
⏺ tool_call   | read_file({'path': 'first_line.txt'})       ← 自己验证（system prompt 里「实现完必须运行验证」的引导）
✔ tool_return | #!/usr/bin/env python
● assistant   | 已完成。tools.py 开头注释第一行是 `#!/usr/bin/env python`（shebang 行），已写入 first_line.txt 并验证成...
```

图上的走线：`llm → gate(直通) → tools → llm → gate → tools → llm → gate → tools → llm → END`——条件边两次回环、第三次判停，与 demo1 的 3 轮 ReAct 完全同构。

### 7-2 约束轴 deny：两次被拒，模型自己换路子（管道运行，审批自动拒绝）

任务：「用 shell 命令 echo hello-deny-demo 的输出写进 deny_test.txt」

```
⏺ tool_call   | execute_bash({'command': 'echo hello-deny-demo > deny_test.txt && cat deny_test.txt'})
⚠ approval    | execute_bash({'command': 'echo hello-deny-demo > deny_test.txt && cat deny_test.txt'})
[非交互模式] 审批默认拒绝（deny）
✔ tool_return | [用户拒绝] 该工具调用未获审批，请换一种不涉险的方式完成任务
⏺ tool_call   | execute_bash({'command': 'echo hello-deny-demo | tee deny_test.txt'})   ← 换个姿势再试
⚠ approval    | execute_bash({'command': 'echo hello-deny-demo | tee deny_test.txt'})
✔ tool_return | [用户拒绝] 该工具调用未获审批，请换一种不涉险的方式完成任务
⏺ tool_call   | write_file({'content': 'hello-deny-demo\n', 'path': 'deny_test.txt'})   ← 弃 shell 改走文件工具
✔ tool_return | [成功] 文件已写入: deny_test.txt (16 字符)
⏺ tool_call   | read_file({'path': 'deny_test.txt'})
✔ tool_return | hello-deny-demo
● assistant   | 完成。由于 shell 命令被审批拒绝，我改用文件写入工具直接创建了 ...
```

两次 `gate(deny) → llm` 回环后模型放弃 shell——拒绝信息作为 ToolMessage 回给模型、模型基于它改策略，这条链路和 demo6 手搓版语义一致。

### 7-3 约束轴 approve：interrupt 暂停 → 放行 → 真执行

真终端交互的弹层等价物（此处用驱动脚本自动批准，渲染同款）：

```
⏺ tool_call   | execute_bash({'command': 'echo approved-demo7 > approved.txt'})
⚠ approval    | execute_bash(...)            ← 图在此暂停（已落盘）
（人答 y → Command(resume="allow")）
✔ tool_return | [命令执行成功，无输出]        ← 放行后真执行
⏺ tool_call   | read_file({'path': 'approved.txt'})
✔ tool_return | approved-demo7
● assistant   | 执行并验证完成...
```

### 7-4 记忆轴：跨进程 /resume

第一个进程跑完任务退出；第二个进程启动后 `/resume`：

```
  [1] 1968e33717b0  1+1等于几？一句话
  [2] render-test   当前目录有哪些 txt 文件？列出即可
  [3] approve-test  执行 shell 命令 echo approved-demo...
  ...
接续哪个会话？输入序号（回车取消）: 1
[已接续] 1968e33717b0（messages 4 条）
❯ 刚才那个问题你答的什么？
● assistant   | 我刚才回答的是：**1+1等于2**。
```

新进程、零手搓 jsonl——thread_id 一换，messages 历史全回来了。

---

## 8. 总结：什么时候手搓、什么时候上框架

| | 手搓（demo1-6 路线） | 框架（LangGraph 路线） |
|---|---|---|
| 看懂原理 | ✅ 每一行都是自己的 | ❌ 图调度 / 状态合并藏在框架里 |
| 代码量 | 循环骨架 ~95 行 | 图声明 ~98 行（**打平**，§1 数据） |
| 白送的能力 | 无——持久化 / 暂停恢复都得自己写 | Checkpointer 每步落盘、interrupt 暂停恢复、recursion_limit、预置件 |
| 上下文优化 | compact / caching 已有手搓实现 | **不接管**，照样自己写 |
| 依赖 | anthropic SDK 一个 | langgraph 全家桶（本 demo 4 个包） |
| 排障 | 读自己的代码 | 事件流 / state 快照 / 框架版本行为（§5 两个坑都来自框架粒度） |

判断口径：**要学原理、或 Agent 逻辑就是核心资产 → 手搓；要快速堆出带持久化和人审的生产级工作流 → 框架**。两者不互斥——本仓库的路线就是先手搓六轴，再来对照篇；读懂了 while 循环，LangGraph 的图就只是「循环的推广」。

```
demo7: base × 框架（对照篇——六轴的 LangGraph 视角收束）
```

> 轴覆盖的权威矩阵见仓库根目录 [README](../README.md)「轴清单 × 内容矩阵」。
