#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 多 Agent 层 — Subagent（多 Agent 轴的核心）

    subagent        委派一次性 Subagent 在后台完成独立子任务——独立 context（messages +
                    system prompt），完成后经 <task-notification> 通知把最终报告送回
                    主 Agent；工具集只给基础四件套（plan / use_skill / ask / job_kill /
                    subagent 全不给——防递归、不回头问用户）
    run_react_loop  主 Agent 与 Subagent 共用的 ReAct 循环——差别只在传入的
                    messages / system / tools（主/子同构，demo5 的关键设计）

多 Agent 轴的全部代码长在本文件：subagent 工具定义 + 实现 + 共用循环。
主循环（agent.py）也 import 这里的 run_react_loop 跑 depth=0 的顶层循环。

模块级 client / MODEL / ALL_TOOLS / FUNCTIONS 由 agent.py 启动时注入
（与 agent.py 的模块级 client 同款模式）：subagent() 需要全量工具做裁剪。
"""

import os

from render import print_divider, print_step, print_stop_reason
import jobs


MAX_ITERATIONS = 10  # 单个 ReAct 循环的最大轮数
_MAIN_ROUND = 0      # 主 Agent 会话内连续轮数（跨 turn 累计，通知汇报轮接着数）

# 思考模式开关（默认关闭）：开启后每轮先推理再决策，✻ thinking 随回复展示
USE_THINKING           = False
THINKING_BUDGET_TOKENS = 2000   # 思考预算（计入 max_tokens，开启时输出上限抬到 8000）

# 模块级依赖——agent.py 启动时注入
client = None       # type: ignore
MODEL = ""
ALL_TOOLS: list = []
FUNCTIONS: dict = {}


# ============================================================
# subagent 工具：定义 + 实现
# ============================================================

SUBAGENT_TOOL = {
    "name": "subagent",
    "description": (
        "委派一个独立的 Subagent 在后台完成子任务。Subagent 有独立的 context（messages），"
        "完成后你会收到 <task-notification> 通知（附最终报告），不要原地等待。\n\n"
        "**使用时机**：相互独立的子任务——每个子任务派一个 Subagent。"
        "链式任务（后一步依赖前一步结果）不要用，Subagent 之间无法传递结果。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "Subagent 的角色，例如「Python 工程师」/「文件统计员」"},
            "task": {"type": "string", "description": "交给 Subagent 完成的具体任务描述"},
        },
        "required": ["role", "task"],
    },
}


def subagent(role: str, task: str) -> str:
    """
    委派独立 Subagent 在后台完成子任务（demo5 新增）。

    子循环跑在线程里（agent job）：独立 messages、独立 system prompt、
    工具集只给基础四件套（plan / use_skill / ask / job_kill / subagent 全不给——
    防递归、独立干活不回头找用户拿主意）。完成后通知机制把最终报告送回主 Agent。
    """
    # ① 工具集裁剪：Subagent 只有基础四件套
    tools = [t for t in ALL_TOOLS
             if t["name"] in ("execute_bash", "read_file", "write_file", "edit")]

    def _run(job, log_file) -> str:
        # ② 独立 messages：全新一份，只装任务描述
        sub_messages = [{"role": "user", "content": task}]
        # ③ 独立 system prompt：角色化 f-string，只拼角色
        sub_system_prompt = (
            f"你是一个被委派来的 Subagent。你的角色是：**{role}**。\n"
            f"请专注于交给你完成的任务，做完后用一两句话汇报结果。"
        )
        return run_react_loop(
            messages=sub_messages,
            tools=tools,
            local_fns=FUNCTIONS,
            system=sub_system_prompt,   # 纯字符串（网关兼容）；主循环才走 cache_control blocks
            depth=1,
            verbose=False,              # 子循环不打印终端——细节全写日志，终端只留主 Agent
            log_file=log_file,
            stop_event=job._stop_event,
        )

    # ④ 后台执行：立即返回，不阻塞主 Agent；完成后通知机制送回最终报告
    job = jobs.REGISTRY.spawn_agent(description=f"{role}：{task[:50]}", run_fn=_run)
    return (f"[Subagent · {role}] 已放入后台运行，job id: {job.id}\n"
            f"输出日志：{job.log_path}（随时可用 read_file 查看）\n"
            "完成后你会收到 <task-notification> 通知（附最终报告），不要原地等待，也不要反复读日志轮询。")


# ============================================================
# 共用 ReAct 循环：主 Agent 与 Subagent 同构
# ============================================================

def run_react_loop(
    messages: list,
    tools: list,
    local_fns: dict,
    system,
    depth: int,
    verbose: bool,
    log_file=None,
    stop_event=None,
) -> str:
    """
    通用 ReAct 循环——主 Agent（depth=0）和 Subagent（depth=1+）共用。

    Args:
        messages:      该循环专属的 messages（主 / 子各自独立）
        tools:         本循环 LLM 能看到的工具列表
        local_fns:     本循环可调用的本地函数字典
        system:        本循环的 system 参数（主循环传 cache_control blocks，子循环传纯字符串）
        depth:         当前的嵌套深度（主 Agent = 0，Subagent = 1+）
        verbose:       是否打印轨迹到终端（子循环 False——细节只写日志）
        log_file:      子循环传入的日志文件——关键步骤始终落盘（agent job 留档可查）
        stop_event:    子循环传入的停止标志——job_kill 协作式终止（每轮检查）
    """
    def _log(text: str) -> None:
        if log_file is not None:
            log_file.write(text + "\n")
            log_file.flush()

    turn_round = 0
    while True:
        turn_round += 1
        if turn_round > MAX_ITERATIONS:
            return f"[错误] ReAct 循环未在 {MAX_ITERATIONS} 轮内完成"
        if depth == 0:
            global _MAIN_ROUND
            _MAIN_ROUND += 1
            round_no = _MAIN_ROUND   # 主 Agent：会话内连续计数（通知汇报轮接着数）
        else:
            round_no = turn_round    # Subagent：各自从 1 数

        if stop_event is not None and stop_event.is_set():
            return "[已终止]"

        if verbose:
            depth_label = f"（depth={depth}）" if depth > 0 else ""
            print_divider(f"第 {round_no} 轮 ReAct{depth_label}")

        create_kwargs = {
            "model": MODEL,
            # 思考预算计入 max_tokens，开启 thinking 时须抬高输出上限
            "max_tokens": 8000 if USE_THINKING else 4096,
            "system": system,
            "tools": tools,
            "messages": messages,
        }
        if USE_THINKING:
            create_kwargs["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}
        response = client.messages.create(**create_kwargs)

        if verbose:
            print_stop_reason(response.stop_reason)

        # 判停（最终回复也落进 messages——主循环落会话历史，子循环随局部变量销毁）
        if response.stop_reason != "tool_use":
            result = "".join(b.text for b in response.content if b.type == "text")
            messages.append({"role": "assistant", "content": response.content})
            if verbose:
                print_step("assistant", result, limit=120)
            _log(f"assistant: {result}")
            return result

        # 日志始终写（子循环 verbose=False 也要留档）；终端打印看 verbose
        for block in response.content:
            if block.type == "tool_use":
                _log(f"tool_call: {block.name}({block.input})")

        if verbose:
            for block in response.content:
                if block.type == "thinking":
                    print_step("thinking", block.thinking, limit=80)
                elif block.type == "text" and block.text.strip():
                    print_step("assistant", block.text, limit=200)
                elif block.type == "tool_use":
                    print_step("tool_call", f"{block.name}({block.input})")

        messages.append({"role": "assistant", "content": response.content})

        # 遍历 tool_use 块，走路由表分发
        tool_results = []
        plan_just_called = False
        for block in response.content:
            if block.type != "tool_use":
                continue
            name = block.name
            args = block.input or {}
            fn = local_fns.get(name)
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
            _log(f"tool_return: {result[:200]}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })
        messages.append({"role": "user", "content": tool_results})

        # plan 调用一次后移除（继承 demo4 底座）——主/子循环一致生效：
        # LLM 列完步骤就放手，不反复管理进度
        if plan_just_called:
            tools = [t for t in tools if t["name"] != "plan"]

    return f"[错误] ReAct 循环未在 {MAX_ITERATIONS} 轮内完成"
