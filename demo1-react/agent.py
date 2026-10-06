#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo1 - 正式版（三文件结构）

演示 Agent 的底层原理 = LLM (大脑) + 工具 (手脚) + 循环 (ReAct)

    agent.py    主入口：客户端初始化 + ReAct 主循环 + 交互式 REPL（本文件）
    tools.py    工具层：工具 schema + 实现 + 路由表
    render.py   渲染层：rich 分色输出 + prompt_toolkit 输入

原始单文件版保留在 agent_single.py（教学起点——「一切始于单文件」）。
本文件由它拆分而来，功能完全一致；拆分动机：
    1. 工具是独立资产——加工具只动 tools.py，主循环不关心实现细节
    2. UI 收敛一处——换终端渲染方案（如调配色、换库）只动 render.py
    3. 主循环只剩 ReAct 骨架——读 agent.py 就是在读「循环」本身
"""

import os

from anthropic import Anthropic

from tools import TOOLS, AVAILABLE_FUNCTIONS
from render import (
    print_banner,
    print_divider,
    print_error,
    print_markdown,
    print_messages,
    print_stop_reason,
    print_step,
    read_user_input,
)


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================
# 网关、模型、超时均写死。API Key 两种获取方式（按优先级）：
#   1. 环境变量 ANTHROPIC_API_KEY（优先级最高，可持久化）
#   2. 未设环境变量 → 运行时交互式提示输入（仅本次有效，不持久化）
# 默认走智谱 BigModel 的 Anthropic 兼容网关 + glm-5.2 模型。

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL          = "glm-5.2"                                  # 模型名
API_TIMEOUT_MS = 3000000                                    # 单次请求超时（毫秒），3000000ms = 50 分钟


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
# Part 2: Agent 主循环（决策 / 行动 / 感知 = ReAct）
# ============================================================
# 每一轮：把整个 messages 重新发给大模型 → 大模型决策是否调用工具
#       → 调用就执行工具并把结果追加回 messages → 再发给大模型
#       → 直到 stop_reason != "tool_use"（任务完成）或达到 MAX_ITERATIONS。
# 与 agent_single.py 的差异只在输出：print 全部换成 render.py 的分色步骤。

MAX_ITERATIONS = 30  # 防止大模型陷入死循环


def run_agent(user_input: str, verbose: bool = True) -> str:
    """
    运行 Agent 处理一次用户任务。

    Args:
        user_input: 用户的任务目标
        verbose: 是否打印每一轮的决策与行动（教学演示建议开启）

    Returns:
        Agent 的最终文本回复
    """
    messages = [{"role": "user", "content": user_input}]
    system_prompt = "你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。"

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print_divider(f"第 {loop_idx} 轮 ReAct")
            print_messages(messages)

        # ---- 决策：大模型思考下一步 ----
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if verbose:
            print_stop_reason(response.stop_reason)
            for block in response.content:
                if block.type == "text":
                    print_step("assistant", block.text, limit=80)
                elif block.type == "tool_use":
                    print_step("tool_call", f"{block.name}({block.input})")

        # ---- 判断是否结束 ----
        if response.stop_reason != "tool_use":
            if verbose:
                print_divider("任务完成")
            return "".join(b.text for b in response.content if b.type == "text")

        # ---- 行动：本地执行工具 + 感知：收集结果 ----
        messages.append({"role": "assistant", "content": response.content})

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

        # 把工具结果作为 user 消息追加进 messages，下一轮大模型就能看到
        messages.append({"role": "user", "content": tool_results})

    return "[错误] 超过最大循环次数（{}），可能陷入死循环".format(MAX_ITERATIONS)


# ============================================================
# 交互式入口：真实 Agent 演示
# ============================================================
# 设置好环境变量 ANTHROPIC_API_KEY 后直接运行（未设则启动时交互式输入），
# 在终端输入任意任务（统计文件、查信息、写脚本……），观察每一轮 ReAct 循环。
# 输入 quit / exit / q 退出。

if __name__ == "__main__":
    init_client()

    print_banner("Demo1 Agent 已启动", [
        f"模型:   {MODEL}",
        f"网关:   {BASE_URL}",
        "输入 quit / exit 退出",
    ])

    while True:
        user_input = read_user_input()
        if user_input is None:
            print("再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("再见！")
            break

        try:
            final = run_agent(user_input, verbose=True)
            print_markdown(final)
        except Exception as e:
            print_error(f"[错误] {e}")
