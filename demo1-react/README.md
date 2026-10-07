# Demo1 — Agent 底层原理

> 用最少的代码展现 Agent 的底层运行机制。
> 一个能干活、但只有「短期记忆」的最简 Agent。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（5 章，含口播 / 表格 / 代码 / 运行时序）
  1. 先说结论
  2. 全局架构与逐层解读
  3. 示例解读循环的运行时序
  4. 工程化拆分：tools、render 与 commands
  5. 总结和展望

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent_single.py` | **原始单文件版**（3 个 Part 一个文件，教学起点——「一切始于单文件」） |
| `agent.py` | 正式版主入口：客户端初始化 + ReAct 主循环 |
| `tools.py` | 工具层：工具 schema + 4 个工具实现 + 路由表（从原 Part 2「工具」拆出） |
| `render.py` | 渲染层：rich 分色输出 + prompt_toolkit 输入（从原散落 print 拆出） |
| `commands.py` | 命令层：`/help` `/status` `/exit` 斜杠命令 |
| `讲稿.md` | 教学讲稿 |
| `requirements.txt` | 依赖清单（`anthropic` + `rich` + `prompt_toolkit`） |

两个版本功能完全一致，`agent_single.py` 可独立运行；拆分动机见讲稿第 4 章。

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

### 配置 API Key

网关、模型、超时已在代码里写死，**只需配置 API Key**（按优先级）：

**方式 1：环境变量（推荐，可持久化）**

```bash
export ANTHROPIC_API_KEY=你的智谱Key    # Key 格式 id.secret，在 bigmodel.cn 生成
```

**方式 2：运行时交互式提示**

未设环境变量直接运行，会提示输入（不持久化，每次运行都要重输）。

> 另有 `USE_THINKING` 开关（`agent.py` / `agent_single.py` 顶部，默认关闭）——开启后模型每轮先推理再决策，思考过程以 `✻ thinking`（暗灰色）随回复展示；思考预算计入 `max_tokens`，开启时输出上限自动抬到 8000。

### 启动 Agent

```bash
python -X utf8 agent.py           # 正式版（三文件结构，rich 渲染）
python -X utf8 agent_single.py    # 原始单文件版（纯 print，零渲染依赖）
```

进入交互模式后，输入任意任务（如「统计当前目录下有多少个 Python 文件，并把结果写入 count.txt」、「读 README.md 并总结要点」等），观察每一轮 ReAct 循环的决策、行动、感知。斜杠命令：`/help` 可用命令 / `/status` 会话状态 / `/quit` 退出（命令纯斜杠；`agent_single.py` 原始版仍支持裸 `quit` / `q`）。

> **注 1**：`verbose=True` 默认开启，打印每一轮的完整决策与工具调用，便于教学观察。
>
> **注 2**：`-X utf8` 防止 GBK Windows 控制台中文乱码；`render.py` 在管道（非终端）环境下自动降级——prompt_toolkit 输入退回内置 `input()`，rich 输出去掉颜色，`printf '任务\n/quit\n' | python -X utf8 agent.py` 可自动跑。
