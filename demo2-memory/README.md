# Demo2 — 记忆与规划

> 在 demo1（LLM × 工具 × 循环）基础上增加 **记忆** 和 **规划** 两个能力。
> 让 Agent 记得过去、规划未来，不再像金鱼。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（7 章，含口播 / 表格 / 代码 / 运行时序）
  1. demo2 vs demo1 的两个增量
  2. 记忆系统：信息搬运的本质
  3. 规划系统：先想再做
  4. 多步执行：Message 的演化
  5. 示例解读：找所有 todo 整理到 todo.md（实测）
  6. 局限与演进方向
  7. 下一节预告：MCP / Rules / plan 自动决策

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具 / 工具实现 / 记忆 / 规划 / 主循环） |
| `讲稿.md` | 教学讲稿 |
| `agent_memory.md` | 运行时生成的长期记忆文件（已 gitignore） |
| `todo.md` | 讲稿示例跑出来的真实产物（保留作参考） |

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
python agent.py            # 直接 ReAct 模式（demo1 风格）
python agent.py --plan     # Plan 模式（先规划再执行）
```

进入交互模式后输入任意任务。**REPL 内可随时切换：**

| 命令 | 作用 |
|---|---|
| `/plan` 或 `/p` | 切换 Plan 模式（开↔关） |
| `/no-plan` 或 `/np` | 关闭 Plan 模式 |
| `/memory` 或 `/m` | 查看当前记忆文件内容 |
| `quit` / `exit` / `q` | 退出 |

> **注**：`verbose=True` 默认开启，打印每个 step 的完整决策与工具调用。
> 运行时会在当前目录生成 `agent_memory.md`（已加入 `.gitignore`），每个用户的记忆不同，不应提交。
