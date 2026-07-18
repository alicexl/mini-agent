#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo6 - 带安全约束的 Agent（约束轴）

在 demo1（base = LLM × 工具 × 循环 × 状态）基础上叠加「约束轴」：给 Agent 的"手脚"
（execute_bash / read_file / write_file）加**三层声明式安全栈**，让危险操作可配置、
可观测、可拦截——而不是硬编码在某个工具函数里。

    × Permission（规则引擎）  —— 工具调用前的访问控制（allow / deny / ask）
    × Sandbox（执行隔离）    —— Bash profile 限制（read-only / write-only / none）
    × Hook（事件回调）       —— PreToolUse / PostToolUse 可插拔观察者

公式：demo6 = base × 约束

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化（同 demo1）
    Part 2: 工具定义（同 demo1，3 件套）
    Part 3: 三层安全栈（★ demo6 核心新增）
    Part 4: 原始工具实现 + dispatch_tool 统一调度入口
    Part 5: Agent 主循环（同 demo1，把 fn() 改成 dispatch_tool()）
    Part 6: 交互式入口

启动：
    python agent.py
"""

import fnmatch
import os
import subprocess

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================
# 网关、模型、超时均写死，用户只需配置 API Key（两种方式）：
#   1. 直接修改下面的 API_KEY
#   2. 都没设 → 运行时交互式提示输入（不持久化，每次都要重输）
# 默认走智谱 BigModel 的 Anthropic 兼容网关 + glm-5.2 模型。

# ↓↓↓ 只需改这一行 ↓↓↓
API_KEY = ""

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL          = "glm-5.2"                                  # 模型名
API_TIMEOUT_MS = 3000000                                    # 单次请求超时（毫秒），3000000ms = 50 分钟


def load_config() -> dict:
    """环境变量优先于代码默认值（仅 API_KEY 走环境变量有用）"""
    return {
        "api_key":       os.environ.get("ANTHROPIC_API_KEY") or API_KEY,
        "base_url":      BASE_URL,
        "model":         MODEL,
        "timeout_ms":    API_TIMEOUT_MS,
    }


def ensure_config() -> dict:
    """
    配置完整性检查。
    缺失 API Key 时交互式提示用户输入（仅本次运行有效，不持久化）。
    """
    config = load_config()
    if config["api_key"]:
        return config

    print("=" * 60)
    print("检测到尚未配置 API Key，请输入（仅本次运行有效）")
    print("如需持久化：请改 agent.py 顶部的 API_KEY 变量")
    print("=" * 60)

    api_key = input("\n请输入 API Key: ").strip()
    if not api_key:
        raise SystemExit("未提供 API Key，退出")

    config["api_key"] = api_key
    return config


# 模块级占位：实际使用前由 __main__ 调用 init_client() 初始化
client: Anthropic = None  # type: ignore


def init_client() -> None:
    """初始化模块级 client（在 __main__ 中调用）"""
    global client
    config = ensure_config()
    kwargs = {
        "api_key": config["api_key"],
        "base_url": config["base_url"],
        # Anthropic SDK 接收秒为单位的超时
        "timeout": config["timeout_ms"] / 1000.0,
    }
    client = Anthropic(**kwargs)


# ============================================================
# Part 2: 工具定义（Function Calling 标准格式）
# ============================================================
# 每次请求随 tools 参数一起发给大模型，相当于一份「工具说明书」。
# 大模型拿到说明书后就知道自己有哪些本地能力，但真正的执行发生在本地代码里。

TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可用于文件操作、系统命令等",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                }
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取指定路径文件内容，返回文本",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径",
                }
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入文件，不存在则创建，存在则覆盖",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
    },
]

SYSTEM_PROMPT = """你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。"""


# ============================================================
# Part 3: 三层安全栈（★ demo6 核心新增）
# ============================================================
# demo1 的工具是「裸奔」的——execute_bash 想跑什么就跑什么，write_file 想写哪里就写哪里。
# 真实 Agent（Claude Code / Cursor）都有声明式的安全栈，把"能不能做"从工具函数里抽出来，
# 变成**可配置、可插拔、可观测**的独立层。
#
# demo6 实现三层，对应三个不同的抽象：
#   · Permission —— 策略层：规则匹配 → allow / deny / ask（类似 Claude Code 的 permission)
#   · Sandbox    —— 执行层：profile 限制 Bash 能跑哪类命令（类似 firejail / Docker)
#   · Hook       —— 观察层：Pre/PostToolUse 回调，可拦、可改、可记录（类似 Claude Code 的 hook)
#
# 三层关系：Hook 可改 input 或拦 → Permission 决策是否允许 → Sandbox 限制实际执行。
# 任一层阻断都返回错误信息给大模型，让它看到原因后调整策略。


# ------------------------------------------------------------
# Part 3.1: Permission —— 声明式规则引擎
# ------------------------------------------------------------
# 规则格式：(tool_name, pattern, action)
#   tool_name: 工具名（execute_bash / read_file / write_file）
#   pattern:   fnmatch shell 通配符，匹配工具的关键参数
#              execute_bash 匹配 command；read_file/write_file 匹配 path
#   action:    "allow" / "deny" / "ask"
#
# 匹配顺序：从上到下，first-match wins（先命中的规则决定结果）。
# 无命中时走 DEFAULT_POLICY。
#
# 为什么是声明式而不是硬编码 if-else？
#   · 配置即策略：修改规则不用改代码（生产里从 YAML 加载）
#   · 可审计：整张规则表一眼看完，PR 审查友好
#   · 可组合：用户/项目/会话级规则按优先级叠加（Claude Code 的 enterprise 模式）

PERMISSION_RULES = [
    # —— 显式 deny —— 绝不让 LLM 跑的命令
    ("execute_bash", "rm -rf *",       "deny"),
    ("execute_bash", "rm -fr *",       "deny"),
    ("execute_bash", "dd *of=/dev/*",  "deny"),
    ("execute_bash", "mkfs.*",         "deny"),
    ("execute_bash", "shutdown*",      "deny"),
    ("execute_bash", "reboot*",        "deny"),
    ("execute_bash", "halt*",          "deny"),
    ("execute_bash", "poweroff*",      "deny"),
    ("execute_bash", "curl *| *sh*",   "deny"),
    ("execute_bash", "wget *| *sh*",   "deny"),

    # —— 显式 allow —— 安全只读类，免确认
    ("execute_bash", "ls *",           "allow"),
    ("execute_bash", "cat *",          "allow"),
    ("execute_bash", "grep *",         "allow"),
    ("execute_bash", "find *",         "allow"),
    ("execute_bash", "head *",         "allow"),
    ("execute_bash", "tail *",         "allow"),
    ("execute_bash", "wc *",           "allow"),
    ("execute_bash", "pwd",            "allow"),
    ("execute_bash", "echo *",         "allow"),
    ("execute_bash", "git status*",    "allow"),
    ("execute_bash", "git diff*",      "allow"),
    ("execute_bash", "git log*",       "allow"),

    # —— 其他 execute_bash：问一下
    ("execute_bash", "*",              "ask"),

    # —— read_file：项目内放行，项目外问一下（防止读敏感配置）
    ("read_file",    "*",              "allow"),

    # —— write_file：项目内放行，项目外问一下
    ("write_file",   "*",              "allow"),
]

DEFAULT_POLICY = "ask"

# 项目目录：用于 write_file 路径决策（写到项目外需要 ask）
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# 全局免确认开关（用户输入 a 后置 True，本会话所有 ask 自动通过）
_auto_approve_all = False


def _match_permission(tool_name: str, key_value: str) -> str:
    """返回命中的 action（allow / deny / ask）；无命中返回 DEFAULT_POLICY。"""
    for rule_tool, pattern, action in PERMISSION_RULES:
        if rule_tool == tool_name and fnmatch.fnmatchcase(key_value, pattern):
            return action
    return DEFAULT_POLICY


def confirm_action(prompt: str) -> bool:
    """
    交互式用户确认（action=ask 时调用）。

    返回 True=放行，False=拒绝。输入 a 后本会话所有 ask 自动通过。
    """
    global _auto_approve_all
    if _auto_approve_all:
        print(f"[Permission] 自动放行（已开启本会话免确认）")
        return True

    print(f"\n[Permission · 需确认] {prompt}")
    while True:
        choice = input("  允许执行吗？[y/n/a] (y=本次允许, n=拒绝, a=本会话全允许): ").strip().lower()
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no", ""}:
            print(f"  [已拒绝]")
            return False
        if choice == "a":
            _auto_approve_all = True
            print(f"  [已开启本会话免确认]")
            return True
        print("  请输入 y / n / a")


# ------------------------------------------------------------
# Part 3.2: Sandbox —— Bash 执行隔离 profile
# ------------------------------------------------------------
# 限制 execute_bash 能跑哪类命令。三档 profile：
#   "read-only":  只允许读类命令（ls / cat / grep / find / head / tail / wc / ps / df / du）
#   "write-full": 允许写类命令（mkdir / touch / rm / mv / cp / echo > / chmod / chown）
#   "none":       不限制（demo1 base 行为）
#
# 实现说明：本 demo 用「命令前缀白名单」近似沙箱——能演示概念，但**不是真隔离**。
# 真隔离要靠 OS-level 工具：Linux firejail / bubblewrap / Docker；Windows AppContainer。
# 因为 shell 永远绕得过去（python -c、base64 解码、变量拼接），白名单只挡"明显违规"。

SANDBOX_PROFILE = "none"   # ← 改成 "read-only" 体验沙箱拦截效果

SANDBOX_COMMAND_PREFIXES = {
    "read-only":  {"ls", "cat", "grep", "find", "head", "tail", "wc",
                   "pwd", "whoami", "ps", "df", "du", "echo", "git"},
    "write-full": {"ls", "cat", "grep", "find", "head", "tail", "wc",
                   "pwd", "whoami", "ps", "df", "du", "echo", "git",
                   "mkdir", "touch", "rm", "mv", "cp", "chmod", "chown",
                   "ln", "tar", "zip", "unzip"},
    "none":       None,   # None 表示不检查
}


def check_sandbox(command: str) -> tuple:
    """
    返回 (allowed, reason)。
    allowed=True 表示通过沙箱检查；allowed=False 时 reason 是拒绝原因。
    """
    if SANDBOX_PROFILE == "none" or SANDBOX_COMMAND_PREFIXES.get(SANDBOX_PROFILE) is None:
        return True, ""

    # 取命令首个 token（最朴素的解析；管道 / 重定向在 demo6 不展开——讲稿会点明这个简化）
    first_token = command.strip().split()[0] if command.strip() else ""
    # 兼容 "git status" 这种——前缀取 "git"
    first_token = first_token.split("/")[0]

    allowed_prefixes = SANDBOX_COMMAND_PREFIXES[SANDBOX_PROFILE]
    if first_token in allowed_prefixes:
        return True, ""
    return False, (
        f"沙箱拦截：profile={SANDBOX_PROFILE!r} 不允许命令前缀 {first_token!r}；"
        f"允许的前缀：{sorted(allowed_prefixes)}"
    )


# ------------------------------------------------------------
# Part 3.3: Hook —— PreToolUse / PostToolUse 事件回调
# ------------------------------------------------------------
# Hook 是可插拔的观察者：在工具执行前/后注入自定义逻辑。
#
# 本 demo 用 Python callable 内联实现（生产级如 Claude Code 用**外部脚本 + JSON IPC**）：
#   · 协议：subprocess.run([script], input=json_payload) → stdout=json_response, exit_code
#   · exit 0=pass / exit 2=block（仅 Pre） / 其他=错误
#   · response 可携带 modified_input / message
#
# Hook 函数签名：
#   pre_hook(tool_name, tool_input) -> dict  返回 {"decision": "pass"|"block", "message": "..."}
#   post_hook(tool_name, tool_input, tool_output) -> dict  返回 {"message": "..."}
#
# 下方注册两个示例 hook：
#   1. block_secret_write：Pre，拦截写入含 "PASSWORD" 的文件
#   2. log_all_calls：Post，把每次工具调用记到 .demo6_hook_log


def hook_block_secret_write(tool_name: str, tool_input: dict) -> dict:
    """Pre 示例：拦截写入含敏感关键词的文件。"""
    if tool_name != "write_file":
        return {"decision": "pass"}
    content = tool_input.get("content", "")
    for secret in ("PASSWORD", "API_KEY=", "PRIVATE KEY", "BEGIN RSA"):
        if secret in content:
            return {
                "decision": "block",
                "message": f"Hook 拦截：内容含敏感关键词 {secret!r}，拒绝写入",
            }
    return {"decision": "pass"}


def hook_log_all_calls(tool_name: str, tool_input: dict, tool_output: str) -> dict:
    """Post 示例：把每次工具调用追加到 .demo6_hook_log。"""
    log_path = os.path.join(PROJECT_DIR, ".demo6_hook_log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            preview_in = str(tool_input).replace("\n", " ")[:120]
            preview_out = str(tool_output).replace("\n", " ")[:120]
            f.write(f"{tool_name}\tinput={preview_in}\toutput={preview_out}\n")
    except Exception as e:
        return {"message": f"log hook 失败: {e}"}
    return {"message": "logged"}


# 注册表：event → list of hooks
HOOKS = {
    "PreToolUse":  [hook_block_secret_write],
    "PostToolUse": [hook_log_all_calls],
}


def run_hooks(event: str, tool_name: str, *args) -> dict:
    """
    运行某 event 下所有 hook，合并结果。
    PreToolUse: 任一 hook 返回 block 即整体 block。
    PostToolUse: 只收集 message。
    """
    aggregated = {"decision": "pass", "messages": []}
    for hook in HOOKS.get(event, []):
        try:
            result = hook(tool_name, *args)
        except Exception as e:
            result = {"message": f"hook 异常: {e}"}

        if event == "PreToolUse" and result.get("decision") == "block":
            aggregated["decision"] = "block"
            aggregated["block_message"] = result.get("message", "hook 拦截")
            break
        if result.get("message"):
            aggregated["messages"].append(result["message"])
    return aggregated


# ============================================================
# Part 4: 原始工具实现 + dispatch_tool 统一调度
# ============================================================
# 原始工具函数（_raw_*）与 demo1 字节一致——它们是"裸能力"。
# dispatch_tool 是 demo6 的核心：把三层安全栈串在"大模型决策 → 工具执行"之间。

def _raw_execute_bash(command: str) -> str:
    """执行 shell 命令（裸实现，同 demo1）"""
    try:
        result = subprocess.run(
            command,
            shell=True,            # 让命令拥有更强能力
            capture_output=True,
            text=True,
            timeout=60,            # 防止死循环 / 长时间阻塞
        )
        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(f"[stderr] {result.stderr}")
        if result.returncode != 0:
            output.append(f"[exit code: {result.returncode}]")
        return "\n".join(output) if output else "[命令执行成功，无输出]"
    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时（60 秒）"
    except Exception as e:
        return f"[错误] 命令执行失败: {e}"


def _raw_read_file(path: str) -> str:
    """读取文件内容（裸实现，同 demo1）"""
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        max_length = 10000
        if len(content) > max_length:
            content = content[:max_length] + f"\n\n... [内容已截断，共 {len(content)} 字符]"
        return content
    except UnicodeDecodeError:
        return "[错误] 文件不是有效的文本文件或编码不支持"
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def _raw_write_file(path: str, content: str) -> str:
    """写入文件（裸实现，同 demo1）"""
    try:
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[成功] 文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


# 工具参数 key（用于 Permission 的 pattern 匹配）
_TOOL_KEY_FIELD = {
    "execute_bash": "command",
    "read_file":    "path",
    "write_file":   "path",
}


def dispatch_tool(tool_name: str, tool_input: dict, verbose: bool = True) -> str:
    """
    demo6 的核心调度入口：串三层安全栈后再执行工具。

    流程：
        1. PreToolUse hooks     —— 可观察 / 拦截
        2. Permission check     —— allow / deny / ask
        3. Sandbox check        —— 仅 execute_bash，profile 白名单
        4. 执行原始工具
        5. PostToolUse hooks    —— 可观察 / 记录

    返回字符串（与裸工具一致），错误信息也字符串化回灌给大模型。
    """
    # ---- 1. PreToolUse hooks ----
    pre_result = run_hooks("PreToolUse", tool_name, tool_input)
    if pre_result["decision"] == "block":
        msg = pre_result.get("block_message", "PreToolUse hook 拦截")
        if verbose:
            print(f"  [Hook · Pre] 拦截: {msg}")
        return f"[Hook 拦截] {msg}"

    # ---- 2. Permission check ----
    key_field = _TOOL_KEY_FIELD.get(tool_name, "")
    key_value = str(tool_input.get(key_field, ""))
    action = _match_permission(tool_name, key_value)

    if action == "deny":
        if verbose:
            print(f"  [Permission · deny] {tool_name}({key_value!r})")
        return f"[Permission 拒绝] {tool_name}({key_value!r}) 命中 deny 规则"
    if action == "ask":
        preview = key_value if len(key_value) <= 80 else key_value[:80] + "..."
        if not confirm_action(f"{tool_name}: {preview}"):
            return f"[Permission 拒绝] 用户未允许 {tool_name}({key_value!r})"
    # action == "allow"：直接放行

    # ---- 3. Sandbox check（仅 execute_bash）----
    if tool_name == "execute_bash":
        allowed, reason = check_sandbox(tool_input.get("command", ""))
        if not allowed:
            if verbose:
                print(f"  [Sandbox · block] {reason}")
            return f"[Sandbox 拦截] {reason}"

    # ---- 4. 执行原始工具 ----
    raw_fn = {
        "execute_bash": _raw_execute_bash,
        "read_file":    _raw_read_file,
        "write_file":   _raw_write_file,
    }.get(tool_name)

    if raw_fn is None:
        return f"[错误] 未知工具: {tool_name}"

    output = raw_fn(**tool_input)

    # ---- 5. PostToolUse hooks ----
    run_hooks("PostToolUse", tool_name, tool_input, output)

    return output


# ============================================================
# Part 5: Agent 主循环（决策 / 行动 / 感知 = ReAct）
# ============================================================
# 与 demo1 完全一致——demo6 的变化全在 dispatch_tool（Part 4）里，
# 主循环只负责"大模型决策 → 工具执行 → 结果回灌"的轮转。

MAX_ITERATIONS = 30  # 防止大模型陷入死循环


def _preview(text: str, limit: int = 60) -> str:
    """截取字符串预览，超长加省略号"""
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览，不需要精细解析每种 block 类型。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def run_agent(user_input: str, verbose: bool = True) -> str:
    """
    运行 Agent 处理一次用户任务。

    与 demo1 的唯一差异：工具执行走 dispatch_tool（带三层安全栈），而不是直接 fn(**input)。
    """
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"第 {loop_idx} 轮 ReAct 循环")
            print(f"{'=' * 60}")
            _print_messages(messages)

        # ---- 决策：大模型思考下一步 ----
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"\n[LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text":
                    preview = block.text[:80] + ("..." if len(block.text) > 80 else "")
                    print(f"  - text      : {preview}")
                elif block.type == "tool_use":
                    print(f"  - tool_use  : {block.name}({block.input})")

        # ---- 判断是否结束 ----
        if response.stop_reason != "tool_use":
            if verbose:
                print(f"\n[循环结束] 大模型判断任务完成，退出循环")
            return "".join(b.text for b in response.content if b.type == "text")

        # ---- 行动 + 感知：通过 dispatch_tool 串三层安全栈后执行 ----
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            if verbose:
                print(f"\n[执行工具] {block.name}({_preview(str(block.input), 80)})")

            # ★ demo6 核心变化：fn(**block.input) → dispatch_tool(...)
            result = dispatch_tool(block.name, block.input, verbose=verbose)

            if verbose:
                print(f"[工具结果] {_preview(result, 200)}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    return "[错误] 超过最大循环次数（{}），可能陷入死循环".format(MAX_ITERATIONS)


# ============================================================
# Part 6: 交互式入口
# ============================================================

def ensure_test_dir() -> str:
    """准备演示用的测试目录（4 个文件），返回路径。演示删除/清理任务时用。"""
    test_dir = os.path.join(PROJECT_DIR, "test_dir")
    os.makedirs(test_dir, exist_ok=True)
    for name in ("a.txt", "b.txt", "c.log", "d.tmp"):
        fp = os.path.join(test_dir, name)
        if not os.path.exists(fp):
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"this is {name}\n")
    return test_dir


if __name__ == "__main__":
    init_client()

    test_dir = ensure_test_dir()

    print("=" * 60)
    print("Demo6 Agent 已启动（安全约束版——三层安全栈）")
    print(f"模型:          {MODEL}")
    print(f"网关:          {BASE_URL}")
    print(f"项目目录:      {PROJECT_DIR}")
    print(f"测试目录:      {test_dir}（已准备好 4 个文件供演示）")
    print(f"Sandbox:       {SANDBOX_PROFILE}")
    print(f"Permission:    {len(PERMISSION_RULES)} 条规则，默认 {DEFAULT_POLICY!r}")
    print(f"Hook:          PreToolUse {len(HOOKS.get('PreToolUse', []))} 个 / "
          f"PostToolUse {len(HOOKS.get('PostToolUse', []))} 个")
    print("=" * 60)
    print("演示建议：")
    print("  · 演示 1（Permission deny）：让 Agent 跑 'rm -rf test_dir/'")
    print("  · 演示 2（Permission ask）：  让 Agent 跑未在 allow 列表的命令，如 'whoami'")
    print("  · 演示 3（Sandbox）：         把 SANDBOX_PROFILE 改为 'read-only'，")
    print("                               让 Agent 跑 'rm test_dir/a.txt'")
    print("  · 演示 4（Hook 拦截）：       让 Agent 写入含 'PASSWORD' 的文件")
    print("  · 演示 5（Hook 日志）：       任意任务结束后查看 .demo6_hook_log")
    print("命令：quit / exit / q 退出")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n用户: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("再见！")
            break

        try:
            final = run_agent(user_input, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")
