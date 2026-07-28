#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo6 - 带安全约束的 Agent（约束轴）

在 demo1（base = LLM × 工具 × 循环 × 状态）基础上叠加「约束轴」：在 LLM 决策和原始
工具执行之间插入**两层 Runtime Control**，让危险操作可配置、可观测、可拦截——
而不是硬编码在某个工具函数里。

    × Permission（规则引擎） —— 策略层：工具调用前的访问控制（allow / deny / ask）
    × Hook（事件回调）      —— 扩展层：PreToolUse / PostToolUse 可插拔扩展（Pre 拦截 / Post 补改）

公式：demo6 = base × 约束

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化（同 demo1）
    Part 2: 工具定义（同 demo1）
    Part 3: 工具实现（同 demo1：execute_bash / read_file / write_file / edit）
    Part 4: Runtime Control（★ demo6 核心新增：Permission + Hook + dispatch_tool）
    Part 5: Agent 主循环（同 demo1，把 fn() 改成 dispatch_tool()）
    Part 6: 交互式入口

启动：
    python agent.py
"""

import fnmatch
import os
import re
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
    {
        "name": "edit",
        "description": (
            "精确替换文件中的一段文本（string replacement）。"
            "比 write_file 整文件覆写更精细，适合改一行 / 改一个值。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string",  "description": "要编辑的文件路径"},
                "old":         {"type": "string",  "description": "要替换的原文本（必须精确匹配，含空格/缩进）"},
                "new":         {"type": "string",  "description": "替换为的新文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配处（默认 false，只替换第一处）"},
            },
            "required": ["path", "old", "new"],
        },
    },
]


# ============================================================
# Part 3: 工具实现 + 路由表
# ============================================================
# 每个工具是一个普通 Python 函数：
#   - 错误信息也字符串化返回给大模型，让它自己看到错误后调整策略
#   - 设置超时，防止死循环或长时间阻塞

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding="utf-8",      # GBK Windows 下 text=True 会崩，显式 UTF-8
            errors="replace",
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


def read_file(path: str) -> str:
    """读取文件内容"""
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        max_length = 20000
        if len(content) > max_length:
            content = content[:max_length] + f"\n\n... [内容已截断，共 {len(content)} 字符]"
        return content
    except UnicodeDecodeError:
        return "[错误] 文件不是有效的文本文件或编码不支持"
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def write_file(path: str, content: str) -> str:
    """写入文件"""
    try:
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[成功] 文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


def edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """精确替换文件中的文本"""
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        if not old:
            return "[错误] old 不能为空"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        occurrences = content.count(old)
        if occurrences == 0:
            return f"[错误] 未找到匹配文本，请用 read_file 确认精确内容"
        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        which = f"全部 {occurrences} 处" if replace_all else f"第 1 处（共 {occurrences} 处）"
        return f"[成功] {path} 替换 {which}"
    except Exception as e:
        return f"[错误] 编辑文件失败: {e}"


# 路由表：工具名 → 实际函数（调度核心）
# 当大模型说「我要调用 execute_bash」时，Agent 通过这张表把名字映射到具体函数并执行。
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "edit":         edit,
}


# ============================================================
# Part 4: Runtime Control（★ demo6 核心新增）
# ============================================================
# demo1 的工具是「裸奔」的——execute_bash 想跑什么就跑什么，write_file 想写哪里就写哪里。
# 真实 Agent（Claude Code / Cursor）都在 LLM 和原始工具之间插入 Runtime Control，
# 把"能不能做"从工具函数里抽出来，变成**可配置、可插拔、可观测**的独立层。
#
# Runtime Control 分两层（dispatch_tool 串接，任一层阻断都返回错误给大模型）：
#   · Permission —— 策略层：规则匹配 → allow / deny / ask（类似 Claude Code 的 permission）
#   · Hook       —— 扩展层：Pre/PostToolUse 回调，可拦、可改、可记录（类似 Claude Code 的 hook）
#
# 两层关系：Hook 可改 input 或拦 → Permission 决策是否允许 → 放行后执行原始工具。
# 大模型从错误信息里看到原因，自行调整策略。


# ------------------------------------------------------------
# Part 4.1: Permission —— 声明式规则引擎
# ------------------------------------------------------------
# 规则格式：(tool_name, pattern, action)
#   tool_name: 工具名（execute_bash / read_file / write_file）
#   pattern:   fnmatch shell 通配符，匹配工具的关键参数
#              execute_bash 匹配 command；read_file/write_file 匹配 path
#   action:    "allow" / "deny" / "ask"
#
# 匹配顺序：从上到下，first-match wins（先命中的规则决定结果）。
# 无命中时走 DEFAULT_POLICY。

PERMISSION_RULES = [
    # —— 显式 deny —— 绝不让 LLM 跑的命令
    ("execute_bash", "rm -rf *",       "deny"),
    ("execute_bash", "mkfs.*",         "deny"),
    ("execute_bash", "shutdown*",      "deny"),
    ("execute_bash", "curl *| *sh*",   "deny"),

    # —— 显式 allow —— 安全只读类，免确认
    ("execute_bash", "ls *",           "allow"),
    ("execute_bash", "dir *",          "allow"),
    ("execute_bash", "cat *",          "allow"),
    ("execute_bash", "grep *",         "allow"),

    # —— 其他 execute_bash：问一下
    ("execute_bash", "*",              "ask"),

    # —— read_file / write_file：默认放行
    ("read_file",    "*",              "allow"),
    ("write_file",   "*",              "allow"),
]

DEFAULT_POLICY = "ask"

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
# Part 4.2: Hook（Lifecycle Extension）—— PreToolUse / PostToolUse 事件回调
# ------------------------------------------------------------
# Hook 是可插拔的 Lifecycle Extension：在工具调用生命周期的前后点注入自定义逻辑。
# Claude Code 把这类机制叫 "hook"；概念上它是「在生命周期点上扩展行为」，所以本 demo
# 也称它为 Lifecycle Extension——两个名字同一个东西。
#
# 本 demo 用 Python callable 内联实现。
#
# Hook 函数签名：
#   pre_hook(tool_name, tool_input) -> dict
#       返回 {"decision": "pass"|"block"|"modify", ...}
#         pass 放行不改 / block 拦截 / modify 改写 input 后放行（带 modified_input）
#   post_hook(tool_name, tool_input, tool_output) -> dict
#       返回 {"message": "...", "modified_output": "...", "output_delta": "..."}
#         modified_output 替换整个 output（如把读出的密钥脱敏）/ output_delta 追加到末尾回灌 LLM
#
# 下方注册两个示例 hook：
#   1. inject_shebang：Pre，给 write_file 写的 .sh 脚本补上 #!/bin/bash shebang（modify）
#   2. scan_output：Post，把读出内容里的密钥脱敏成 [xxxxx] 并补「已脱敏」提示（modified_output + output_delta）


# Post hook（scan_output）用它把读出内容里的密钥值替换为 [xxxxx]（保留 key，大小写不敏感）。
# 教学版只认 password / api_key / secret 这几种 key。
_REDACT_RE = re.compile(
    r'((?:password|api_key|apikey|secret)\s*[:=]\s*)\S+',
    re.IGNORECASE,
)


def hook_inject_shebang(tool_name: str, tool_input: dict) -> dict:
    """Pre 示例：给 write_file 写的 .sh 脚本补上 #!/bin/bash shebang 再放行（不改路径、不拦截）。

    这体现 Hook 区别于 Permission 的杀手锏——Permission 只能 allow/deny/ask，改不了
    input；Hook 能「改写」：检测到写 .sh 脚本时，若开头没有 shebang，就在最前面补一行
    #!/bin/bash，让脚本可以 ./script 直接执行（不用 bash script）。
    """
    if tool_name != "write_file":
        return {"decision": "pass"}
    path = tool_input.get("path", "")
    if not path.endswith(".sh"):                  # 只管 .sh 脚本
        return {"decision": "pass"}
    content = tool_input.get("content", "")
    if content.startswith("#!"):                  # 已有 shebang（任意 #! 开头）就不重复加
        return {"decision": "pass"}
    new_input = dict(tool_input)
    new_input["content"] = "#!/bin/bash\n" + content
    return {
        "decision": "modify",
        "modified_input": new_input,
        "message": "Hook 注入：写入 .sh 脚本，已在开头补上 #!/bin/bash shebang",
    }


def hook_scan_output(tool_name: str, tool_input: dict, tool_output: str) -> dict:
    """Post 示例：把工具输出里的疑似密钥/凭证脱敏成 [xxxxx]，再补「已脱敏」提示。

    read_file 读出的明文密钥（password=hunter2）原样回灌有外传风险——Post 在「回灌前」把值换成
    [xxxxx]：modified_output 让 dispatch_tool 替换整个 output，output_delta 追加提示。
    """
    redacted, n = _REDACT_RE.subn(r"\1[xxxxx]", tool_output)
    if n == 0:
        return {"message": "no secrets detected", "output_delta": ""}
    delta = f"\n\nℹ️ [Post hook] 输出含疑似凭证，已将 {n} 处敏感值脱敏为 [xxxxx]。"
    return {"message": f"redacted {n}", "modified_output": redacted, "output_delta": delta}


# 注册表：event → list of hooks
HOOKS = {
    "PreToolUse":  [hook_inject_shebang],
    "PostToolUse": [hook_scan_output],
}


def run_hooks(event: str, tool_name: str, *args) -> dict:
    """
    运行某 event 下所有 hook，合并结果。
    PreToolUse: block 立即终止；modify 链式改写 input（后一个 hook 看到前一个改写后的 input）。
    PostToolUse: 收集 message + output_delta；modified_output 取最后一个 hook 的（替换整个 output，
                 如把读出的密钥脱敏）。教学版单 Post hook，不做链式替换。
    """
    aggregated = {"decision": "pass", "messages": [], "output_delta": "", "modified_output": None}
    current_input = args[0] if (event == "PreToolUse" and args) else None
    for hook in HOOKS.get(event, []):
        try:
            hook_args = (current_input,) if event == "PreToolUse" else args
            result = hook(tool_name, *hook_args)
        except Exception as e:
            result = {"message": f"hook 异常: {e}"}

        if event == "PreToolUse":
            decision = result.get("decision", "pass")
            if decision == "block":
                aggregated["decision"] = "block"
                aggregated["block_message"] = result.get("message", "hook 拦截")
                break
            if decision == "modify" and result.get("modified_input") is not None:
                current_input = result["modified_input"]
                aggregated["decision"] = "modify"
        if event == "PostToolUse" and result.get("modified_output") is not None:
            aggregated["modified_output"] = result["modified_output"]
        if result.get("message"):
            aggregated["messages"].append(result["message"])
        if result.get("output_delta"):
            aggregated["output_delta"] += result["output_delta"]
    if aggregated["decision"] == "modify":
        aggregated["modified_input"] = current_input
    return aggregated


# dispatch_tool：把上面的 Permission(4.1) + Hook(4.2) 串进"大模型决策 → 工具执行"之间。


def dispatch_tool(tool_name: str, tool_input: dict, verbose: bool = True) -> str:
    """
    demo6 的核心调度入口：串两层 Runtime Control 后再执行工具。

    流程：
        1. PreToolUse hooks     —— 可观察 / 拦截 / 改写 input（Extension Layer）
        2. Permission check     —— allow / deny / ask（Policy Layer）
        3. 执行原始工具（用 Pre 改写后的 input）
        4. PostToolUse hooks    —— 可观察 / 替换 / 补改 output（Extension Layer）

    返回字符串（与裸工具一致），错误信息也字符串化回灌给大模型。
    """
    # 工具参数 key（用于 Permission 的 pattern 匹配）：只有 dispatch_tool 用，就内联在这里
    _TOOL_KEY_FIELD = {
        "execute_bash": "command",
        "read_file":    "path",
        "write_file":   "path",
        "edit":         "path",
    }

    # ---- 1. PreToolUse hooks（可观察 / 拦截 / 改写 input）----
    pre_result = run_hooks("PreToolUse", tool_name, tool_input)
    if pre_result["decision"] == "block":
        msg = pre_result.get("block_message", "PreToolUse hook 拦截")
        if verbose:
            print(f"  [Hook · Pre] 拦截: {msg}")
        return f"[Hook 拦截] {msg}"
    if pre_result["decision"] == "modify":
        tool_input = pre_result["modified_input"]
        if verbose:
            msg = "; ".join(pre_result.get("messages", [])) or "input 已改写"
            print(f"  [Hook · Pre] 改写输入: {msg}")

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

    # ---- 3. 执行原始工具 ----
    raw_fn = AVAILABLE_FUNCTIONS.get(tool_name)

    if raw_fn is None:
        return f"[错误] 未知工具: {tool_name}"

    try:
        output = str(raw_fn(**tool_input))
    except Exception as e:
        return f"[错误] 工具 {tool_name} 执行失败: {e}"

    # ---- 4. PostToolUse hooks（可观察 / 替换 / 补改 output）----
    post_result = run_hooks("PostToolUse", tool_name, tool_input, output)
    if post_result.get("modified_output") is not None:
        output = post_result["modified_output"]   # Post 替换整个 output（如把读出的密钥脱敏成 [xxxxx]）
    if post_result.get("output_delta"):
        output += post_result["output_delta"]     # Post 补的内容（如「已脱敏」提示）回灌给 LLM

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
    """调试打印——只是给人看的预览。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[调用工具 {block.get('name')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(str(block.get("content", ""))[:100])
                else:
                    t = getattr(block, "type", None)
                    if t == "text":
                        parts.append(getattr(block, "text", ""))
                    elif t == "tool_use":
                        parts.append(f"[调用工具 {getattr(block, 'name', '')}]")
            content = "\n".join(parts)
        print(f"  [{i}] {msg.get('role', '?'):<9}: {_preview(content)}")
    print()


def run_agent(user_input: str, verbose: bool = True) -> str:
    """
    运行 Agent 处理一次用户任务。

    Args:
        user_input: 用户的任务目标
        verbose: 是否打印每一轮的决策与行动（教学演示建议开启）

    Returns:
        Agent 的最终文本回复

    与 demo1 的唯一差异：工具执行走 dispatch_tool（带 Runtime Control），而不是直接 fn(**input)。
    """
    messages = [{"role": "user", "content": user_input}]
    system_prompt = "你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。"

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
            system=system_prompt,
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

        # ---- 行动 + 感知：通过 dispatch_tool 串 Runtime Control 后执行 ----
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                # ★ demo6 核心变化：通过 dispatch_tool 把工具名分发到实际函数（带 Runtime Control）
                if verbose:
                    print(f"\n[执行工具] {block.name}({block.input})")
                result = dispatch_tool(block.name, block.input, verbose=verbose)

                if verbose:
                    preview = str(result)[:200] + (
                        "..." if len(str(result)) > 200 else ""
                    )
                    print(f"[工具结果] {preview}")

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,  # Tool ID 精确匹配每次调用
                        "content": result,
                    }
                )

        # 把工具结果作为 user 消息追加进 messages，下一轮大模型就能看到
        messages.append({"role": "user", "content": tool_results})

    return "[错误] 超过最大循环次数（{}），可能陷入死循环".format(MAX_ITERATIONS)


