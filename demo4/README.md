# Demo4 — Subagent 的分工合作

> 教学讲稿见 `讲稿.md`，本文件是技术参考文档。

## 一、Demo4 在系列中的位置

| Demo | 主题 | 关键能力 |
|---|---|---|
| demo1 | LLM × 工具 × 循环 | ReAct、本地工具 |
| demo2 | 记忆 × 规划 | agent_memory.md、独立 plan 命令 |
| demo3 | Rules × MCP | 行为约束、JSON-RPC 远程工具、plan 自动决策 |
| **demo4** | **Subagent 分工** | **主 Agent 可派生一次性独立 Subagent** |

demo4 在 demo3 基础上做**一减一加**：

- **减法**：去掉 MCP（三件小工具搬回本地）、去掉 plan 工具（与 subagent 语义重叠）
- **加法**：新增 `subagent` 本地工具，可派生独立 Agent 循环

## 二、核心概念：Subagent

Subagent 不是新协议、新框架——**它就是一个工具**。调一下它，主 Agent 内部启动一个新的 ReAct 循环。

与主 Agent 的差异只有三件：

| 维度 | 主 Agent | Subagent |
|---|---|---|
| **messages** | 整个 task 共享一份 | 每次派生新建一份，结束时销毁 |
| **system_prompt** | 基础说明 + Rules + 记忆 | 只拼角色化指令，不注入 Rules / 记忆 |
| **工具集** | 含 `subagent` 工具 | 去掉 `subagent` 工具（防递归） |
| **生命周期** | 持续整个 task | 一次性：派生 → 干活 → 返回 → 消亡 |

底层用**同一份 `_react_loop`** 代码——Subagent 没有任何特殊循环逻辑。

## 三、文件结构

```
demo4/
├── agent.py            ← 全部实现（单文件，约 480 行）
├── .agent/
│   └── rules.md        ← Rules 规范文件（沿用 demo3 思路）
├── 讲稿.md             ← 教学讲稿
├── README.md           ← 本文件
└── requirements.txt    ← 仅 anthropic
```

`agent.py` 按 6 个 Part 组织：

| Part | 内容 |
|---|---|
| 1 | LLM 客户端初始化（与 demo1/2/3 一致） |
| 2 | 本地工具定义 + 实现（7 个：bash/read/write/add/multiply/weather/subagent） |
| 3 | Rules 加载器（沿用 demo3） |
| 4 | 记忆系统（沿用 demo2） |
| 5 | 工具路由表（`LOCAL_FUNCTIONS` 字典） |
| 6 | Agent 主循环 + Subagent 循环（共享 `_react_loop`） |

## 四、本地工具集

demo3 的 7 工具里：

- 三个文件 / shell 工具 → **沿用**
- 三个 MCP 工具（add / multiply / weather）→ **搬回本地**（去掉 MCP 这层）
- plan 工具 → **删掉**（与 subagent 语义重叠）
- 新增 subagent 工具

合计 7 个本地工具，没有任何远程 RPC。

| 工具名 | 来源 | 用途 |
|---|---|---|
| `execute_bash` | 沿用 | 执行 shell 命令 |
| `read_file`    | 沿用 | 读文件 |
| `write_file`   | 沿用 | 写文件 |
| `add`          | 从 MCP Server 搬回本地 | 加法 |
| `multiply`     | 从 MCP Server 搬回本地 | 乘法 |
| `weather`      | 从 MCP Server 搬回本地 | 天气查询 |
| `subagent`     | demo4 新增 | 委派独立 Subagent |

## 五、关键代码片段

### 5.1 subagent 工具的 schema

```python
{
    "name": "subagent",
    "description": (
        "委派一个独立的 Subagent 来完成子任务。适合相互独立、专业分工的场景——"
        "比如同时让一个角色做加法、另一个角色做乘法、第三个角色写文件。"
        "Subagent 拥有独立的角色（system_prompt）和独立的上下文（messages），"
        "执行完返回结果摘要后即消亡，不会保留记忆。"
        "注意：相互依赖的任务（后一步要用前一步结果）不要用 subagent，"
        "应让主 Agent 自己顺序完成。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role":  {"type": "string", "description": "Subagent 的角色，例如「加法计算专家」/「Python 工程师」"},
            "task":  {"type": "string", "description": "交给 Subagent 完成的具体任务描述"},
        },
        "required": ["role", "task"],
    },
}
```

