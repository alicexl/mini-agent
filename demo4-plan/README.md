# Demo4 — 规划轴

> 在 demo1-react（base）上独立叠加「规划轴」：新增 `plan` 工具（自动决策版） + Skill 可复用的工作流模板。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（5 章）
  1. demo4 干了什么
  2. 机制一：plan（自动决策版）
  3. 机制二：Skill（可复用的工作流模板）
  4. 真实演示案例（简单任务跳过 plan / 复杂任务 plan 列步骤 / Skill 触发）
  5. 总结

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | Agent 主程序（Part 1-5：客户端 / 工具定义（含 plan + use_skill）/ 工具实现 / Skill 加载器 / 主循环） |
| `skills/review.md` | 示例 Skill——代码审查工作流（YAML frontmatter + 工作流 body） |
| `讲稿.md` | 教学讲稿 |

## 设计要点

### plan（自动决策版）

- demo1 的工具保留不变
- demo4 新增 `plan` + `use_skill` 两个工具
- **plan**：LLM 列步骤（字符串数组），Agent 打印清单。一次性可视化，不追踪进度。plan 调用一次后从 tools 移除
- **何时用由 LLM 自动判断**：
  - 简单任务（1-2 步、单一工具）→ 跳过 plan，直接 ReAct
  - 复杂任务（3+ 步、多工具协作、有依赖）→ 先 plan 列步骤

### Skill（可复用的工作流模板）

- 每个 skill 是 `skills/*.md` 文件，YAML frontmatter 三字段：`name` / `description` / `triggers`（关键词数组）
- frontmatter 之后是 body——工作流正文（多步指令模板）
- **加载时机**：启动时 `load_skills()` 扫目录 + 解析 frontmatter → 内存维护 `{name: skill}` 字典
- **激活时机**：system prompt 含所有 skill 的元信息（name + description + triggers）；LLM 看到用户任务匹配某 skill 时，自主调用 `use_skill` 工具获取 body；body 以 tool_result 形式进入 messages
- **system prompt 两层叠加**：
  1. 基础说明（角色）
  2. 可用 Skills 元信息（name + description + triggers，不含 body）
- **为什么 body 不进 system prompt**：skill 一多就撑爆上下文。元信息每个 skill 一两行（100 个 skill 也才几百 token），body 只在 LLM 需要时通过 `use_skill` 拉取
- 简易 YAML 解析用正则实现（`_parse_frontmatter`），不引入 PyYAML 依赖——教学代码保持零额外依赖
- 对应 Claude Code 的 Skill 工具（`commit` / `review-pr` 等命令的本质）

## 运行

### 安装依赖

```bash
pip install -r requirements.txt
```

依赖清单（`anthropic`）。

### 配置 API Key

**推荐：环境变量**（避免 Key 进 git 历史）

```bash
# Git Bash
export ANTHROPIC_API_KEY="你的智谱 BigModel Key"
python agent.py
```

或者改 `agent.py` Part 1 顶部的 `API_KEY = ""`（不推荐——会被 git track）。

默认走智谱 BigModel 的 Anthropic 兼容网关（`https://open.bigmodel.cn/api/anthropic`）+ `glm-5.2` 模型，换官方 API 或其他兼容网关只需改 `BASE_URL` / `MODEL`。

### 启动

```bash
python agent.py
```

启动后会打印已加载的工具列表 + skill 列表。进入交互模式后输入任意任务：

- **简单任务示例**：`统计当前目录下 .py 文件数` —— LLM 跳过 plan，直接 execute_bash
- **复杂任务示例**：`我需要做几件事：1) 先读 agent.py...注意第2步依赖第1步的结果` —— LLM 自判为复杂任务，调 plan 列步骤
- **Skill 触发示例**：`帮我 review 一下 agent.py` —— LLM 看到 review skill 元信息，自主调用 `use_skill` 获取工作流，按流程输出结构化意见
- `/skills` 查看已加载的 skills
- `quit` / `exit` / `q` 退出

### 自定义 Skill

在 `skills/` 目录下新建 `*.md` 文件，按以下格式：

```markdown
---
name: my-skill
description: 一句话说明做什么
triggers: ["触发词1", "触发词2"]
---

# 工作流正文

收到匹配任务时按以下步骤执行：

1. 第一步...
2. 第二步...
3. 输出格式...
```

重启 Agent 即生效。

### 运行时产物

- plan 状态只在终端打印，不落盘。每次新任务自动清空
