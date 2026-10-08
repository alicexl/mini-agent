#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo3 - 工具扩展轴的 Agent

公式：demo3 = base × 工具扩展

    × MCP 协议（mcp.py：JSON-RPC 2.0 over HTTP，跨进程工具，可跨语言跨机器）
    × 继承 demo2 全部记忆能力（会话持久化 / MEMORY.md 模型自主维护 / caching）
    × 手动压缩（/compact；与 demo2 的差异：无自动压缩阈值）

七文件结构（demo2 六文件 + MCP 客户端层）：
    agent.py     主入口：客户端 + ReAct 主循环（本地/MCP 统一分发）+ REPL
    tools.py     工具层（demo1 四件套，本地）
    mcp.py       MCP 客户端层：握手 / 发现 / 调用，Server 不可用时降级仅本地
    render.py    渲染层（demo2 版：分色 + cache 统计 + user 回放）
    memory.py    记忆层（demo2 版减自动压缩：MEMORY.md / 手动 compact / caching）
    session.py   会话层：memory/<会话ID>.jsonl 持久化 + /resume
    commands.py  命令层：/help /status /tools /memory /resume /new /compact /quit

启动顺序：
    1. 先启动 MCP Server：  python mcp_server.py
    2. 再启动 Agent：       python -X utf8 agent.py
"""

import os

from anthropic import Anthropic

from tools import TOOLS, AVAILABLE_FUNCTIONS
import mcp
from memory import (
    MEMORY_FILE, MEMORY_WINDOW_LINES, USE_CACHE_CONTROL,
    build_system_prompt, build_system_param, review_memory,
)
import session
from render import (
    print_banner, print_cache_stats, print_divider, print_error,
    print_markdown, print_messages, print_stop_reason, print_step,
    read_user_input,
)
from commands import SessionState, handle_command


# ============================================================
# Part 1: 配置 + LLM 客户端初始化（同 demo2）
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


# 模块级占位：实际使用前由 __main__ 初始化
client: Anthropic = None  # type: ignore
ALL_TOOLS: list = []      # 本地 + MCP 合并后的工具 schema（main 启动时赋值）
MCP_CLIENT = None         # MCP 客户端实例（main 启动时赋值）


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
# Part 2: 统一工具分发 + Agent 主循环
# ============================================================
# 与 demo2 的核心区别：
#   - 工具集从 4 个本地扩展到 4 本地 + N MCP（N 由 server 决定）
#   - 工具调用统一分发：本地工具名走函数调用，其他走 MCP RPC——
#     LLM 视角下本地 / MCP 无差异（schema 两端一致，直接 + 拼接合并）
#   - 无自动压缩（demo3 刻意去掉阈值检查，压缩只由 /compact 手动发起）

MAX_ITERATIONS = 30  # 防止大模型陷入死循环


def _dispatch_tool(name: str, args: dict) -> str:
    """统一工具分发：本地 or MCP。LLM 不需要知道工具在哪，只按名字调用。"""
    if name in AVAILABLE_FUNCTIONS:
        try:
            return str(AVAILABLE_FUNCTIONS[name](**args))
        except Exception as e:
            return f"[错误] 本地工具 {name} 执行失败: {e}"

    # 不在本地 → 走 MCP
    try:
        return MCP_CLIENT.call_tool(name, args)
    except Exception as e:
        return f"[错误] MCP 工具 {name} 调用失败: {e}"


def run_agent(user_input: str, history: list, verbose: bool = True):
    """
    在会话历史上跑一轮 ReAct（history 就地追加；工具统一走本地/MCP 分发）。

    Returns:
        (最终回复, 本轮新增消息列表)
    """
    # 1. 构建 system prompt（含项目级记忆）+ 转 cache_control blocks
    system_prompt = build_system_prompt(verbose=verbose)
    system_param = build_system_param(system_prompt)

    # 2. ReAct 循环（在会话历史上追加）
    new_messages = []

    user_msg = {"role": "user", "content": user_input}
    history.append(user_msg)
    new_messages.append(user_msg)

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print_divider(f"第 {loop_idx} 轮 ReAct")
            print_messages(history)

        # 2a. 决策：调 LLM（system 走 cache_control，工具集 = 本地 + MCP）
        create_kwargs = {
            "model": MODEL,
            # 思考预算计入 max_tokens，开启 thinking 时须抬高输出上限
            "max_tokens": 8000 if USE_THINKING else 4096,
            "system": system_param,
            "tools": ALL_TOOLS,
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

        # 2b. 判停（最终回复也落进会话历史）
        if response.stop_reason != "tool_use":
            if verbose:
                print_divider("任务完成")
            result = "".join(b.text for b in response.content if b.type == "text")
            final_msg = {"role": "assistant", "content": response.content}
            history.append(final_msg)
            new_messages.append(final_msg)
            break

        # 2c. 行动 + 感知（统一分发：本地函数 or MCP RPC）
        assistant_msg = {"role": "assistant", "content": response.content}
        history.append(assistant_msg)

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = _dispatch_tool(block.name, block.input or {})
            if verbose:
                print_step("tool_return", result, limit=200)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        results_msg = {"role": "user", "content": tool_results}
        history.append(results_msg)
        new_messages.extend([assistant_msg, results_msg])
    else:
        result = "[错误] 超过最大循环次数"

    return result, new_messages


# ============================================================
# 交互式入口
# ============================================================

if __name__ == "__main__":
    init_client()

    # MCP 握手 + 发现工具（Server 不可用时降级为仅本地）
    MCP_CLIENT, mcp_tools = mcp.discover()

    # 合并工具（schema 统一，直接拼接）
    ALL_TOOLS = TOOLS + mcp_tools
    print(f"[Tools] 合并后共 {len(ALL_TOOLS)} 个工具：{', '.join(t['name'] for t in ALL_TOOLS)}")

    state = SessionState(
        model=MODEL,
        base_url=BASE_URL,
        client=client,
        session_id=session.new_session_id(),
        tools=ALL_TOOLS,
        extra_lines=[
            f"MCP:    {mcp.MCP_URL}",
            f"工具:   {len(ALL_TOOLS)} 个（本地 {len(TOOLS)} + MCP {len(mcp_tools)}）",
            f"项目记忆: {MEMORY_FILE}（窗口 {MEMORY_WINDOW_LINES} 行）",
            "压缩:   仅手动 /compact（无自动压缩）",
            f"缓存:   cache_control={'on' if USE_CACHE_CONTROL else 'off'}",
        ],
    )
    print_banner("Demo3 Agent 已启动（工具扩展轴）", [
        f"模型:   {MODEL}",
        f"网关:   {BASE_URL}",
        *state.extra_lines,
        "命令:   /help 查看命令，/tools 看工具，/quit 退出",
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
            session.append_messages(state.session_id, new_msgs)
            print_markdown(final)
            # 任务结束回顾一次记忆：提醒模型判断有没有值得持久的事实，不强制写
            review_memory(state.history, client, MODEL, verbose=True)
        except Exception as e:
            print_error(f"[错误] {e}")
