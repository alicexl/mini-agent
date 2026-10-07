#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo1 命令层 — 斜杠命令系统（/help /status /exit）

主循环拿到用户输入后，先看第一个字符是不是 /：斜杠命令由程序自己解释执行，
其余输入才发给 Agent。命令用注册表挂载——加命令 = 往 COMMANDS 加一项，
主循环一行不用动；/help 遍历注册表自动生成，新命令自动出现。
"""

from dataclasses import dataclass, field
from typing import Callable

from render import console
from tools import TOOLS


@dataclass
class Command:
    name: str
    description: str
    handler: Callable  # handler(state) -> bool：返回 False 表示退出程序


@dataclass
class SessionState:
    """跨轮次的会话状态——命令读写它"""
    model: str
    base_url: str
    history: list = field(default_factory=list)  # 会话历史（跨任务持续增长，重启即丢）
    extra_lines: list = field(default_factory=list)  # 各 demo 自定的附加状态行


def _cmd_help(state: SessionState) -> bool:
    console.print("\n[dim]--- 可用命令 ---[/]")
    for cmd in COMMANDS.values():
        console.print(f"[cyan]/{cmd.name:<8}[/] {cmd.description}")
    console.print()
    return True


def _cmd_status(state: SessionState) -> bool:
    lines = [
        f"模型:   {state.model}",
        f"网关:   {state.base_url}",
        f"工具数: {len(TOOLS)}（{', '.join(t['name'] for t in TOOLS)}）",
        f"消息:   {len(state.history)} 条（会话内持续增长）",
        *state.extra_lines,
    ]
    console.print("\n" + "\n".join(f"[dim]{l}[/]" for l in lines) + "\n")
    return True


def _cmd_exit(state: SessionState) -> bool:
    console.print("再见！")
    return False


COMMANDS = {
    "help":   Command("help", "显示可用命令", _cmd_help),
    "status": Command("status", "显示会话状态", _cmd_status),
    "exit":   Command("exit", "退出程序", _cmd_exit),
    "quit":   Command("quit", "退出程序（/exit 同）", _cmd_exit),
}


def handle_command(user_input: str, state: SessionState) -> str:
    """
    处理以 / 开头的命令。

    返回 'pass'：不是命令，主循环继续交给 Agent
    返回 'continue'：命令已处理，跳到下一轮输入
    返回 'break'：命令要求退出
    """
    if not user_input.startswith("/"):
        return "pass"

    cmd_name = user_input[1:].split()[0]
    command = COMMANDS.get(cmd_name)
    if command is None:
        console.print(f"[yellow]未知命令：/{cmd_name}，输入 /help 查看可用命令[/]\n")
        return "continue"

    return "continue" if command.handler(state) else "break"