# ============================================================
# Part 6: 交互式入口
# ============================================================

def ensure_test_dir(base_dir: str) -> str:
    """准备演示用的测试目录（5 个文件），返回路径。演示删除/清理/读取任务时用。"""
    test_dir = os.path.join(base_dir, "test_dir")
    os.makedirs(test_dir, exist_ok=True)
    for name in ("a.txt", "b.txt", "c.log", "d.tmp"):
        fp = os.path.join(test_dir, name)
        if not os.path.exists(fp):
            with open(fp, "w", encoding="utf-8") as f:
                f.write(f"this is {name}\n")
    # db.conf 含明文凭证，演示 Post hook 扫描输出（read_file 它 → 命中密钥 → 替换成 [xxxxx] + 提示）
    db_conf = os.path.join(test_dir, "db.conf")
    if not os.path.exists(db_conf):
        with open(db_conf, "w", encoding="utf-8") as f:
            f.write("db_host=db.example.com\n")
            f.write("api_key=sk-proj-demo1234567890abcdef\n")
            f.write("password=hunter2\n")
    return test_dir


if __name__ == "__main__":
    init_client()

    project_dir = os.path.dirname(os.path.abspath(__file__))
    test_dir = ensure_test_dir(project_dir)

    print("=" * 60)
    print("Demo6 Agent 已启动（安全约束版——两层 Runtime Control）")
    print(f"模型:          {MODEL}")
    print(f"网关:          {BASE_URL}")
    print(f"项目目录:      {project_dir}")
    print(f"测试目录:      {test_dir}（已准备好 5 个文件供演示）")
    print(f"Permission:    {len(PERMISSION_RULES)} 条规则，默认 {DEFAULT_POLICY!r}")
    print(f"Hook:          PreToolUse {len(HOOKS.get('PreToolUse', []))} 个 / "
          f"PostToolUse {len(HOOKS.get('PostToolUse', []))} 个")
    print("=" * 60)
    print("演示建议：")
    print("  · 演示 1（Permission deny）：让 Agent 删除 test_dir 目录")
    print("  · 演示 2（Permission allow）：让 Agent 看看 test_dir 目录下有什么文件")
    print("  · 演示 3（Hook 注入）：       让 Agent 在 test_dir 下写个 hi.sh，内容是 echo hello")
    print("  · 演示 4（Hook 扫输出）：     read_file test_dir/db.conf，看 Post hook 把密钥脱敏成 [xxxxx]")
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