description 里的两段提示是关键：

- ✅ "**相互独立、专业分工**" → LLM 看到多任务会主动委派
- ❌ "**相互依赖不要用 subagent**" → LLM 看到依赖会自己顺序做

### 5.2 Subagent 的 system_prompt

```python
def build_subagent_system_prompt(role: str) -> str:
    return (
        f"你是一个被委派来的 Subagent。你的角色是：**{role}**。\n"
        f"请专注于交给你完成的任务，做完后用一两句话汇报结果。"
    )
```

**与主 Agent system_prompt 的差异**：

- 不注入 Rules（demo4 简化省略；真实场景可注入全局规范）
- 不注入记忆（避免无关历史任务干扰）

### 5.3 启动独立 ReAct 循环

```python
def _run_subagent(role, task, tools, local_fns, depth, verbose):
    indent = "    " * depth   # 缩进打印让嵌套轨迹可视化

    sub_messages = [{"role": "user", "content": task}]      # 独立 messages
    sub_system_prompt = build_subagent_system_prompt(role)  # 独立 prompt

    final = _react_loop(                                    # 与主 Agent 同一个 ReAct
        messages=sub_messages,
        tools=tools,                                         # 已去掉 subagent 工具
        local_fns=local_fns,
        system_prompt=sub_system_prompt,
        tools_for_subagent=tools,
        depth=depth,
        verbose=verbose,
        indent=indent,
    )
    # sub_messages / sub_system_prompt 在函数返回时即被丢弃
    return f"[Subagent · {role}] 任务：{task}\n结果：{final}"
```

### 5.4 防递归：subagent 工具集去掉自身

```python
def run_agent(user_input, all_tools, ...):
    # 主 Agent 能调 subagent；给 subagent 准备的工具集已经去掉 subagent
    tools_for_subagent = [t for t in all_tools if t.get("name") != "subagent"]
    return _react_loop(
        ...,
        tools_for_subagent=tools_for_subagent,
    )
```

`_react_loop` 看到 LLM 调 `subagent` 时，把 `tools_for_subagent` 传给 `_run_subagent`，子循环的 LLM 看不到 subagent 工具——单层分包，堵死递归。

## 六、设计决策

### 6.1 为什么 Subagent 要有独立的 messages

| 维度 | demo3 plan step（共享 messages） | demo4 Subagent（独立 messages） |
|---|---|---|
| 上下文 | step 间共享，前一步结果后一步能看到 | 完全隔离，看不到主 Agent 历史 |
| 膨胀   | 越多 step，messages 越长 | 每个 Subagent 只看自己的任务，messages 短 |
| 专注   | 后续 step 会被前面所有对话影响 | Subagent 聚焦被委派的那一件事 |
| 结束   | 整个 plan 跑完 messages 一直在 | Subagent 结束 messages 立刻销毁 |

**取舍**：

- 共享 messages（plan）适合**有依赖关系的多步骤**——Step 2 要用 Step 1 的结果
- 独立 messages（Subagent）适合**相互独立的子任务**——四个加法各算各的

### 6.2 为什么 Subagent 要有自己的 system_prompt

`role` 字段让 Subagent "演"一个角色——加法专家 / Python 工程师 / 测试工程师。这个角色会**改变 LLM 的行为**：

- 写代码时用 Python 工程师的风格
- 做测试时写出更严谨的用例

工业级 Agent 框架（Claude Code 的 subagent、Cursor 的 @backend / @frontend）本质都是这套：**给 Subagent 一个身份，让它在自己的专业领域内行动**。

### 6.3 为什么 Subagent 工具集要去掉 subagent

