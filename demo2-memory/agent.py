#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo2 - 带记忆的 Agent（记忆轴）

在 demo1（base = LLM × 工具 × 循环 × 状态）基础上叠加「记忆轴」：
    × 长期记忆（agent_memory.md，跨任务持久化）
    × 动态压缩（compact_messages，老消息滚动摘要）
    × Prompt caching（cache_control breakpoint，减少重复传输）

公式：demo2 = base × 记忆

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化（同 demo1）
    Part 2: 工具定义（同 demo1，3 件套）
    Part 3: 工具实现 + 路由表（同 demo1）
    Part 4: 长期记忆系统（agent_memory.md 滑动窗口）
    Part 5: 上下文管理（compact_messages 动态压缩 + cache_control caching）
    Part 6: Agent 主循环（ReAct + compact 触发 + caching 命中统计）

用法：
    python agent.py
"""

import os
import subprocess
from datetime import datetime

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

# Prompt caching 开关：某些 Anthropic 兼容网关不实现 cache_control 后端，
# 设为 False 时回退为普通字符串 system prompt（功能正常，只是不命中缓存）。
USE_CACHE_CONTROL = True


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

SYSTEM_PROMPT_BASE = """你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。"""


# ============================================================
# Part 3: 工具实现 + 路由表
# ============================================================
# 每个工具是一个普通 Python 函数：
#   - 错误信息也字符串化返回给大模型，让它自己看到错误后调整策略
#   - 设置超时，防止死循环或长时间阻塞
#   - shell=True 让命令拥有更强能力（风险换能力）

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
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


def read_file(path: str) -> str:
    """读取文件内容"""
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


# 路由表：工具名 → 实际函数（调度核心）
# 当大模型说「我要调用 execute_bash」时，Agent 通过这张表把名字映射到具体函数并执行。
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file": read_file,
    "write_file": write_file,
}


# ============================================================
# Part 4: 长期记忆系统（demo2 新增）
# ============================================================
# 用一个 Markdown 文件做跨任务长期记忆：
#   - 每次 task 结束，追加「时间 + 任务 + 结果摘要」
#   - 每次构建 system prompt 时，加载最后 50 行作为「Progressive Context」
#   - 这是滑动窗口：窗口大小固定，旧记忆会随新记忆累积被挤出窗口
#
# 揭示的本质：大模型有上下文窗口限制，本地必须把外部存储的信息
#             有选择地搬运进 prompt。所有记忆方案（向量库、压缩、
#             分层）底层都是「存在哪 + 怎么存 + 搬多少」的问题。

MEMORY_FILE          = "agent_memory.md"
MEMORY_WINDOW_LINES  = 50   # 滑动窗口大小（行数）


def load_memory() -> str:
    """
    加载记忆文件最后 N 行。
    第一次运行时文件不存在 → 返回空字符串（无 Progressive Context）。
    """
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 滑动窗口：只取最后 N 行
        window = lines[-MEMORY_WINDOW_LINES:]
        return "".join(window)
    except Exception as e:
        print(f"[警告] 读取记忆文件失败: {e}")
        return ""


def append_memory(task: str, result: str) -> None:
    """
    Task 级结束写入记忆。
    结构：时间戳 + 任务原文 + 结果摘要（前 500 字，防止文件膨胀）
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 结果摘要限长，避免一次任务把整个文件撑爆
    result_preview = (result or "").strip()
    if len(result_preview) > 500:
        result_preview = result_preview[:500] + "..."

    entry = (
        f"\n## [{timestamp}]\n"
        f"**任务**: {task}\n"
        f"**结果**: {result_preview}\n"
    )
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[警告] 写入记忆文件失败: {e}")


