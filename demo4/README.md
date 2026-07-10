# Demo4 — Subagent 的分工合作

> 主 Agent 可派生一次性独立 Subagent，各自拥有独立上下文和角色，适合相互独立的子任务。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（4 章，含口播 / 代码 / 运行时序）
  1. 整体把握：demo4 做了什么
  2. Subagent：本质、schema、实现、设计决策
  3. 真实案例：四个独立任务派给 Subagent
  4. Plan vs Subagent + 总结 + demo5 预告

概念讲解、设计原理、案例详解全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具 / Rules / 记忆 / 工具路由 / 主循环+Subagent） |
| `讲稿.md` | 教学讲稿 |
| `.agent/rules.md` | Rules 规范文件（沿用 demo3 思路） |
| `agent_memory.md` | 运行时生成的长期记忆文件（已 gitignore） |

## 设计方案

### demo4 在系列中的位置

在 demo3 基础上做**一减一加**：

- **减法**：去掉 MCP（三件小工具搬回本地）、去掉 plan 工具（与 subagent 语义重叠）
- **加法**：新增 `subagent` 本地工具，可派生独立 Agent 循环

### 本地工具集

合计 7 个本地工具，没有任何远程 RPC：

| 工具名 | 来源 | 用途 |
|---|---|---|
| `execute_bash` | 沿用 | 执行 shell 命令 |
| `read_file`    | 沿用 | 读文件 |
| `write_file`   | 沿用 | 写文件 |
| `add`          | 从 MCP Server 搬回本地 | 加法 |
| `multiply`     | 从 MCP Server 搬回本地 | 乘法 |
| `weather`      | 从 MCP Server 搬回本地 | 天气查询 |
| `subagent`     | demo4 新增 | 委派独立 Subagent |

### Subagent 设计要点

- **一次性**：派生 → 干活 → 返回结果摘要 → 消亡，messages 不保留
- **独立上下文**：新建 messages，不注入 Rules / 记忆，只拼角色化 system_prompt
- **防递归**：Subagent 的工具集去掉 `subagent` 自身——单层分包，堵死递归

### Plan vs Subagent 决策树

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

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单见 `requirements.txt`（仅 `anthropic` SDK）。

### 配置 API Key

网关、模型、超时已在代码里写死，**只需配置 API Key**：

```python
# agent.py Part 1
API_KEY         = ""                                         # ← 只改这一行
BASE_URL        = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel
MODEL           = "glm-5.2"
API_TIMEOUT_MS  = 3000000                                    # 50 分钟
```

**方法 1：改代码（最简单）**

打开 `agent.py`，把 Part 1 顶部的 `API_KEY = ""` 改成你的 Key。

**方法 2：首次运行交互式提示**

`API_KEY` 为空时直接运行 `python agent.py`，会提示输入（仅本次运行有效，不持久化）。

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖。

### 启动 Agent

```bash
python agent.py
```

进入交互模式后输入任意任务。**REPL 命令：**

| 命令 | 作用 |
|---|---|
| `/memory` 或 `/m` | 查看当前记忆文件内容 |
| `quit` / `exit` / `q` | 退出 |

> **注**：`verbose=True` 默认开启，打印每个 step 的完整决策与工具调用。
> 运行时会在当前目录生成 `agent_memory.md`（已加入 `.gitignore`），每个用户的记忆不同，不应提交。
