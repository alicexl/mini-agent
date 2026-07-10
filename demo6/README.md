# Demo6 — 上下文压缩

> 在 demo1–5 基础上解决「多轮 ReAct 让 messages 越攒越多，撞顶即崩」的问题。
> 核心能力：`compact_messages` 动态压缩对话历史，让多 step 任务不撞顶。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（7 章，含图解 / 代码 / 实战演示全过程）
  1. 问题：messages 为什么会爆
  2. 四种解决思路
  3. 压缩原理（图解）
  4. 代码实现：`compact_messages`
  5. 实战演示：一个 4-step 任务的压缩全过程
  6. demo 实现 vs 生产级实现（兼与 demo2 对比）
  7. demo1–7 系列回顾

问题背景、压缩原理图解、实战演示全过程、安全边界回退讲解均在讲稿里。本 README 只讲**设计要点**和**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具 / Rules 加载 / **compact_messages** / Plan+压缩主循环 / 交互入口） |
| `讲稿.md` | 教学讲稿 |
| `.agent/rules.md` | 行为规范（注入 system prompt） |
| `hello.txt` | 讲稿示例跑出来的真实产物（保留作参考） |

## 设计方案

### 四个解决思路对比

| 方案 | 思路 | 优缺点 |
|---|---|---|
| ① 扩窗口 | 换更大 context 的模型 | 零成本；治标不治本，超长任务仍会撞顶 |
| ② 限循环 | 限制 step 次数，爆了就重启 | 实现简单；粗暴，失败重试代价高 |
| ③ 阶段化 | 只保留最近 N 条（滑动窗口） | 实现简单；丢弃关键信息，大模型"失忆" |
| **④ 压缩** | 旧消息让大模型做成摘要，保留最近几条原文 | 保留要点；多一次 LLM 调用，可能丢细节 |

**demo6 选方案 ④**——质量与成本的最佳平衡点，也是生产级 Agent（如 Claude Code）的核心做法。

### compact_messages 压缩策略

- **触发阈值**：`COMPACT_THRESHOLD = 8`（messages 达到 8 条触发压缩）
- **保留首尾**：首条 user 任务指令始终保留；最近 `KEEP_RECENT = 4` 条原文不动
- **中间摘要**：旧消息区交给大模型生成一段摘要文本，替换为 `[对话历史摘要]` + 一条过渡 `assistant` 消息
- **安全边界**：切点不能落在 `tool_use` / `tool_result` 对子中间（Anthropic API 要求紧邻），`_find_safe_boundary` 从理想切点向前回溯跳过危险位置
- **触发时机**：Plan 每个 step 开头调一次 `compact_messages`，5–6 step 的任务会触发 2–3 次压缩

### demo 实现 vs 生产级实现

| 维度 | demo6（教学版） | 生产级（如 Claude Code） |
|---|---|---|
| **触发指标** | 固定条数（`COMPACT_THRESHOLD=8`） | 基于 token 数，按实际上下文窗口占比触发（如 80%） |
| **压缩粒度** | 一次性把旧消息压成一个 summary | 分层：最近原文、稍远摘要、更远压得更细 |
| **保留策略** | 最近 N 条 + 安全边界 | 智能选择：关键词、关键决策、文件路径、代码片段等 |
| **prompt 定制** | 通用压缩 prompt | 按场景定制（coding 保留路径/决策原因；research 保留事实引用等） |
| **失败处理** | 摘要失败直接抛异常 | 重试 + 降级（保留原文） |

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

**方式 1：改代码（最简单）**

打开 `agent.py`，把 Part 1 顶部的 `API_KEY = ""` 改成你的 Key。

**方式 2：首次运行交互式提示**

`API_KEY` 为空时直接运行 `python agent.py`，会提示输入（仅本次运行有效，不持久化）。

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖。

### 启动 Agent

```bash
python agent.py
```

进入交互模式后输入任意任务。建议给一个多步骤任务以观察压缩过程，例如：

```
在 demo6 目录下创建一个 hello.txt 写入"hello demo6"，再读取它，最后告诉我文件大小
```

观察日志中 `[compact]` 开头的行——这就是压缩被触发的时刻。

| 命令 | 作用 |
|---|---|
| 任意文本 | 当作新任务输入 |
| `quit` / `exit` / `q` | 退出 |

> **注**：`verbose=True` 默认开启，打印每个 step 的完整决策与工具调用，以及压缩触发时的摘要过程。
