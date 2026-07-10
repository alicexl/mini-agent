# Demo5 — Team 协作与事件驱动状态机

> 在 demo4 基础上引入持久 Agent、任务状态机和事件驱动质检员，
> 让多个 Agent 像真实项目组一样协作。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（5 章）
  1. 从临时工到正式员工
  2. Agent 类：身份、记忆、inbox、chat
  3. Team 类：招募、通信、解散、协作
  4. 真实案例：有依赖的多步任务
  5. 三方对比 + 总结 + 未来

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（7 个 Part：客户端 / 工具 / Rules / 记忆 / Agent 类 / Team + 状态机 / 入口） |
| `讲稿.md` | 教学讲稿 |
| `.agent/rules.md` | Team 级 Rules，注入到项目经理 prompt |
| `agent_memory.md` | 运行时生成的长期记忆（已 gitignore） |
| `retry_demo.py` | 辅助脚本（演示质检重试流程） |

## 设计方案

### 核心变化：从 demo4 Subagent 到 demo5 Team

| 维度 | demo4 Subagent | demo5 Team |
|---|---|---|
| Agent 生命周期 | 一次性（函数调用完即销毁） | **持久化**（实例存活整个 team，messages 跨多次 chat 累积） |
| 通信 | 无 | **inbox 机制**（send 一对一 / broadcast 群发） |
| 质检 | 无 | **事件驱动质检员**（任务一完成立即验收，最多 3 次） |
| 任务编排 | LLM 自行调 subagent 工具 | **状态机调度**（pending / reviewing / redoing / passed / failed） |
| 依赖处理 | 无法传递 | 依赖全 passed 才启动；依赖 failed 自动级联 failed |

### 任务状态机

```
pending -->[依赖全 passed]--> reviewing -->[质检通过]--> passed (终态)
                              |
                              +--[质检不过, attempts<3]--> redoing --> (回 reviewing)
                              |
                              +--[质检不过, attempts=3]--> failed (终态)

pending -->[依赖 failed]--> failed (级联, 终态)
```

- 单任务最多质检 3 次（`MAX_REVIEW_ATTEMPTS = 3`）
- 质检员是普通 Agent，可用 `read_file` / `execute_bash` 实际复查
- 事件循环用**单线程**模拟"质检员持续监听"，避免多线程写 `Agent.messages` 的安全问题

### 本地工具集

demo5 沿用 demo4 的 6 个本地工具，**去掉 subagent**——协调工作交给 Team 类外部编排。

| 工具名 | 用途 |
|---|---|
| `execute_bash` | 执行 shell 命令 |
| `read_file` | 读文件 |
| `write_file` | 写文件 |
| `add` | 加法 |
| `multiply` | 乘法 |
| `weather` | 天气查询 |

### 三种模式决策参考

| 场景 | 选择 |
|---|---|
| 子任务严格按步骤、有依赖 | Plan（共享 messages） |
| 子任务相互独立 | Subagent（独立 messages） |
| 有依赖 + 需要通信 + 需要质检 | **Team**（事件驱动 + 状态机） |

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖仅 `anthropic` SDK。

### 配置 API Key

编辑 `agent.py` 顶部的 `API_KEY` 变量，或设置环境变量：

```bash
export ANTHROPIC_API_KEY=...
```

### 启动

```bash
cd demo5
python agent.py
```

REPL 内输入任务即可（例如「帮我完成下面一组任务：1) 算 35+47；2) 把第 1 步的结果乘以 8；3) 查北京天气；4) 把所有结果写入 result.txt」），`quit` / `exit` 退出。质检员由项目经理 prompt 强制分配，用户无需指定。
