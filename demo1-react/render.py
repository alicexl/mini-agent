#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo1 渲染层 — 终端 UI，所有输出收敛于此

从 agent_single.py 散落的 print 调用拆出。两个库各管一头：
    rich            输出——分色 step（✻ thinking / ● assistant / ⏺ tool_call / ✔ tool_return）、
                    Rule 分隔线、模型回复 Markdown 整块渲染
    prompt_toolkit  输入——方向键编辑 + ↑↓ 翻历史（替代 input() 的转义序列乱码问题）

非 TTY 降级：printf 管道喂任务（CI / 自动验证）时 prompt_toolkit 无法工作
（它需要真终端），read_user_input 自动退回内置 input()——功能不缺，只是没有编辑手感。
"""

import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule

# 全仓共享一个 Console（rich 的输出宽度 / 编码探测都挂在它身上）
console = Console()

# step 前缀图标 + 配色（thinking 仅在 USE_THINKING=True 时出现）
_STYLES = {
    "thinking":    ("✻", "dim"),
    "assistant":   ("●", "green"),
    "tool_call":   ("⏺", "yellow"),
    "tool_return": ("✔", "cyan"),
}


def _preview(text: str, limit: int = 0) -> str:
    """压成单行预览；limit>0 时截断加省略号，limit=0 不截断"""
    text = str(text).replace("\n", " ").strip()
    if limit and len(text) > limit:
        return text[:limit] + "..."
    return text


def print_step(kind: str, text: str, limit: int = 0) -> None:
    """打印一行 ReAct 步骤：图标 + 着色类型标签 + 内容"""
    icon, style = _STYLES[kind]
    console.print(f"[{style}]{icon} {kind:<11}[/] | {escape(_preview(text, limit))}")


def print_divider(title: str = "") -> None:
    """自适应宽度的横线分隔（可带标题）"""
    console.print(Rule(title, style="grey50"))


def print_banner(title: str, lines: list) -> None:
    """启动横幅：面板 + 若干信息行"""
    body = f"[bold]{title}[/]\n" + "\n".join(lines)
    console.print(Panel(body, border_style="grey50"))


def print_markdown(text: str) -> None:
    """模型最终回复整块按 Markdown 渲染（列表圆点 / 代码块高亮）"""
    console.print(Markdown(text))


def print_error(text: str) -> None:
    console.print(f"[red]{escape(text)}[/]")


def print_stop_reason(stop_reason: str) -> None:
    console.print(f"[grey50]stop_reason = {stop_reason}[/]")


def print_messages(messages: list) -> None:
    """messages 调试预览（dim）——教学用：看清上下文如何一轮轮增长"""
    console.print(f"[dim]\\[messages] 当前 {len(messages)} 条消息[/]")
    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[调用工具 {block.get('name')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(str(block.get("content", ""))[:100])
                else:
                    t = getattr(block, "type", None)
                    if t == "text":
                        parts.append(getattr(block, "text", ""))
                    elif t == "tool_use":
                        parts.append(f"[调用工具 {getattr(block, 'name', '')}]")
            content = "\n".join(parts)
        console.print(f"[dim]  [{i}] {msg.get('role', '?'):<9}: {escape(_preview(content, 60))}[/]")


# ============================================================
# 输入：prompt_toolkit（真终端）/ input()（管道降级）
# ============================================================

# PromptSession 模块级单例——只有它会记住本次运行的输入历史（↑↓ 翻）
# 仅在真终端且装了 prompt_toolkit 时创建；管道 / 缺库时为 None，走 input() 降级
if sys.stdin.isatty() and sys.stdout.isatty():
    try:
        from prompt_toolkit import PromptSession
        _prompt_session = PromptSession()
    except ImportError:
        _prompt_session = None
else:
    _prompt_session = None


def read_user_input() -> "str | None":
    """
    读一行用户输入，上下各补一条横线让输入在滚动历史里保持边界。
    返回 None 表示退出（Ctrl-C / Ctrl-D）。
    """
    print_divider()
    try:
        if _prompt_session is not None:
            user_input = _prompt_session.prompt("❯ ").strip()
        else:
            user_input = input("❯ ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    print_divider()
    return user_input
