# Demo1 — Agent 底层原理

> 用最少的代码展现 Agent 的底层运行机制。
> 一个能干活、但只有「短期记忆」的最简 Agent。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（6 章，含口播 / 表格 / 代码 / 运行时序）
  1. 结论：先说结论
  2. 全局架构
  3. 逐层解读
  4. 示例解读循环的运行时序
  5. 深入理解关键设计
  6. 总结和展望

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（4 个 Part：客户端 / 工具定义 / 工具实现+路由表 / ReAct 主循环） |
| `讲稿.md` | 教学讲稿 |
| `requirements.txt` | 依赖清单（仅 `anthropic` SDK） |

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

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

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖（优先级：环境变量 > 代码变量）。

### 启动 Agent

```bash
python agent.py
```

进入交互模式后，输入任意任务（如「统计当前目录下有多少个 Python 文件，并把结果写入 count.txt」、「读 README.md 并总结要点」等），观察每一轮 ReAct 循环的决策、行动、感知。输入 `quit` / `exit` 退出。

> **注**：`verbose=True` 默认开启，打印每一轮的完整决策与工具调用，便于教学观察。
