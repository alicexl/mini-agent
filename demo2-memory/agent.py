#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo2 - 带记忆的 Agent（记忆轴）

公式：demo2 = base × 记忆

    × 跨会话记忆（memory/MEMORY.md，模型策展——对齐 Claude Code）
    × 会话持久化（memory/<会话ID>.jsonl，/resume 恢复）
    × 动态压缩（compact_messages，老消息滚动摘要）
    × Prompt caching（cache_control breakpoint，减少重复传输）

六文件结构（在 demo1 三文件基础上，记忆轴独立成层 + 会话层 + 命令层）：
    agent.py     主入口：客户端 + ReAct 主循环（compact 触发 + cache 统计）+ REPL
    tools.py     工具层（同 demo1）
    render.py    渲染层（demo1 版 + print_cache_stats）
    memory.py    记忆层：项目级记忆 + compact + caching（记忆轴全部机制）
    session.py   会话层：memory/<会话ID>.jsonl 会话持久化 + /resume
    commands.py  命令层：/help /status /memory /resume /new /exit

用法：
    python -X utf8 agent.py
"""

import os

from anthropic import Anthropic

from tools import TOOLS, AVAILABLE_FUNCTIONS
from memory import (
    MEMORY_FILE, MEMORY_WINDOW_LINES, COMPACT_THRESHOLD_MESSAGES,
    COMPACT_KEEP_RECENT, USE_CACHE_CONTROL,
    build_system_prompt, build_system_param, compact_messages,
)
import session
from render import (
    print_banner, print_cache_stats, print_divider, print_error,
    print_markdown, print_messages, print_stop_reason, print_step,
    read_user_input,
)
from commands import SessionState, handle_command


# ============================================================
# Part 1: 配置 + LLM 客户端初始化（同 demo1 新 base）
# ============================================================
# 网关、模型、超时均写死。API Key 两种获取方式（按优先级）：
#   1. 环境变量 ANTHROPIC_API_KEY（优先级最高，可持久化）
#   2. 未设环境变量 → 运行时交互式提示输入（仅本次有效，不持久化）
# 默认走智谱 BigModel 的 Anthropic 兼容网关 + glm-5.2 模型。

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL          = "glm-5.2"                                  # 模型名
API_TIMEOUT_MS = 3000000                                    # 单次请求超时（毫秒），3000000ms = 50 分钟

# 思考模式开关（默认关闭）：开启后模型每轮先推理再决策，✻ thinking 随回复展示
USE_THINKING           = False
THINKING_BUDGET_TOKENS = 2000   # 思考预算（计入 max_tokens，开启时输出上限抬到 8000）


def load_config() -> dict:
    """API Key 只从环境变量读（不存在代码内常量）"""
    return {
        "api_key":       os.environ.get("ANTHROPIC_API_KEY") or "",
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
    print("如需持久化：请设置环境变量 ANTHROPIC_API_KEY")
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
# Part 2: Agent 主循环（ReAct + compact + caching）
# ============================================================
# 与 demo1 的核心区别：
#   - system prompt = 基础 + MEMORY.md 内容 + 维护指引，走 cache_control
#   - 每轮 ReAct 前检查是否需要 compact_messages
#   - 跨会话记忆由模型在对话中用 write_file / edit 自主维护（无自动落盘）
#   - 会话历史落盘 memory/<会话ID>.jsonl，支持 /resume
#   - 每轮打印 cache 命中统计（创建 vs 命中）

MAX_ITERATIONS = 30  # 防止大模型陷入死循环


def run_agent(user_input: str, history: list, verbose: bool = True):
    """
    在活会话历史上跑一轮 ReAct（history 就地追加/压缩）。

    流程：
        1. 加载项目级记忆（MEMORY.md）→ 构建 system prompt（含维护指引）
        2. 在 history 上进入 ReAct 循环：
           a. 检查是否触发 compact_messages
           b. 调 LLM（system 走 cache_control）
           c. 判停 / 行动 / 感知（同 demo1）
        3. 跨会话记忆由模型在对话中用 write_file / edit 自主维护（无自动落盘）

    Returns:
        (最终回复, 本轮新增消息列表)
        compact 触发过则新增列表为 None——历史被改写，调用方应全量重写会话文件
    """
    # 1. 构建 system prompt（含项目级记忆）+ 转 cache_control blocks
    system_prompt = build_system_prompt(verbose=verbose)
    system_param = build_system_param(system_prompt)

    # 2. ReAct 循环（在活历史上追加）
    new_messages = []
    compacted = False

    user_msg = {"role": "user", "content": user_input}
    history.append(user_msg)
    new_messages.append(user_msg)

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print_divider(f"第 {loop_idx} 轮 ReAct")
            print_messages(history)

        # 2a. 上下文管理：检查是否需要 compact（改写整个历史，增量作废）
        if len(history) >= COMPACT_THRESHOLD_MESSAGES:
            history[:] = compact_messages(list(history), client, MODEL, verbose=verbose)
            compacted = True

        # 2b. 决策：调 LLM（system 走 cache_control）
        create_kwargs = {
            "model": MODEL,
            # 思考预算计入 max_tokens，开启 thinking 时须抬高输出上限
            "max_tokens": 8000 if USE_THINKING else 4096,
            "system": system_param,
            "tools": TOOLS,
            "messages": history,
        }
        if USE_THINKING:
            create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
        response = client.messages.create(**create_kwargs)

        if verbose:
            print_stop_reason(response.stop_reason)
            for block in response.content:
                if block.type == "thinking":
                    print_step("thinking", block.thinking, limit=80)
                elif block.type == "text":
                    print_step("assistant", block.text, limit=80)
                elif block.type == "tool_use":
                    print_step("tool_call", f"{block.name}({block.input})")
            print_cache_stats(response.usage, use_cache_control=True)

        # 2c. 判停（最终回复也落进会话历史——恢复会话时能看到上次「答了什么」）
        if response.stop_reason != "tool_use":
            if verbose:
                print_divider("任务完成")
            result = "".join(b.text for b in response.content if b.type == "text")
            final_msg = {"role": "assistant", "content": response.content}
            history.append(final_msg)
            if not compacted:
                new_messages.append(final_msg)
            break

        # 2d. 行动 + 感知
        assistant_msg = {"role": "assistant", "content": response.content}
        history.append(assistant_msg)

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            name = block.name
            args = block.input or {}
            fn = AVAILABLE_FUNCTIONS.get(name)
            if fn is None:
                result = f"[错误] 未知工具: {name}"
            else:
                try:
                    result = str(fn(**args))
                except Exception as e:
                    result = f"[错误] 工具 {name} 执行失败: {e}"

            if verbose:
                print_step("tool_return", result, limit=200)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        results_msg = {"role": "user", "content": tool_results}
        history.append(results_msg)
        if not compacted:
            new_messages.extend([assistant_msg, results_msg])
    else:
        result = "[错误] 超过最大循环次数"

    return result, (None if compacted else new_messages)


# ============================================================
# 交互式入口
# ============================================================

if __name__ == "__main__":
    init_client()

    state = SessionState(
        model=MODEL,
        base_url=BASE_URL,
        session_id=session.new_session_id(),
        extra_lines=[
            f"项目记忆: {MEMORY_FILE}（窗口 {MEMORY_WINDOW_LINES} 行）",
            f"压缩:   compact（阈值 {COMPACT_THRESHOLD_MESSAGES} 条 / 保留最近 {COMPACT_KEEP_RECENT} 条）",
            f"缓存:   cache_control={'on' if USE_CACHE_CONTROL else 'off'}",
        ],
    )
    print_banner("Demo2 Agent 已启动（记忆轴）", [
        f"模型:   {MODEL}",
        f"网关:   {BASE_URL}",
        f"会话:   {state.session_id}",
        *state.extra_lines,
        "命令:   /help 查看命令，/resume 恢复历史会话，/quit 退出",
    ])

    while True:
        user_input = read_user_input()
        if user_input is None:
            print("再见！")
            break

        if not user_input:
            continue

        action = handle_command(user_input, state)
        if action == "break":
            break
        if action == "continue":
            continue

        try:
            final, new_msgs = run_agent(user_input, state.history, verbose=True)
            # 会话级落盘：增量追加；compact 改写过历史则全量重写
            if new_msgs is None:
                session.rewrite_session(state.session_id, state.history)
                print(f"[会话] compact 改写历史，已全量重写 {session.session_file(state.session_id)}")
            else:
                session.append_messages(state.session_id, new_msgs)
            print_markdown(final)
        except Exception as e:
            print_error(f"[错误] {e}")
