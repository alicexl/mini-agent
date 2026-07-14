# Demo2 — 记忆轴

> 在 demo1（base = LLM × 工具 × 循环）基础上叠加「记忆轴」：让 Agent **记得过去**（长期记忆）、**不爆上下文**（动态压缩）、**跑得起长 prompt**（cache_control 缓存）。
>
> 公式：`demo2 = base × 记忆`

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（含口播 / 表格 / 代码 / 运行时序）
  1. demo2 vs demo1 的增量：从「单轮 ReAct」到「带记忆的循环」
  2. 长期记忆系统：信息搬运的本质（agent_memory.md + 滑动窗口）
  3. 上下文管理一：动态压缩（compact_messages）
  4. 上下文管理二：Prompt caching（cache_control breakpoint）
  5. 示例解读：长任务实测（compact 触发 + caching 命中）
  6. 局限与工业级演进
  7. 下一节预告：工具扩展轴（demo3）

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具 / 工具实现 / 长期记忆 / 上下文管理 / 主循环） |
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
python agent.py
```

进入交互模式后输入任意任务。

| 命令 | 作用 |
|---|---|
| `/memory` 或 `/m` | 查看当前记忆文件内容 |
| `quit` / `exit` / `q` | 退出 |

### 可调参数（Part 4 / Part 5）

| 参数 | 默认 | 含义 |
|---|---|---|
| `MEMORY_WINDOW_LINES` | 50 | 长期记忆加载到 system prompt 的滑动窗口（行数） |
| `COMPACT_THRESHOLD_MESSAGES` | 20 | messages 条数达此阈值触发 compact_messages |
| `COMPACT_KEEP_RECENT` | 6 | compact 时保留最近 N 条原始消息 |
| `USE_CACHE_CONTROL` | True | 是否启用 prompt caching；某些兼容网关不支持时可关掉 |

> 运行时会在当前目录生成 `agent_memory.md`（已加入 `.gitignore`），每个用户的记忆不同，不应提交。