def build_system_prompt(verbose: bool = False) -> str:
    """
    构建 system prompt = 基础 prompt + Progressive Context（历史记忆后缀）。

    每次任务开始构建一次，整个 task 内不变（任务进行中记忆不会变化，
    只有任务结束后才会追加新条目）。
    """
    memory = load_memory()
    if verbose:
        if memory:
            n_lines = len(memory.splitlines())
            n_tasks = sum(1 for line in memory.splitlines() if line.startswith("## ["))
            print(f"[记忆] 已加载 {n_tasks} 条历史任务（{n_lines} 行）作为 Progressive Context:")
            for line in memory.splitlines():
                if line.startswith("## ["):
                    print(f"   {line}")
                elif line.startswith("**任务**:"):
                    print(f"     {line}")
        else:
            print(f"[记忆] 无历史记忆（首次运行或文件为空）")

    if not memory.strip():
        return SYSTEM_PROMPT_BASE
    return SYSTEM_PROMPT_BASE + "\n\n## 历史任务记忆（最近）\n\n" + memory


# ============================================================
# Part 5: 上下文管理（demo2 新增）
# ============================================================
# 两个核心机制：
#
#   (A) Prompt caching（cache_control breakpoint）
#       长 system prompt + 历史 memory 每轮请求都重发 → 重复传输。
#       Anthropic API 支持在 system blocks 上加 cache_control 标记，
#       第一次请求创建缓存（5min TTL），后续命中缓存时服务端直接复用，无需重传。
#       Claude Code 每次请求都用 cache_control，工业 Agent 必备。
#
#   (B) compact_messages（动态压缩）
#       多轮 ReAct 把 messages 撑爆上下文窗口（如 1M）。
#       达到阈值时，把老消息让 LLM 摘要成一段，保留最近 N 条原始消息。

# 压缩触发阈值（消息条数）。生产级按 token 占比触发（见总览第八节）。
COMPACT_THRESHOLD_MESSAGES = 10  # 演示用低阈值，方便短任务就触发一次压缩
COMPACT_KEEP_RECENT        = 4   # 压缩时保留最近 N 条原始消息

COMPACT_SYSTEM_PROMPT = """你是上下文压缩助手。把下面的 Agent 对话历史压缩成一段简洁的事实摘要。

要求：
1. 保留：用户意图、关键决策、工具调用的核心结果（文件路径/数字/结论）
2. 丢弃：重复的试错、冗长的工具原始输出、无关细节
3. 用一段 200-400 字的连贯叙述输出，不要分点列条
4. 不要加任何前缀说明，直接输出摘要内容"""


def _build_system_param(system_prompt: str):
    """
    构建 messages.create 的 system 参数。
    - USE_CACHE_CONTROL=True：返回 blocks 形式，最后一个 block 带 cache_control
    - USE_CACHE_CONTROL=False：返回纯字符串（兼容不支持 caching 的网关）
    """
    if not USE_CACHE_CONTROL:
        return system_prompt
    return [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]


