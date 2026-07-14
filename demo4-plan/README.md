# Demo4 — 规划轴

> 在 demo1-react（base）上独立叠加「规划轴」：新增 `todo_write` 工具（自动决策版） + 接入 Skill 预消化工作流。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（6 章）
  1. 结论：demo4 vs demo1
  2. 为什么需要规划（ReAct 走一步看一步的痛点）
  3. 机制一：TodoWrite（自动决策版）
  4. 机制二：Skill（预消化的工作流）
  5. 真实案例：简单任务跳过 todo / 复杂任务 5 步推进 / review skill 命中
  6. 总结与下一节预告（demo5 多 Agent）

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | Agent 主程序（Part 1-5：客户端 / 工具定义（含 todo_write）/ 工具实现 / Skill 加载器 / 主循环） |
| `skills/review.md` | 示例 Skill——代码审查工作流（YAML frontmatter + 工作流 body） |
| `讲稿.md` | 教学讲稿 |

## 设计要点

### TodoWrite（自动决策版）

- demo1 的 3 件套（execute_bash / read_file / write_file）保留不变
- demo4 新增 `todo_write`——把多步任务的 step 列表显式化（对应 Claude Code 的 TodoWrite）
- 三状态：`pending` / `in_progress` / `completed`，每次调用传全量 todos 覆盖式更新
- **关键不是落盘**：todos.md 是给人看的副产物；真正的作用是让 LLM 把"规划阶段"在 ReAct 轨迹里显式化
- **何时用由 LLM 自动判断**：
  - 简单任务（1-2 步、单一工具）→ 跳过 todo_write，直接 ReAct
  - 复杂任务（3+ 步、多工具协作、有依赖）→ 先 todo_write 列步骤，再逐步执行并更新状态
- 不做手动 `/plan` 模式——把"LLM 怎么判断复杂度"放在聚光灯下

### Skill（预消化的工作流）

- 每个 skill 是 `skills/*.md` 文件，YAML frontmatter 三字段：`name` / `description` / `triggers`（关键词数组）
- frontmatter 之后是 body——预消化的工作流正文（多步指令模板）
- **加载时机**：启动时 `load_skills()` 扫目录 + 解析 frontmatter → 内存维护 `{name: skill}` 字典
- **激活时机**：每次用户输入时 `match_skill()` 关键词扫描 → 命中则把对应 body 注入 system prompt
- **system prompt 三层叠加**：
  1. 基础说明（角色 + 工具清单 + todo_write 使用指引）
  2. 可用 Skills 元信息（name + description，每次都带）
  3. 当前激活 skill 的 body（命中时注入；否则空段）
- **为什么不全部 body 常驻 prompt**：skill 一多就撑爆上下文。第二层只放元信息（每个 skill 一两行），第三层命中时才注入对应 body——100 个 skill 也只增加几百 token 元信息开销
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

- **简单任务示例**：`统计当前目录下 .py 文件数` —— LLM 跳过 todo_write，直接 execute_bash
- **复杂任务示例**：`第一步读 agent.py；第二步提取函数名；第三步...严格按 N 步执行` —— LLM 先 todo_write 列步骤再逐步推进
- **Skill 触发示例**：`帮我 review 一下 agent.py` —— review skill 自动激活，按工作流输出结构化意见
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

- `todos.md` —— todo_write 工具的落盘文件（每次调用覆盖式更新），已加入 `.gitignore`
