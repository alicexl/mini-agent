#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4 - 规划轴的 Agent

公式：demo4 = base × 规划

    × plan（plan.py：当前任务的规划——LLM 自判复杂度，复杂任务开头列一次步骤）
    × Skill（skill.py：skills/*.md 可复用工作流，元信息常驻 + 正文按需加载）
    × ask_user_question（ask.py：需求有歧义时主动向用户提问，对齐 Claude Code）
    × 继承 demo2 全部记忆能力（会话持久化 / MEMORY.md / caching）
    × 手动压缩（/compact；同 demo3：无自动压缩阈值）

九文件结构（demo2 六文件 + 规划层 + Skill 层 + 提问层）：
    agent.py     主入口：客户端 + ReAct 主循环（工具合并 + plan 一致性）+ REPL
    tools.py     工具层（demo1 四件套，本地）
    plan.py      规划层：plan 工具（demo4 新增）
    skill.py     Skill 层：Skill 加载器 + use_skill（demo4 新增）
    ask.py       提问层：ask_user_question 方向键 UI + 非 TTY 降级（demo4 新增）
    render.py    渲染层（demo2 版：分色 + cache 统计 + user 回放）
    memory.py    记忆层（demo2 版减自动压缩）
    session.py   会话层：memory/<会话ID>.jsonl 持久化 + /resume
    commands.py  命令层：/help /status /tools /skills /memory /resume /new /compact /quit

用法：
    python -X utf8 agent.py
"""

import os

from anthropic import Anthropic

from tools import TOOLS as BASE_TOOLS, AVAILABLE_FUNCTIONS as BASE_FUNCTIONS
import plan
import skill
import ask
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
# Part 1: 配置 + LLM 客户端初始化（同 demo2/demo3）
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
# Part 2: Agent 主循环（ReAct + plan 一致性）
# ============================================================
# 与 demo3 的核心区别：
#   - 工具集 = 本地四件套 + plan/use_skill/ask_user_question（规划轴三件）
#   - system prompt = 记忆层产出 + Skills 元信息段 + 提问指引
#   - plan 一致性：复杂任务 LLM 先列步骤（调一次后从 tools 移除，列完就放手）；
#     探索/提问澄清靠 plan 工具描述的 prompt 引导（不裁写工具，教学最简版）；
#     ask_user_question 常驻（任务中途拿不准也能问）——与 plan 的一次性形成对照

MAX_ITERATIONS = 30  # 防止大模型陷入死循环


def build_system_prompt_full(verbose: bool = False) -> str:
    """
    组装完整 system prompt（四层叠加）：
        1. 基础（身份 + 工具 + 工作流程）        —— memory.py BASE_PROMPT
        2. 跨会话记忆（MEMORY.md 内容 + 维护指引）—— memory.py
        3. 可用 Skills 元信息（不含 body）        —— skill.py（渐进式披露）
        4. 提问指引（什么时候该问用户）           —— ask.py
    """
    sp = build_system_prompt(verbose=verbose)
    sp += skill.build_skill_metadata_section()
    sp += ask.ASK_GUIDANCE
    return sp


def run_agent(user_input: str, history: list, verbose: bool = True):
    """
    在会话历史上跑一轮 ReAct（history 就地追加；规划轴工具参与分发）。

    Returns:
        (最终回复, 本轮新增消息列表)
    """
    # 1. 构建 system prompt（四层叠加）+ 转 cache_control blocks
    system_prompt = build_system_prompt_full(verbose=verbose)
    system_param = build_system_param(system_prompt)

    # 2. ReAct 循环（在会话历史上追加；tools 为可变副本——plan 调用一次后移除）
    new_messages = []
    tools = list(ALL_TOOLS)

    user_msg = {"role": "user", "content": user_input}
    history.append(user_msg)
    new_messages.append(user_msg)

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print_divider(f"第 {loop_idx} 轮 ReAct")
            print_messages(history)

        # 2a. 决策：调 LLM（system 走 cache_control）
        create_kwargs = {
            "model": MODEL,
            # 思考预算计入 max_tokens，开启 thinking 时须抬高输出上限
            "max_tokens": 8000 if USE_THINKING else 4096,
            "system": system_param,
            "tools": tools,
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

        # 2c. 行动 + 感知（统一分发）
        assistant_msg = {"role": "assistant", "content": response.content}
        history.append(assistant_msg)

        tool_results = []
        plan_just_called = False
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
            if name == "plan":
                plan_just_called = True

            if verbose:
                print_step("tool_return", result, limit=200)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        # plan 调用一次后移除——LLM 列完步骤就放手，不反复管理进度
        if plan_just_called:
            tools = [t for t in tools if t["name"] != "plan"]

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

    # 工具合并：本地四件套 + 规划轴三件（schema 格式一致，直接拼接）
    ALL_TOOLS = BASE_TOOLS + plan.PLAN_TOOLS + skill.SKILL_TOOLS + [ask.ASK_TOOL]
    AVAILABLE_FUNCTIONS = {**BASE_FUNCTIONS, **plan.PLAN_FUNCTIONS, **skill.SKILL_FUNCTIONS,
                           "ask_user_question": ask.ask_user_question}

    # 启动时加载 Skills（skill.py 模块级 _SKILLS，use_skill 运行时读取）
    skill._SKILLS = skill.load_skills()
    if skill._SKILLS:
        print(f"[Skills] 加载 {len(skill._SKILLS)} 个：")
        for name, info in skill._SKILLS.items():
            print(f"  - {name}: {info['description'][:60]}")
            print(f"    触发词: {', '.join(info['triggers'])}")
    else:
        print(f"[Skills] 未在 {skill.SKILLS_DIR} 找到任何 .md 文件（Agent 仍可运行）")

    print(f"\n[Tools] 共 {len(ALL_TOOLS)} 个本地工具："
          f"{', '.join(t['name'] for t in ALL_TOOLS)}")

    state = SessionState(
        model=MODEL,
        base_url=BASE_URL,
        client=client,
        session_id=session.new_session_id(),
        tools=ALL_TOOLS,
        extra_lines=[
            f"工具:   {len(ALL_TOOLS)} 个（四件套 + plan/use_skill/ask_user_question）",
            f"Skills: {len(skill._SKILLS)} 个（元信息常驻，正文按需加载）",
            f"项目记忆: {MEMORY_FILE}（窗口 {MEMORY_WINDOW_LINES} 行）",
            "压缩:   仅手动 /compact（无自动压缩）",
            f"缓存:   cache_control={'on' if USE_CACHE_CONTROL else 'off'}",
        ],
    )
    print_banner("Demo4 Agent 已启动（规划轴）", [
        f"模型:   {MODEL}",
        f"网关:   {BASE_URL}",
        *state.extra_lines,
        "命令:   /help 查看命令，/skills 看 Skill，/quit 退出",
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
