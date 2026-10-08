#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4 命令层 — 斜杠命令系统（/help /status /tools /skills /memory /resume /new /compact /exit）

主循环拿到用户输入后，先看第一个字符是不是 /：斜杠命令由程序自己解释执行，
其余输入才发给 Agent。命令用注册表挂载——加命令 = 往 COMMANDS 加一项，
主循环一行不用动；/help 遍历注册表自动生成，新命令自动出现。
"""

from dataclasses import dataclass, field
from typing import Callable

import session
from render import console, print_step
from memory import (
    MEMORY_FILE, MEMORY_WINDOW_LINES,
    COMPACT_KEEP_RECENT, USE_CACHE_CONTROL, load_memory, _extract_text,
    compact_messages,
)


@dataclass
class Command:
    name: str
    description: str
    handler: Callable  # handler(state) -> bool：返回 False 表示退出程序


@dataclass
class SessionState:
    """跨轮次的会话状态——命令读写它，主循环往里计数"""
    model: str
    base_url: str
    client: object = None  # 主循环 init_client() 后注入（/compact 摘要要用）
    session_id: str = ""
    history: list = field(default_factory=list)  # 活会话消息（/resume 填回、/new 清空）
    tools: list = field(default_factory=list)  # 合并后的工具 schema（本地 + MCP，/tools 用）
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
        f"会话:   {state.session_id}",
        f"消息:   {len(state.history)} 条（/compact 可手动压缩）",
        *state.extra_lines,
    ]
    console.print("\n" + "\n".join(f"[dim]{l}[/]" for l in lines) + "\n")
    return True


def _cmd_tools(state: SessionState) -> bool:
    """查看当前完整工具清单。"""
    console.print(f"\n[dim]--- 当前 {len(state.tools)} 个工具 ---[/]")
    for t in state.tools:
        console.print(f"[cyan]{t['name']}[/]: {t.get('description', '')[:60]}")
    console.print()
    return True


def _cmd_skills(state: SessionState) -> bool:
    """查看已加载的 Skills。"""
    import plan
    if not plan._SKILLS:
        console.print(f"[dim](未在 {plan.SKILLS_DIR} 找到任何 .md 文件)[/]\n")
        return True
    console.print(f"\n[dim]--- 已加载 {len(plan._SKILLS)} 个 Skills ---[/]")
    for name, info in plan._SKILLS.items():
        console.print(f"[cyan]{name}[/]: {info['description']}")
        console.print(f"  [dim]触发词: {', '.join(info['triggers'])}[/]")
    console.print()
    return True


def _cmd_compact(state: SessionState) -> bool:
    """手动压缩会话历史（force 跳过阈值，立即压缩）——对应 Claude Code 的 /compact。"""
    if not state.history:
        console.print("[dim](会话还没有消息，无需压缩)[/]\n")
        return True
    if len(state.history) <= COMPACT_KEEP_RECENT:
        console.print(f"[dim]消息只有 {len(state.history)} 条，压无可压[/]\n")
        return True

    before = len(state.history)
    compacted = compact_messages(list(state.history), state.client, state.model,
                                 verbose=True)
    state.history[:] = compacted
    # compact 改写了历史——全量重写会话文件（与主循环的自动压缩同款处理）
    session.rewrite_session(state.session_id, state.history)
    console.print(f"[dim]压缩完成：{before} → {len(state.history)} 条，会话文件已重写[/]\n")
    return True


def _cmd_memory(state: SessionState) -> bool:
    content = load_memory()
    console.print(f"\n[dim]--- {MEMORY_FILE} 内容（窗口 {MEMORY_WINDOW_LINES} 行）---[/]")
    console.print(content or "[dim](空)[/]")
    console.print("[dim]--- end ---\n[/]")
    return True


def _cmd_new(state: SessionState) -> bool:
    """开新会话：清空活历史 + 换新会话 ID。旧会话文件原样留在磁盘等 /resume。"""
    state.history.clear()
    state.session_id = session.new_session_id()
    console.print(f"已开启新会话 [cyan]{state.session_id}[/]\n")
    return True


def _cmd_resume(state: SessionState) -> bool:
    """列出历史会话（编号选择，回车取消），选中即恢复并回放。"""
    sessions = session.list_sessions()
    if not sessions:
        console.print("[dim](memory/ 下还没有历史会话)[/]\n")
        return True

    console.print("\n[dim]--- 历史会话（新 → 旧）---[/]")
    for i, (sid, mtime, prompt) in enumerate(sessions, 1):
        console.print(f"[cyan]{i}[/]. {mtime:%m-%d %H:%M}  [dim]{sid}[/]  {prompt[:50]}")

    choice = input("输入编号恢复（回车取消）: ").strip()
    if not choice.isdigit() or not (1 <= int(choice) <= len(sessions)):
        console.print("[dim]已取消[/]\n")
        return True

    sid, _, _ = sessions[int(choice) - 1]
    state.history[:] = session.load_history(sid)
    state.session_id = sid  # 切回旧会话 ID——后续消息继续追加到原文件

    console.print(f"\n已恢复会话 [cyan]{sid}[/]，共 {len(state.history)} 条消息：\n")
    for msg in state.history:
        role = msg.get("role", "?")
        text = _extract_text(msg.get("content", ""))
        if role in ("user", "assistant"):
            print_step(role, text, limit=80)
    console.print()
    return True


def _cmd_exit(state: SessionState) -> bool:
    console.print("再见！")
    return False


COMMANDS = {
    "help":    Command("help", "显示可用命令", _cmd_help),
    "status":  Command("status", "显示会话状态", _cmd_status),
    "tools":   Command("tools", "查看工具清单", _cmd_tools),
    "skills":  Command("skills", "查看已加载 Skills", _cmd_skills),
    "memory":  Command("memory", "查看项目级记忆", _cmd_memory),
    "resume":  Command("resume", "恢复历史会话", _cmd_resume),
    "new":     Command("new", "开启新会话", _cmd_new),
    "compact": Command("compact", "手动压缩会话历史", _cmd_compact),
    "exit":    Command("exit", "退出程序", _cmd_exit),
    "quit":    Command("quit", "退出程序（/exit 同）", _cmd_exit),
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
