#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 TUI 层 — Claude Code 同款行内渲染界面（demo5 新增）

    输出直通终端滚动区——滚轮 / 滚动条 / PgUp 由终端原生处理，任何终端都兼容，
    退出后历史仍留在滚动区；只有「输入行」做单行原位重绘。输出一帧帧沉进滚动区、
    输入永远在终端底部——观感上就是"输入框钉底"（Claude Code 正是这个策略，
    它不用交替屏幕）。

    结构：
      控制器线程：REPL 轮询逻辑（Agent 逻辑零改动，输出经 sys.stdout 重定向进队列）
      TUI 主线程：prompt_toolkit 行内 Application（输入行 + 可选 ask 弹层块），
                 后台任务把输出队列经 run_in_terminal 直写真终端
"""

import asyncio
import queue
import sys
import threading

from prompt_toolkit import Application
from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, VSplit, Window, Layout
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.output import create_output
from prompt_toolkit.styles import Style

_real_stdout = sys.stdout              # 真终端 stdout（输出直写这里）
log_queue: queue.Queue = queue.Queue()      # 输出日志（print/rich → 打印循环）
input_queue: queue.Queue = queue.Queue()    # 用户输入 → 控制器线程
_ask_queue: queue.Queue = queue.Queue()     # ask 问题（控制器 → TUI）
_answer_queue: queue.Queue = queue.Queue()  # ask 答案（TUI → 控制器）
_exit_flag = threading.Event()
_app_holder: dict = {}                 # {"app": Application}——跨线程退出用


class _QueueFile:
    """sys.stdout 的替身。两阶段：
    TUI 启动前（active=False）：直通真终端——API Key 提示、启动横幅正常显示
    TUI 启动后（active=True）：进日志队列——由 TUI 打印循环直写真终端"""
    active = False

    def write(self, text):
        if text:
            if self.active:
                log_queue.put(text)
            else:
                _real_stdout.write(text)
    def flush(self):
        if not self.active:
            _real_stdout.flush()
    def isatty(self):
        return False


def redirect_stdout():
    """agent.py 在任何 render import 之前调用——rich Console 创建时会捕获此时的
    sys.stdout，所有输出自动走本文件。"""
    sys.stdout = _QueueFile()


def request_exit():
    """控制器线程请求退出（Ctrl+C / EOF / /quit 后调用）"""
    _exit_flag.set()
    app = _app_holder.get("app")
    if app is not None:
        try:
            app.exit()
        except Exception:
            pass


def pick(questions) -> "list | None":
    """ask 的弹层钩子（控制器线程调用）：问题交给 TUI 弹层块，阻塞等答案"""
    _ask_queue.put(questions)
    return _answer_queue.get()


# ============================================================
# ask 弹层状态机（ask.py 独立版的方向键逻辑移植——渲染目标不同）
# ============================================================

class _PickerState:
    OTHER = {"label": "其他（输入自定义文本）", "description": ""}

    def __init__(self, questions):
        self.questions = questions
        self.qi = 0
        self.oi = 0
        self.answers = [None] * len(questions)
        self.picks = [set() for _ in questions]
        self.custom = False
        self.cancelled = False

    def opts(self):
        return self.questions[self.qi]["options"] + [self.OTHER]

    def advance(self):
        if self.qi >= len(self.questions) - 1:
            return "done"
        self.qi += 1
        self.oi = 0
        return "next"

    def finish(self) -> "list | None":
        if self.cancelled or any(a is None for a in self.answers):
            return None
        return self.answers


def _chips_fragments(p: _PickerState):
    frags = [("class:arrow", "← ")]
    for i, q in enumerate(p.questions):
        mark = "✔" if p.answers[i] is not None else ("❯" if i == p.qi else "")
        cls = "class:chip-done" if p.answers[i] is not None else (
            "class:chip-cur" if i == p.qi else "class:chip-todo")
        frags.append((cls, f" {mark} {q['header']} "))
    frags.append(("class:arrow", " Submit →\n"))
    return frags


def _picker_fragments(p: _PickerState):
    frags = list(_chips_fragments(p))
    q = p.questions[p.qi]
    multi = "（多选：Space 勾选）" if q["multi_select"] else ""
    frags.append(("class:question", f"\n{q['question']} {multi}\n"))
    for i, opt in enumerate(p.opts()):
        pointer = "❯ " if i == p.oi else "  "
        if q["multi_select"] and i < len(p.opts()) - 1:
            pointer = ("❯ ☑ " if i == p.oi else "  ☑ ") if i in p.picks[p.qi] \
                else ("❯ □ " if i == p.oi else "  □ ")
        cls = "class:opt-cur" if i == p.oi else "class:opt"
        frags.append((cls, f"{pointer}{opt['label']}\n"))
        if opt["description"]:
            frags.append(("class:dim", f"      {opt['description']}\n"))
    frags.append(("class:dim",
                  "\n↑↓ 选项 · Enter 选中/确认 · ←→ 切换题目 · Esc 取消"))
    if p.custom:
        frags.append(("class:question", "\n（其他）输入自定义文本后回车：\n"))
    return frags


# ============================================================
# TUI 构建
# ============================================================

def run(repl_fn, output=None):
    """主线程入口：起控制器线程跑 repl_fn，本线程跑行内 TUI。"""
    import ask
    ask.PICKER_HOOK = pick   # ask 弹层改走 TUI 内联块

    _QueueFile.active = True   # 从此刻起输出进队列，由打印循环直写真终端

    st = {"picker": None}
    input_buffer = Buffer(multiline=False, accept_handler=None)

    kb = KeyBindings()
    in_picker = Condition(lambda: st["picker"] is not None)
    in_custom = Condition(lambda: st["picker"] is not None and st["picker"].custom)

    @kb.add("c-c")
    def _(event):
        request_exit()

    # ---- 弹层键位（挂在输入 buffer 上：buffer 键位优先级最高）----
    buf_kb = KeyBindings()

    @buf_kb.add("up", filter=in_picker & ~in_custom)
    def _(event):
        st["picker"].oi = (st["picker"].oi - 1) % len(st["picker"].opts())

    @buf_kb.add("down", filter=in_picker & ~in_custom)
    def _(event):
        st["picker"].oi = (st["picker"].oi + 1) % len(st["picker"].opts())

    @buf_kb.add("left", filter=in_picker & ~in_custom)
    def _(event):
        st["picker"].qi = (st["picker"].qi - 1) % len(st["picker"].questions)
        st["picker"].oi = 0

    @buf_kb.add("right", filter=in_picker & ~in_custom)
    def _(event):
        st["picker"].qi = (st["picker"].qi + 1) % len(st["picker"].questions)
        st["picker"].oi = 0

    @buf_kb.add("space", filter=in_picker & ~in_custom)
    def _(event):
        p = st["picker"]
        q = p.questions[p.qi]
        if q["multi_select"] and p.oi < len(p.opts()) - 1:
            if p.oi in p.picks[p.qi]:
                p.picks[p.qi].discard(p.oi)
            else:
                p.picks[p.qi].add(p.oi)

    @buf_kb.add("escape", filter=in_picker)
    def _(event):
        p = st["picker"]
        if p.custom:
            p.custom = False
            input_buffer.reset()
            return
        p.cancelled = True
        _answer_queue.put(p.finish())
        st["picker"] = None
        event.app.invalidate()

    # ---- 输入框回车：弹层模式走弹层逻辑，普通模式提交输入 ----
    def _on_accept(buffer):
        text = buffer.text
        buffer.reset()
        p = st["picker"]
        if p is None:
            if text:
                log_queue.put(f"❯ {text}\n")   # 回显进滚动区：对话记录里保留用户消息
                input_queue.put(text)
            return
        if p.custom:
            if text.strip():
                p.answers[p.qi] = text.strip()
            p.custom = False
            if p.advance() == "done":
                _answer_queue.put(p.finish())
                st["picker"] = None
            return
        if p.oi == len(p.opts()) - 1:      # 选了「其他」→ 自定义输入
            p.custom = True
            return
        q = p.questions[p.qi]
        if q["multi_select"]:
            labels = [p.opts()[i]["label"] for i in sorted(p.picks[p.qi])]
            p.answers[p.qi] = "、".join(labels) if labels else p.opts()[p.oi]["label"]
        else:
            p.answers[p.qi] = p.opts()[p.oi]["label"]
        if p.advance() == "done":
            _answer_queue.put(p.finish())
            st["picker"] = None

    input_control = BufferControl(buffer=input_buffer, key_bindings=buf_kb)
    input_window = VSplit([
        Window(width=2, height=1, content=FormattedTextControl([("class:prompt", "❯ ")])),
        Window(height=1, content=input_control),
    ])
    picker_window = ConditionalContainer(
        Window(content=FormattedTextControl(
            lambda: _picker_fragments(st["picker"])), wrap_lines=True),
        filter=in_picker,
    )

    style = Style.from_dict({
        "prompt":    "#3b82f6 bold",
        "arrow":     "#9ca3af",
        "chip-cur":  "bg:#5b21b6 #ffffff bold",
        "chip-done": "#10b981",
        "chip-todo": "#6b7280",
        "question":  "bold",
        "opt-cur":   "#3b82f6 bold",
        "opt":       "",
        "dim":       "gray",
    })

    app = Application(
        layout=Layout(HSplit([picker_window, input_window])),
        key_bindings=kb,
        style=style,
        full_screen=False,   # 行内渲染：输出沉入终端滚动区，输入行原位重绘
        output=output or create_output(stdout=_real_stdout),
    )
    _app_holder["app"] = app

    input_buffer.accept_handler = _on_accept

    async def _drain_loop():
        """后台任务：把输出队列直写真终端（经 run_in_terminal 隐藏输入行再打印），
        并激活 ask 弹层。"""
        import traceback
        while True:
            await asyncio.sleep(0.1)
            if _exit_flag.is_set():
                return
            try:
                chunks = []
                while True:
                    try:
                        chunks.append(log_queue.get_nowait())
                    except queue.Empty:
                        break
                if chunks:
                    def _write_chunks():
                        for c in chunks:
                            _real_stdout.write(c)
                        _real_stdout.flush()

                    await run_in_terminal(_write_chunks)   # 3.0.43：返回 Future，直接 await
                activated = False
                while True:
                    try:
                        questions = _ask_queue.get_nowait()
                    except queue.Empty:
                        break
                    if st["picker"] is None:
                        st["picker"] = _PickerState(questions)
                        activated = True
                if activated:
                    app.invalidate()
            except Exception:
                # 打印循环崩溃会吞掉全部输出——把异常直接打到终端上可见
                _real_stdout.write("\n[TUI 打印循环异常]\n" + traceback.format_exc())
                _real_stdout.flush()
                raise

    def _pre_run():
        app.create_background_task(_drain_loop())

    threading.Thread(target=repl_fn, daemon=True).start()
    app.run(pre_run=_pre_run)
