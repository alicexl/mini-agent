# mini-agent — 从 0 到 1 拆解 Agent 的底层原理

> 用 7 个递进的最简 demo 把 Claude Code / Cursor / Devin 这类"自动干活"工具的底层机制完全拆开。

📖 **[完整总览 → demo1-7-总览.md](demo1-7-总览.md)**

## 目录

| Demo | 主题 | 关键能力 |
|---|---|---|
| [demo1](demo1) | LLM × 工具 × 循环 | ReAct、本地工具 |
| [demo2](demo2) | 记忆 × 规划 | `agent_memory.md`、手动 Plan |
| [demo3](demo3) | Rules × MCP | 行为约束、JSON-RPC 远程工具、Plan 自动决策 |
| [demo4](demo4) | Subagent 分工 | 主 Agent 派生一次性独立 Subagent |
| [demo5](demo5) | Team 协作 + 事件驱动 | 持久 Agent + 状态机 + 质检员持续监听 |
| [demo6](demo6) | 上下文压缩 | `compact_messages` 动态压缩对话历史 |
| [demo7](demo7) | 安全边界 | 三道防线：黑名单 / 用户确认 / 输出截断 |

## 快速开始

```bash
# 1. 配置 API Key（环境变量，不要写进代码）
export ANTHROPIC_API_KEY=xxx

# 2. 进入任一 demo 目录运行
cd demo1
pip install -r requirements.txt
python agent.py
```

每个 demo 目录下有 `讲稿.md`（教学讲稿）和 `README.md`（技术参考）。

## 学习路径

推荐按编号顺序学习——每个 demo 都在前一个的基础上做"一减一加"，演进逻辑清晰。

详细的学习路径、能力矩阵、三种拆任务机制对比见 **[总览页](demo1-7-总览.md)**。