def _extract_text(content) -> str:
    """
    从 message content（str 或 block list）提取纯文本，便于估算/摘要。

    支持三种 content 形态：
        - str                      → 直接返回
        - list of dict             → demo2 自己拼的 messages（tool_result 也是 dict）
        - list of SDK block 对象   → demo1 沿用的 response.content（assistant 回复）

    block 类型处理：
        text         → 取 text
        tool_use     → "[调用工具 name]"（含 input 摘要让摘要器看到决策）
        tool_result  → 取 content（截断到 200 字符，避免污染摘要）
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts = []
    for block in content:
        # dict 和 SDK block 对象统一用 .get / getattr 取字段
        get = block.get if isinstance(block, dict) else lambda k, d="": getattr(block, k, d)
        btype = get("type")

        if btype == "text":
            parts.append(get("text", ""))
        elif btype == "tool_use":
            args_preview = str(get("input", ""))[:80]
            parts.append(f"[调用工具 {get('name', '')}] {args_preview}")
        elif btype == "tool_result":
            # tool_result 的 content 可能是 str 或 list of {type:text}
            rc = get("content", "")
            parts.append(_extract_text(rc) if isinstance(rc, list) else str(rc)[:200])
    return "\n".join(parts)


def _is_tool_result_message(msg) -> bool:
    """判断 message 是否为「承载 tool_result 的 user 消息」（与触发它的 assistant tool_use 配对）"""
    role = msg.get("role") if isinstance(msg, dict) else getattr(msg, "role", None)
    if role != "user":
        return False
    content = msg.get("content", []) if isinstance(msg, dict) else getattr(msg, "content", [])
    if not isinstance(content, list):
        return False
    return any(
        (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "tool_result"
        for b in content
    )


def _find_recent_start(messages: list) -> int:
    """
    找 recent 段的起始 index（old = messages[:start], recent = messages[start:]）。

    切点不能落在 tool_result 消息上——否则前缀的 summary_msg(user) + ack_msg(assistant)
    会切断 tool_result 与触发它的 assistant tool_use 的配对，导致 API 报
    "tool_result without preceding tool_use"。遇到 tool_result 就向前回退一步。
    """
    start = max(1, len(messages) - COMPACT_KEEP_RECENT)
    while start > 1 and _is_tool_result_message(messages[start]):
        start -= 1
    return start


def should_compact(messages: list) -> bool:
    """是否需要触发压缩：消息条数超阈值"""
    return len(messages) >= COMPACT_THRESHOLD_MESSAGES


def compact_messages(messages: list, verbose: bool = False) -> list:
    """
    动态压缩 messages：保留最近 N 条，老的让 LLM 摘要成一段。

    返回新的 messages list（不修改原 list）。摘要失败时静默回退到原 messages。
    """
    if len(messages) < COMPACT_THRESHOLD_MESSAGES:
        return messages

    # 切点保护：不能让 recent 第一条是 tool_result 消息（会切断 tool_use ↔ tool_result 配对）
    recent_start = _find_recent_start(messages)
    old_messages = messages[:recent_start]
    recent_messages = messages[recent_start:]

    if verbose:
        back = len(recent_messages) - COMPACT_KEEP_RECENT
        back_note = f"（回退 {back} 步避开 tool_result）" if back > 0 else ""
        print(f"\n[compact] 触发：{len(old_messages)} 条老消息 → 摘要")
        print(f"[compact] 保留最近 {len(recent_messages)} 条原始消息{back_note}")

    # 把老消息转成纯文本给 LLM 摘要
    transcript_parts = []
    for msg in old_messages:
        role = msg.get("role", "?")
        text = _extract_text(msg.get("content", ""))
        transcript_parts.append(f"### {role}\n{text}")
    transcript = "\n\n".join(transcript_parts)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=COMPACT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"对话历史：\n\n{transcript}\n\n请输出压缩摘要："}],
        )
        summary = "".join(b.text for b in response.content if b.type == "text")

        if verbose:
            preview = summary.replace("\n", " ")[:200]
            print(f"[compact] 摘要: {preview}...")

        # 把摘要注入成 [历史对话摘要] 标记消息，让后续 LLM 知道这是压缩过的上下文
        summary_msg = {
            "role": "user",
            "content": f"[历史对话已压缩，摘要如下]\n{summary}",
        }
        ack_msg = {
            "role": "assistant",
            "content": "好的，我已了解历史对话摘要，继续执行当前任务。",
        }

        new_messages = [summary_msg, ack_msg] + recent_messages
        if verbose:
            print(f"[compact] 压缩后：{len(new_messages)} 条消息")
        return new_messages

    except Exception as e:
        if verbose:
            print(f"[compact] 摘要失败 ({e})，保留原 messages 不压缩")
        return messages


# ============================================================
# Part 6: Agent 主循环（ReAct + compact + caching）
# ============================================================
# 与 demo1 的核心区别：
#   - system prompt 走 cache_control（首请求建缓存，后续命中）
#   - 每轮 ReAct 前检查是否需要 compact_messages
#   - task 结束写入长期记忆 agent_memory.md
#   - 每轮打印 cache hit/miss 统计（cache_creation vs cache_read）

MAX_ITERATIONS = 30


def _preview(text: str, limit: int = 60) -> str:
    """截取字符串预览，超长加省略号"""
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览，不需要精细解析每种 block 类型。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        text = _extract_text(msg.get("content", ""))
        print(f"  [{i}] {msg.get('role', '?'):<9}: {_preview(text)}")
    print()


def _print_cache_stats(usage, verbose: bool = True) -> None:
    """打印 cache 命中统计（教学用，让读者直观看到 caching 效果）"""
    if not verbose or usage is None:
        return
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
    input_tokens = getattr(usage, "input_tokens", 0) or 0

    if cache_create > 0:
        print(f"  [cache] 创建缓存 {cache_create} tokens + 输入 {input_tokens}")
    elif cache_read > 0:
        print(f"  [cache] 命中缓存 {cache_read} tokens + 输入 {input_tokens} ✓")
    elif USE_CACHE_CONTROL:
        # cache_control 发了但本轮网关没返回命中数据——可能是网关内部缓存策略（如
        # 大小上限 / TTL 短），也可能是兼容网关根本不实现 caching 后端。
        print(f"  [cache] 未命中 / 输入 {input_tokens} tokens")
    else:
        print(f"  [cache] caching 关闭 / 输入 {input_tokens} tokens")


def run_agent(user_input: str, verbose: bool = True) -> str:
    """
    ReAct 主循环，集成记忆轴的所有机制。

    流程：
        1. 加载长期记忆 → 构建 system prompt（含 Progressive Context）
        2. 进入 ReAct 循环：
           a. 检查是否触发 compact_messages
           b. 调 LLM（system 走 cache_control）
           c. 判停 / 行动 / 感知（同 demo1）
        3. Task 结束写入长期记忆

    Returns:
        最终助手的文本回复
    """
    # 1. 构建 system prompt（含历史记忆）+ 转 cache_control blocks
    system_prompt = build_system_prompt(verbose=verbose)
    system_param = _build_system_param(system_prompt)

    # 2. ReAct 循环
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n----- ReAct 第 {loop_idx} 轮 -----")
            _print_messages(messages)

        # 2a. 上下文管理：检查是否需要 compact
        if should_compact(messages):
            messages = compact_messages(messages, verbose=verbose)

        # 2b. 决策：调 LLM（system 走 cache_control）
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_param,
            tools=TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"[LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text":
                    print(f"  - text     : {_preview(block.text, 80)}")
                elif block.type == "tool_use":
                    print(f"  - tool_use : {block.name}({block.input})")
            _print_cache_stats(response.usage, verbose=verbose)

        # 2c. 判停
        if response.stop_reason != "tool_use":
            if verbose:
                print(f"[任务结束] 大模型判断完成")
            result = "".join(b.text for b in response.content if b.type == "text")
            break

        # 2d. 行动 + 感知
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                fn = AVAILABLE_FUNCTIONS.get(block.name)
                if fn is None:
                    result = f"[错误] 未知工具: {block.name}"
                else:
                    if verbose:
                        print(f"[执行工具] {block.name}({block.input})")
                    result = fn(**block.input)

                if verbose:
                    print(f"[工具结果] {_preview(result, 200)}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})
    else:
        result = "[错误] 超过最大循环次数"

    # 3. Task 级结束：写入长期记忆
    append_memory(user_input, result)
    if verbose:
        print(f"\n[记忆] 已写入 {MEMORY_FILE}（任务 + 结果摘要）")

    return result


# ============================================================
# 交互式入口
# ============================================================

def main() -> None:
    # 未配置 API Key 时会交互式提示输入
    init_client()

    print("=" * 60)
    print("Demo2 Agent 已启动（记忆轴）")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"记忆:   {MEMORY_FILE}（窗口 {MEMORY_WINDOW_LINES} 行）")
    print(f"压缩:   compact_messages（阈值 {COMPACT_THRESHOLD_MESSAGES} 条 / 保留 {COMPACT_KEEP_RECENT} 条）")
    print(f"缓存:   cache_control={'on' if USE_CACHE_CONTROL else 'off'}")
    print("命令:   /memory 查看记忆 / quit 退出")
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

        if user_input.lower() in {"/memory", "/m"}:
            print(f"\n--- {MEMORY_FILE} 内容 ---")
            print(load_memory() or "(空)")
            print(f"--- end ---")
            continue

        try:
            final = run_agent(user_input, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
