# Demo2 — 记忆轴

> 在 demo1（base = LLM × 工具 × 循环 × 状态）基础上叠加「记忆轴」：让 Agent **记得过去**（长期记忆）、**不爆上下文**（动态压缩）、**跑得起长 prompt**（cache_control 缓存）。
>
> 公式：`demo2 = base × 记忆`

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（含口播 / 表格 / 代码 / 运行时序）
  1. 结论：demo2 比 demo1 多了什么
  2. 记忆的两层：会话持久化与跨会话记忆
  3. 会话内窗口管理一：动态压缩（compact_messages）
  4. 会话内窗口管理二：Prompt caching
  5. 示例解读：实测四机制
  6. 局限与工业级演进

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 主入口：客户端 + ReAct 主循环（compact 触发 + cache 统计）+ REPL |
| `tools.py` | 工具层（同 demo1：schema + 4 工具 + 路由表） |
| `render.py` | 渲染层（demo1 版 + `print_cache_stats` 缓存命中统计） |
| `memory.py` | **记忆层**：项目级记忆 + compact + caching（记忆轴机制与可调参数） |
| `session.py` | **会话层**：`memory/<会话ID>.jsonl` 会话持久化 + `/resume` 恢复 |
| `commands.py` | 命令层：`/help` `/status` `/memory` `/resume` `/new` `/compact` `/quit` |
| `讲稿.md` | 教学讲稿 |
| `memory/` | 运行时生成的双层记忆目录（已 gitignore）：**项目级** `MEMORY.md`（模型自主维护的持久事实，跨会话加载进 system prompt——对齐 Claude Code 的 MEMORY.md）+ **会话级** `<会话ID>.jsonl`（完整 messages，`/resume` 恢复） |

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`anthropic` + `rich` + `prompt_toolkit`。

### 配置 API Key

网关、模型、超时已在代码里写死，**只需配置 API Key**（按优先级）：

**方式 1：环境变量（推荐，可持久化）**

```bash
export ANTHROPIC_API_KEY=你的智谱Key    # Key 格式 id.secret，在 bigmodel.cn 生成
```

**方式 2：运行时交互式提示**

未设环境变量直接运行，会提示输入（不持久化，每次运行都要重输）。

### 启动 Agent

```bash
python -X utf8 agent.py
```

进入交互模式后输入任意任务（如「统计当前目录下有多少个 Python 文件，并把结果写入 count.txt」）。对话中出现值得跨会话记住的持久事实时，模型会自主用 `write_file` / `edit` 更新 `memory/MEMORY.md`（模型自主维护，对齐 Claude Code）；下次启动时其内容加载进 system prompt。

| 命令 | 作用 |
|---|---|
| `/help` | 显示可用命令（遍历注册表自动生成） |
| `/status` | 显示会话状态（模型 / 会话 ID / 消息数 / 记忆配置） |
| `/memory` | 查看项目级记忆（`memory/MEMORY.md`） |
| `/resume` | 列出历史会话（时间 + 首句摘要），输编号恢复并回放 |
| `/new` | 开启新会话（清空活历史 + 换新会话 ID；旧会话文件留在磁盘） |
| `/compact` | 手动压缩会话历史（跳过阈值，老消息摘要成一段并全量重写会话文件） |
| `/exit` `/quit` | 退出程序 |

> **管道自动跑**：`printf '任务\n/quit\n' | python -X utf8 agent.py`——非终端环境下 render 自动降级（`input()` + 去色），退出必须用 `/quit`（裸词退出已移除）。

### 可调参数（`memory.py` 顶部）

| 参数 | 默认 | 含义 |
|---|---|---|
| `MEMORY_WINDOW_LINES` | 50 | MEMORY.md 加载进 system prompt 的行数上限（自主维护应保持精炼，此为防膨胀保险丝） |
| `COMPACT_THRESHOLD_MESSAGES` | 12 | messages 条数达此阈值触发 compact_messages |
| `COMPACT_KEEP_RECENT` | 4 | compact 时保留最近 N 条原始消息 |
| `USE_CACHE_CONTROL` | True | 是否启用 prompt caching；某些兼容网关不支持时可关掉 |

> 另有 `USE_THINKING` 思考模式开关（`agent.py` 顶部，默认关闭，同 demo1）。

> 运行时会在当前目录生成 `memory/` 目录（已加入 `.gitignore`）：项目级 `MEMORY.md` + 会话级 `<会话ID>.jsonl`，每个用户的记忆不同，不应提交。
