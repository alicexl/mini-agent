# mini-agent — 从 0 到 1 拆解 Agent 的底层原理

> 用 6 个递进的最简 demo 把 Claude Code / Cursor / Devin 这类"自动干活"工具的底层机制完全拆开。

📖 **[完整总览 → demo1-6-总览.md](demo1-6-总览.md)**

## 目录

| Demo | 轴 | 一句话公式 | 解决的核心问题 |
|---|---|---|---|
| [demo1-react](demo1-react) | 循环 | `LLM × 工具 × 循环 × 状态` | Agent 最小心跳——ReAct 循环跑通（base） |
| [demo2-memory](demo2-memory) | 记忆 | `base × 记忆` | 任务结束就忘；多轮 ReAct 把 messages 撑爆上下文 |
| [demo3-tools](demo3-tools) | 工具 | `base × 工具` | 工具只有 read/write/bash 三件套；想接外部协议 |
| [demo4-plan](demo4-plan) | 规划 | `base × 规划` | 走一步看一步容易跑偏；常见任务每次重新想 |
| [demo5-multiagent](demo5-multiagent) | 多 Agent | `base × 多 Agent` | 单 Agent 上下文膨胀；不能外包/协作独立子任务 |
| [demo6-safety](demo6-safety) | 约束 | `base × 约束` | 工具太自由（`rm -rf /`），安全逻辑硬编码不可配置 |

> 每条轴正交：demo1 是 base，demo2-6 各自独立加一条轴，不链式叠加。读者可以按任意顺序学 demo2-6。

## 快速开始

```bash
# 1. 配置 API Key（任选一种）
#    a) 改 demo1-react/agent.py 顶部的 API_KEY = ""
#    b) export ANTHROPIC_API_KEY=xxx
#    c) 启动时交互式输入

# 2. 进入任一 demo 目录运行
cd demo1-react
pip install -r requirements.txt
python agent.py
```

默认走**智谱 BigModel 的 Anthropic 兼容网关**（`https://open.bigmodel.cn/api/anthropic`）+ `glm-5.2` 模型——接口与 Anthropic SDK 完全兼容，换官方 API 或别的兼容网关只需改 `BASE_URL` / `MODEL`。

每个 demo 目录下有：
- **`讲稿.md`** — 完整教学讲稿（口播 + 屏显 + 真实运行日志，配合视频讲解）
- **`agent.py`** — 单文件实现（200-600 行，可直接 `python agent.py` 跑通）
- **`README.md`** — 精简的设计方案 + 运行说明

## 学习路径

推荐按编号顺序学习——demo1 是 base，学完 demo1 后 demo2-6 可以按兴趣调整顺序。

详细的学习路径、能力矩阵、三种拆任务机制对比见 **[总览页](demo1-6-总览.md)**。
