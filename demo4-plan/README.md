# Demo4 — 规划轴

> 在 demo2（base × 记忆）基础上叠加规划轴——**plan（当前任务拆步骤）、Skill（历史经验复用）、ask_user_question（歧义时主动问用户）**。继承全部记忆能力（无自动压缩，同 demo3）。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（6 章）
  1. 结论：demo4 比 demo2 多了什么
  2. 机制一：plan（当前任务的规划）
  3. 机制二：Skill（历史经验的复用）
  4. 机制三：ask_user_question（歧义时主动问）
  5. 真实演示案例
  6. 总结

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 主入口：客户端 + ReAct 主循环（工具合并 + plan 调用后移除）+ REPL |
| `tools.py` | 工具层（demo1 四件套，本地） |
| `plan.py` | **规划层**：plan 工具（LLM 自判复杂度，调后从 tools 移除） |
| `skill.py` | **Skill 层**：Skill 加载器 + use_skill（渐进式披露） |
| `ask.py` | **提问层**：ask_user_question 方向键 UI + 非 TTY 降级（对齐 Claude Code 的 AskUserQuestion） |
| `render.py` | 渲染层（demo2 版：分色 + cache 统计 + user 回放） |
| `memory.py` | 记忆层（demo2 版减自动压缩） |
| `session.py` | 会话层：`memory/<会话ID>.jsonl` 持久化 + `/resume` |
| `commands.py` | 命令层：`/help` `/status` `/tools` `/skills` `/memory` `/resume` `/new` `/compact` `/quit` |
| `skills/review.md` | 示例 Skill——代码审查工作流 |
| `memory/` | 运行时生成的双层记忆目录（已 gitignore，同 demo2） |

## 设计要点

### 与 demo2 的差异

- **新增 `plan.py`**（规划层）+ **`skill.py`**（Skill 层）+ **`ask.py`**（提问层）：规划轴的三个新机制
- **无自动压缩**（同 demo3）：压缩只由 `/compact` 手动发起

### plan：一次性规划

LLM 自判任务复杂度——3 步以上、步骤间有依赖的任务先调 `plan` 列步骤（Agent 打印清单），**调用一次后从 tools 移除**（列完就放手，不反复管理进度）；简单任务直接干（描述里写了反向抑制）。

### Skill：渐进式披露

`skills/*.md`（YAML frontmatter：name/description/triggers）启动时扫描加载；**system prompt 只放元信息**（name + description + 触发词），正文由 LLM 按需经 `use_skill` 拉取——skill 再多也只增加少量元信息开销。

### ask_user_question：歧义时主动问

需求有歧义 / 有多种合理实现 / 拿不准方向时，LLM 调 `ask_user_question` 把问题抛给用户（1-4 题、每题 2-4 选项、选项带说明），而不是自作主张。UI 自动追加「其他（自定义输入）」；**非 TTY 环境（管道喂任务）自动降级**为逐题打印 + 读一行。

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖：`anthropic` + `rich` + `prompt_toolkit`。

### 配置 API Key

环境变量 `ANTHROPIC_API_KEY`（推荐，可持久化）；未设时启动交互式输入。

### 启动 Agent

```bash
python -X utf8 agent.py
```

演示建议：
- **ask**：输入「帮我生成一个密码」——类型/长度没说清，Agent 会主动提问（方向键选择或输入自定义答案）
- **plan**：输入「我需要你做几件事：1) 读 plan.py 找出所有工具名 2) 整理成表格 3) 写入 inventory.md 4) 读回验证，注意步骤间有依赖」——先列步骤清单再干活
- **Skill**：输入「帮我 review 一下 plan.py」——匹配 review skill 触发词，先取工作流再按步骤审查

> **管道自动跑**：`printf '任务\n答案\n/quit\n' | python -X utf8 agent.py`——ask 的方向键 UI 自动降级为编号选择，答案按行喂入。

### 可调参数（`memory.py` 顶部，同 demo2/demo3）

| 参数 | 默认 | 含义 |
|---|---|---|
| `MEMORY_WINDOW_LINES` | 50 | MEMORY.md 加载进 system prompt 的行数上限（防止上下文无限增长） |
| `COMPACT_KEEP_RECENT` | 4 | `/compact` 时保留最近 N 条原始消息 |
| `USE_CACHE_CONTROL` | True | 是否启用 prompt caching |

> 另有 `USE_THINKING` 思考模式开关（`agent.py` 顶部，默认关闭）。
