# Demo6 — 安全约束轴

> 在 demo1（base = LLM × 工具 × 循环 × 状态）基础上叠加「约束轴」：在 LLM 决策和原始工具之间插入**两层 Runtime Control**——可配置、可观测、可拦截，而不是硬编码在某个工具函数里。
>
> 公式：`demo6 = base × 约束`

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（4 章 + 附录）
  1. 结论：demo6 vs demo1（execute_bash 无约束 + 两个 Runtime Control）
  2. Permission：声明式规则引擎（allow / deny / ask）
  3. Hook：PreToolUse / PostToolUse 事件回调
  4. 局限、演进和总结（firejail / Docker / microVM）

概念讲解、设计原理、演进方向全部在讲稿里。本 README 只讲**怎么跑起来**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具定义 / 工具实现 / Runtime Control（Permission+Hook+dispatch_tool）/ 主循环 / 入口） |
| `讲稿.md` | 教学讲稿 |

## 设计要点

### 两层 Runtime Control（dispatch_tool 串接）

每次工具调用按顺序过两层，任一层阻断都返回错误给大模型：

| 层 | 抽象 | 实现 | 示例 |
|---|---|---|---|
| **Permission**（策略层） | 准入决策 | `PERMISSION_RULES` 列表 + fnmatch | `rm -rf *` → deny |
| **Hook**（扩展层 / Lifecycle Extension） | 事件回调 | `HOOKS` 注册表 + Python callable | Pre 注入（写 `.sh` 脚本 → 补 `#!/bin/bash` shebang，改 input 不拦截）；Post 扫输出（命中密钥 → 替换成 `[xxxxx]` 并补「已脱敏」提示） |

> 真正的执行隔离（firejail / Docker / microVM）属于 Execution Environment，在调用链路之下，不在本 demo 范围——见讲稿第 4 章。

### 与朴素黑名单方案的差异

| 维度 | 朴素黑名单方案 | demo6 |
|---|---|---|
| 防线抽象 | 黑名单 regex + 用户确认 + 输出截断 | Permission + Hook 两层 Runtime Control |
| 配置风格 | 硬编码 regex | 声明式规则（PERMISSION_RULES / HOOKS） |
| 可扩展性 | 加规则要改代码 | 加规则只改配置；hook 可插拔 |
| 对标产品 | 启发式脚本 | Claude Code permissions / hooks |

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

启动后会自动建好 `test_dir/` 测试目录（5 个文件：a.txt / b.txt / c.log / d.tmp / db.conf）。

| 命令 | 作用 |
|---|---|
| `quit` / `exit` / `q` | 退出 |
| 其他 | 当作新任务输入 |

### 演示场景

启动信息里会列出建议演示：

| 演示 | 演示什么 | 操作 |
|---|---|---|
| **1 Permission deny** | 命中 deny 规则直接拦 | 输入「删除 test_dir 目录」 → `rm -rf test_dir/` 命中 deny 被拦，LLM 直接结束并建议你手动删 |
| **2 Permission allow** | 命中白名单免确认 | 输入「看看 test_dir 目录下有什么文件」 → `ls test_dir` 命中 `ls *` → `allow` |
| **3 Hook 注入** | Pre hook 改写 input | 输入「写个 hi.sh 脚本，内容就一行 echo hello」 → 文件里实际多了 `#!/bin/bash` shebang，LLM 还以为只有 `echo hello` |
| **4 Hook 扫输出** | Post hook 替换 output | `read_file test_dir/db.conf` → 输出里密钥被替换成 `[xxxxx]`、末尾补「已脱敏 N 处」提示，LLM 看到的就是脱敏版 |

### 可调参数（Part 4）

| 参数 | 默认 | 含义 |
|---|---|---|
| `PERMISSION_RULES` | 见 Part 4.1 | 声明式规则表，first-match wins |
| `DEFAULT_POLICY` | `"ask"` | 无规则命中时的默认 action |
| `HOOKS` | 见 Part 4.2 | PreToolUse / PostToolUse 注册表 |

> demo6 不引入新工具——`TOOLS` 与 demo1 base 字节一致（execute_bash / read_file / write_file / edit）。所有变化都在 `dispatch_tool`（Part 4）的工具调度层。
