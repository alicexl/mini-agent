#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo2 记忆层 — 记忆轴的全部机制

demo2 = base × 记忆。记忆轴在此独立成文件（与 demo1「工具是独立资产」同构）：

    跨会话记忆     memory/MEMORY.md，模型自主维护（对齐 Claude Code）：system prompt
                  带维护指引，模型发现值得持久的事实就用 write_file / edit 更新；
                  每次任务开始加载其内容进 system prompt
    动态压缩       compact_messages：达到阈值时，老消息让 LLM 摘要成一段，
                  保留最近 N 条原始消息
    Prompt caching cache_control breakpoint：长 system prompt 前缀复用，
                  首次请求创建缓存，后续命中免重传

memory/ 目录下的双层记忆分工（对照 Claude Code 的 MEMORY.md + projects/*.jsonl）：
    memory/MEMORY.md         项目级——模型自主维护的持久事实，跨会话加载进 system prompt（本文件）
    memory/<会话ID>.jsonl    会话级——完整 messages，/resume 恢复（见 session.py）

揭示的本质：大模型有上下文窗口限制，本地必须把外部存储的信息有选择地
搬运进 prompt。所有记忆方案（向量库、压缩、分层）底层都是
「存在哪 + 怎么存 + 搬多少」的问题。
"""

import os


# ============================================================
# 可调参数（demo2 的实验区）
# ============================================================

# --- 跨会话记忆（项目级，存 memory/ 目录，模型自主维护） ---
MEMORY_FILE         = os.path.join("memory", "MEMORY.md")  # 项目级记忆文件（模型维护）
MEMORY_WINDOW_LINES = 50                 # 防止上下文无限增长：最多取最后 N 行

# --- 动态压缩 ---
# 压缩触发阈值（消息条数）。生产级按 token 占比触发（见总览第八节）。
COMPACT_THRESHOLD_MESSAGES = 12  # 演示用低阈值，方便短任务就触发一次压缩
COMPACT_KEEP_RECENT        = 4   # 压缩时保留最近 N 条原始消息

# --- 任务结束记忆回顾 ---
REVIEW_TAIL_MESSAGES = 10  # 回顾时带给模型的会话末尾条数（控制成本）

# --- Prompt caching ---
# 某些 Anthropic 兼容网关不实现 cache_control 后端，
# 设为 False 时回退为普通字符串 system prompt（功能正常，只是不命中缓存）。
USE_CACHE_CONTROL = True

# --- 基础 system prompt（同 demo1 新 base：身份 + 工具 + 工作流程） ---
BASE_PROMPT = (
    "你是一个有用的助手，可以通过工具读写文件、执行命令，帮用户完成任务。"
    "工作流程：先理解需求，再动手实现，实现完后必须运行验证。"
)


# ============================================================
# 跨会话记忆：MEMORY.md（模型自主维护）加载
# ============================================================

def load_memory() -> str:
    """
    加载记忆文件（模型自主维护：正常应保持精炼，最后 N 行是防止上下文无限增长）。
    第一次运行时文件不存在 → 返回空字符串。
    """
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        window = lines[-MEMORY_WINDOW_LINES:]
        return "".join(window)
    except Exception as e:
        print(f"[警告] 读取记忆文件失败: {e}")
        return ""


# 记忆维护指引（对齐 Claude Code 的 MEMORY.md：模型用普通文件工具自己维护）
MEMORY_GUIDANCE = """

## 记忆维护指引

以上记忆文件（memory/MEMORY.md）由你维护：当对话中出现值得跨会话记住的
持久事实（用户偏好、项目约定、关键结论），主动用 write_file 或 edit 更新它——
只记事实、保持精炼，琐碎的过程不要写。没有值得记的，就不用写。"""


def build_system_prompt(verbose: bool = False) -> str:
    """
    构建 system prompt = 基础 prompt + 跨会话记忆（MEMORY.md 内容 + 维护指引）。

    每次任务开始构建一次，整个任务内不变——system 稳定，缓存才能命中。
    """
    memory = load_memory()
    if verbose:
        if memory:
            n_lines = len(memory.splitlines())
            print(f"[记忆] 已加载 {MEMORY_FILE}（{n_lines} 行）:")
            for line in memory.splitlines():
                if line.strip():
                    print(f"   {line}")
        else:
            print(f"[记忆] 无跨会话记忆（{MEMORY_FILE} 为空或不存在——首次运行，或还没有值得记住的事实）")

    if not memory.strip():
        return BASE_PROMPT + MEMORY_GUIDANCE
    return BASE_PROMPT + "\n\n## 跨会话记忆（memory/MEMORY.md）\n\n" + memory + MEMORY_GUIDANCE


def review_memory(history: list, client, model: str, verbose: bool = False) -> None:
    """
    任务结束的「记忆回顾」：给模型一次机会判断本次对话有没有值得跨会话
    记住的持久事实——有就让它自己用 write_file / edit 更新 MEMORY.md，
    没有就直接结束。不强制：写不写由模型判断（对应指引「没有值得记的，
    就不用写」）。

    这是纯靠对话中自发写入之外的一道兜底：重要事实即使模型当场没想起来
    写，任务收尾时也会被再问一次。带 REVIEW_TAIL_MESSAGES 条会话末尾做
    上下文，最多跑 3 轮（写一次 + 确认，足够）。
    """
    if not history:
        return
    from tools import TOOLS, AVAILABLE_FUNCTIONS  # 延迟导入，避免层次纠缠

    messages = [dict(m) for m in history[-REVIEW_TAIL_MESSAGES:]]
    messages.append({"role": "user", "content": (
        "任务已结束。请回顾以上对话：是否出现了值得跨会话记住的持久事实"
        "（用户偏好、项目约定、关键结论）？"
        "有就用 write_file / edit 更新 memory/MEMORY.md（只记事实、保持精炼）；"
        "没有就直接回答「无需更新」，不要为了写而写。"
    )})

    updated = False
    for _ in range(3):
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=build_system_prompt(),
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name in ("write_file", "edit"):
                updated = True
            fn = AVAILABLE_FUNCTIONS.get(block.name)
            result = str(fn(**(block.input or {}))) if fn else f"[错误] 未知工具: {block.name}"
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
        messages.append({"role": "user", "content": tool_results})

    if verbose:
        if updated:
            print(f"[记忆] 任务结束回顾：已更新 {MEMORY_FILE}")
        else:
            print("[记忆] 任务结束回顾：无需更新")


# ============================================================
# Prompt caching：system 参数构建
# ============================================================

def build_system_param(system_prompt: str):
    """
    构建 messages.create 的 system 参数。
    - USE_CACHE_CONTROL=True：返回 blocks 形式，block 带 cache_control
    - USE_CACHE_CONTROL=False：返回纯字符串（兼容不支持 caching 的网关）
    """
    if not USE_CACHE_CONTROL:
        return system_prompt
    return [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]


# ============================================================
# 动态压缩：compact_messages 及其辅助
# ============================================================

def _extract_text(content) -> str:
    """
    从 message content（str 或 block list）提取纯文本，便于估算/摘要。

    支持三种 content 形态：
        - str                      → 直接返回
        - list of dict             → demo2 自己拼的 messages（tool_result 也是 dict）
        - list of SDK block 对象   → 沿用的 response.content（assistant 回复）

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


def compact_messages(messages: list, client, model: str, verbose: bool = False, force: bool = False) -> list:
    """
    动态压缩 messages：保留最近 N 条，老的让 LLM 摘要成一段。

    client / model 由调用方传入（memory 层不持有全局 client）。
    force=True 跳过阈值检查——供 /compact 命令手动触发（自动触发走阈值）。
    返回新的 messages list（不修改原 list）。摘要失败时静默回退到原 messages。
    """
    if len(messages) < COMPACT_THRESHOLD_MESSAGES and not force:
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

    compact_system_prompt = """你是上下文压缩助手。把下面的 Agent 对话历史压缩成一份结构化摘要，供后续工作无缝接续。

要求按三段输出：
1. 用户请求与意图：用户要做什么、有哪些约束（用户原话逐字保留，不要转述）
2. 关键结果与决策：完成了什么、重要的文件路径/数字/结论、踩过并修复的坑
3. 当前进度与下一步：压缩前正在做什么、紧接着的下一步是什么

丢弃：重复的试错过程、冗长的工具原始输出。不要加任何前缀说明，直接输出摘要。"""

    try:
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=compact_system_prompt,
            messages=[{"role": "user", "content": f"对话历史：\n\n{transcript}\n\n请输出压缩摘要："}],
        )
        summary = "".join(b.text for b in response.content if b.type == "text")

        if verbose:
            preview = summary.replace("\n", " ")[:200]
            print(f"[compact] 摘要: {preview}...")

        # 把摘要注入成 [历史对话摘要] 标记消息，让后续 LLM 知道这是压缩过的上下文
        summary_msg = {
            "role": "user",
            "content": (
                f"[历史对话已压缩，摘要如下]\n{summary}\n"
                "请基于摘要继续工作，不要向用户复述摘要。"
            ),
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
