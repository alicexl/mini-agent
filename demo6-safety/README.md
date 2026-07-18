# Demo6 — 安全约束轴

> 在 demo1（base = LLM × 工具 × 循环 × 状态）基础上叠加「约束轴」：给 Agent 的"手脚"（execute_bash / read_file / write_file）加**三层声明式安全栈**——可配置、可观测、可拦截，而不是硬编码在某个工具函数里。
>
> 公式：`demo6 = base × 约束`

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（7 章）
  1. 结论：demo6 vs demo1（"裸手"风险）
  2. 三层栈总览（dispatch_tool 流程）
  3. Permission：声明式规则引擎（allow / deny / ask）
  4. Sandbox：执行隔离 profile（read-only / write-full / none）
  5. Hook：PreToolUse / PostToolUse 事件回调
  6. 局限与工业级演进（firejail / Docker / microVM / JSON IPC Hook）
  7. 后续轴预告（系列回顾）

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具定义 / 三层安全栈 / 工具实现 + dispatch_tool / 主循环 / 入口） |
| `讲稿.md` | 教学讲稿 |

## 设计要点

### 三层安全栈（dispatch_tool 串接）

每次工具调用按顺序过三层，任一层阻断都返回错误给大模型：

| 层 | 抽象 | 实现 | 拦截示例 |
|---|---|---|---|
| **Permission** | 策略层 | `PERMISSION_RULES` 列表 + fnmatch | `rm -rf *` → deny |
| **Sandbox** | 执行层 | `SANDBOX_PROFILE` 命令前缀白名单 | read-only profile 拦 `rm` |
| **Hook** | 观察层 | `HOOKS` 注册表 + Python callable | 写含 PASSWORD 的文件被 Pre 拦 |

### 与 demo7（旧版）的差异

| 维度 | demo7（旧） | demo6（新） |
|---|---|---|
| 防线抽象 | 黑名单 regex + 用户确认 + 输出截断 | Permission / Sandbox / Hook 三层正交栈 |
| 配置风格 | 硬编码 regex | 声明式规则（PERMISSION_RULES / SANDBOX_PROFILE / HOOKS） |
| 可扩展性 | 加规则要改代码 | 加规则只改配置；hook 可插拔 |
| 对标产品 | 启发式脚本 | Claude Code permissions / sandbox / hooks |

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

**方式 2：运行时交互式提示**

`API_KEY` 为空时直接运行 `python agent.py`，会提示输入（不持久化，每次运行都要重输）。

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖（优先级：环境变量 > 代码变量）。

### 启动 Agent

```bash
python agent.py
```

启动后会自动建好 `test_dir/` 测试目录（4 个文件：a.txt / b.txt / c.log / d.tmp）。

| 命令 | 作用 |
|---|---|
| `quit` / `exit` / `q` | 退出 |
| 其他 | 当作新任务输入 |

### 演示场景

启动信息里会列出建议演示：

| 演示 | 演示什么 | 操作 |
|---|---|---|
| **1 Permission deny** | 命中 deny 规则直接拦 | 输入「删掉 test_dir 目录」 → 看 LLM 第一次跑 `rm -rf test_dir/` 被拦，改用单文件删除 |
| **2 Permission ask** | catch-all 走用户确认 | 输入「跑一下 whoami」 → 走 `*` 规则 → `ask` |
| **3 Sandbox 拦截** | profile 白名单拦 | 改 `SANDBOX_PROFILE = "read-only"`，输入「删掉 test_dir/a.txt」 → Permission 通过但 Sandbox 拦 |
| **4 Hook 拦截敏感词** | Pre hook 拦 | 输入「把数据库连接字符串写到 db.conf（含 PASSWORD）」 |
| **5 Hook 日志** | Post hook 记录 | 跑任意任务后查看 `.demo6_hook_log` |

### 可调参数（Part 3）

| 参数 | 默认 | 含义 |
|---|---|---|
| `PERMISSION_RULES` | 见 Part 3.1 | 声明式规则表，first-match wins |
| `DEFAULT_POLICY` | `"ask"` | 无规则命中时的默认 action |
| `SANDBOX_PROFILE` | `"none"` | Bash 执行隔离 profile |
| `HOOKS` | 见 Part 3.3 | PreToolUse / PostToolUse 注册表 |

> demo6 不引入新工具——`TOOLS` 与 demo1 base 字节一致（execute_bash / read_file / write_file）。所有变化都在 `dispatch_tool`（Part 4）的工具调度层。