如果 Subagent 自己也能调 subagent，大模型一旦"想偷懒"就会无限派生——token 烧光、上下文爆炸、调用栈溢出。

demo4 的做法直接粗暴：`[t for t in all_tools if t.get("name") != "subagent"]`——**主 Agent 能调 subagent，Subagent 不能再调 subagent**。

## 七、真实运行示例

### 7.1 四个独立任务（典型 subagent 场景）

```
用户: 任务如下：1) 计算 35+47；2) 计算 65*9；3) 查上海天气；4) 把 "hello" 写入 hello.txt

主 Agent:
  ├ subagent(role="加法计算专家", task="计算 35 + 47")
  │    └─ 独立 ReAct: add(35, 47) → "35 + 47 = 82" → end_turn
  ├ subagent(role="乘法计算专家", task="计算 65 × 9")
  │    └─ 独立 ReAct: multiply(65, 9) → "65 × 9 = 585" → end_turn
  ├ subagent(role="天气查询助手", task="查询上海的天气")
  │    └─ 独立 ReAct: weather("上海") → "多云 22°C" → end_turn
  ├ subagent(role="文件写入助手", task="把 'hello' 写入 hello.txt")
  │    └─ 独立 ReAct: write_file("hello.txt", "hello") → end_turn
  └ 总结 end_turn

助手: 4 个子任务全部完成：
      - 加法：35 + 47 = 82
      - 乘法：65 × 9 = 585
      - 天气：上海 多云 22°C
      - 文件：hello.txt 已写入（5 字符）
```

**主 Agent 的 messages 只追加 4 个 subagent 的结果摘要**——每个 subagent 内部的 tool_use 细节全部丢弃，主 Agent 上下文保持极短。

### 7.2 简单查询（典型非 subagent 场景）

```
用户: 用 weather 工具查一下上海天气

主 Agent:
  └ weather({"city": "上海"}) → "多云 22°C" → end_turn

助手: 上海天气：多云，22°C
```

LLM 读了 subagent 工具 description 里的"**相互独立、专业分工**"——一个简单查询不符合，直接调 weather，不委派。

## 八、Plan vs Subagent 决策树

```
任务复杂吗？
├─ 否 → 直接 ReAct（不拆任务）
└─ 是 → 子任务之间有依赖吗？
        ├─ 有依赖 → 用 Plan（共享 messages，后续 step 看到前面结果）
        └─ 相互独立 → 用 Subagent（独立 messages，主 Agent 只看摘要）
```

| 场景 | 选 plan 还是 subagent |
|---|---|
| 「算 35+47，结果乘 8，写文件，验证」 | **plan**（Step 2 依赖 Step 1 的结果） |
| 「四件独立的事：加法 / 乘法 / 天气 / 写文件」 | **subagent**（互不依赖，独立 messages 更短） |
| 「查一下上海天气」 | **直接 ReAct**（不需要拆任务） |

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
cd demo4
python agent.py
```

REPL 内输入任务即可。退出用 `quit` / `exit`。

## 十、demo 进度回顾

| 能力 | demo1 | demo2 | demo3 | demo4 |
|---|---|---|---|---|
| LLM × 工具 × 循环 | ✅ | ✅ | ✅ | ✅ |
| 跨任务长期记忆 | ❌ | ✅ | ✅ | ✅ |
| 规划 + 多步串联 | ❌ | ✅ | ✅（自动决策） | — |
| 行为约束（Rules） | ❌ | ❌ | ✅ | ✅ |
| 外部工具协议（MCP） | ❌ | ❌ | ✅ | ❌（demo4 简化） |
| **多 Agent 分工（Subagent）** | ❌ | ❌ | ❌ | ✅ |

## 十一、下一节预告

demo5 会讲**多个 Subagent 之间的协作与编排**：

- Subagent 之间的**通信通道**（消息队列 / 共享黑板 / 事件总线）
- Subagent 的**持久记忆与身份管理**（跨任务复用）
- 真正的"团队协作"

这才是 subagent 机制的完整形态。
