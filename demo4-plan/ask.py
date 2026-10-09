#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4 提问层 — ask_user_question：让 Agent 主动向用户提问（规划轴新增）

需求有歧义 / 有多种合理实现 / 拿不准方向时，LLM 调这个工具把问题抛给用户，
而不是自作主张。入参 = 问题列表（结构化 schema），工具内部弹出方向键选择 UI
收集答案，再把答案拼成自然语言作为 tool_result 塞回模型。

设计对齐 Claude Code 的 AskUserQuestion：
    - 批量提问（1-4 题，顶部 chip 导航切换）
    - 选项带说明（label + description 两层）
    - UI 自动追加「其他（输入自定义文本）」——schema 里不列 Other
    - 有推荐选项放第一个，label 末尾加「（推荐）」
    - Esc 取消：返回「用户取消了提问…不要自作主张」

非 TTY 降级：管道喂任务时没有真终端，方向键 UI 无法工作——自动退化为
逐题打印 + 读一行（编号选择或自由文本），CI / 自动验证不炸。
"""

import sys


# system prompt 追加的提问指引（工具描述管「怎么填」，这里管「什么时候用」）
ASK_GUIDANCE = (
    "\n\n## 提问指引\n\n"
    "如果用户的需求有歧义、有多种合理实现可选、或你拿不准方向，"
    "应当先用 ask_user_question 工具向用户提问澄清，不要自作主张。"
)

ASK_TOOL = {
    "name": "ask_user_question",
    "description": (
        "向用户提多选题，收集偏好、澄清歧义、让用户做决定。"
        "**使用时机**：需求有歧义、有多种合理实现、拿不准方向时——先问再干，不要自作主张。"
        "**约定**：UI 会自动追加「其他（自定义输入）」，不要在 options 里再列；"
        "有推荐选项放第一个，label 末尾加「（推荐）」。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "description": "1-4 个问题",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "完整问句，以问号结尾"},
                        "header":   {"type": "string", "description": "≤12 字符的标签，渲染在 UI 顶部导航"},
                        "options": {
                            "type": "array",
                            "description": "2-4 个选项",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label":       {"type": "string", "description": "选项展示文本，1-5 个词"},
                                    "description": {"type": "string", "description": "选项含义 / 选了会发生什么"},
                                },
                                "required": ["label"],
                            },
                        },
                        "multi_select": {"type": "boolean", "description": "True 多选（选项不互斥），False 单选（默认）"},
                    },
                    "required": ["question", "header", "options"],
                },
            },
        },
        "required": ["questions"],
    },
}


# ============================================================
# 入参校验 + 主入口
# ============================================================

def _validate(questions) -> "list | str":
    """校验失败返回错误字符串（喂回模型重试），成功返回规范化的问题列表。"""
    if not isinstance(questions, list) or not (1 <= len(questions) <= 4):
        return "[错误] questions 必须是 1-4 个问题的数组，请调整后重试"
    norm = []
    for q in questions:
        if not isinstance(q, dict):
            return "[错误] 每个问题必须是对象"
        opts = q.get("options")
        if not isinstance(opts, list) or not (2 <= len(opts) <= 4):
            return f'[错误] 问题 "{q.get("question", "")}" 的 options 必须是 2-4 个，请调整后重试'
        norm.append({
            "question":     str(q.get("question", "")).strip(),
            "header":       str(q.get("header", "")).strip()[:12] or "问题",
            "options":      [{"label": str(o.get("label", "")).strip(),
                              "description": str(o.get("description", "")).strip()} for o in opts],
            "multi_select": bool(q.get("multi_select", False)),
        })
    return norm


def _format_result(questions, answers) -> str:
    pairs = "; ".join(f'"{q["question"]}" → "{a}"' for q, a in zip(questions, answers))
    return f"用户回答如下：{pairs}。请基于这些回答继续。"


CANCELLED_MSG = "用户取消了提问，未提供任何回答。请等待用户进一步指示，不要自作主张。"


def ask_user_question(questions) -> str:
    """主入口：校验 → 收集答案（方向键 UI 或降级）→ 拼自然语言返回。"""
    norm = _validate(questions)
    if isinstance(norm, str):
        return norm

    if _is_tty():
        try:
            answers = _Picker(norm).run()
        except KeyboardInterrupt:
            print("[ask] 已取消")
            answers = None
        except Exception as e:
            print(f"[ask] 方向键 UI 异常（{e}），降级为文本输入")
            answers = _ask_plain(norm)
    else:
        answers = _ask_plain(norm)

    if answers is None:
        return CANCELLED_MSG
    return _format_result(norm, answers)


def _is_tty() -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    try:
        import prompt_toolkit  # noqa: F401
        return True
    except ImportError:
        return False


# ============================================================
# 非 TTY 降级：逐题打印 + 读一行
# ============================================================

def _ask_plain(questions) -> "list | None":
    n = len(questions)
    print("\n┌─ Agent 提问 " + "─" * 42)
    answers = []
    try:
        for i, q in enumerate(questions, 1):
            multi = "(多选，编号逗号分隔)" if q["multi_select"] else ""
            print(f"│ [{i}/{n}] {q['question']} {multi}")
            for j, opt in enumerate(q["options"], 1):
                desc = f" —— {opt['description']}" if opt["description"] else ""
                print(f"│   {j}. {opt['label']}{desc}")
            raw = input("│ 你的选择（编号或直接输入文字）: ").strip()
            if not raw:
                return None
            if q["multi_select"]:
                picked = []
                for part in raw.replace("，", ",").split(","):
                    part = part.strip()
                    if part.isdigit() and 1 <= int(part) <= len(q["options"]):
                        picked.append(q["options"][int(part) - 1]["label"])
                    elif part:
                        picked.append(part)
                answers.append("、".join(picked) if picked else raw)
            else:
                if raw.isdigit() and 1 <= int(raw) <= len(q["options"]):
                    answers.append(q["options"][int(raw) - 1]["label"])
                else:
                    answers.append(raw)
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    print("└" + "─" * 55)
    return answers


# ============================================================
# 方向键 UI（prompt_toolkit，对齐 Claude Code 的 AskUserQuestion）
# ============================================================
# 顶部 chip 导航（←→ 切题）+ 带说明的纵向选项列表（↑↓ 移动）
# 单选：Enter 选中并跳下一题；多选：Space 勾选、Enter 确认
# 末位「其他」：Enter 进入自定义文本输入；Esc 取消整个提问

def _run_picker(questions):
    """构建并运行 prompt_toolkit 应用，返回 answers 列表或 None（取消）。"""
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.styles import Style

    OTHER = {"label": "其他（输入自定义文本）", "description": ""}

    class State:
        def __init__(self):
            self.qi = 0                       # 当前题
            self.oi = 0                       # 当前选项（含“其他”）
            self.answers = [None] * len(questions)
            self.picks = [set() for _ in questions]   # 多选已勾选项
            self.custom = False               # 正在输入自定义文本
            self.cancelled = False
            self.done = False

        def opts(self):
            return questions[self.qi]["options"] + [OTHER]

        def advance(self):
            if self.qi >= len(questions) - 1:
                self.done = True
            else:
                self.qi += 1
                self.oi = 0

    st = State()
    buf = Buffer(multiline=False)

    kb = KeyBindings()

    @kb.add("up", filter=~Condition(lambda: st.custom))
    def _up(event):
        st.oi = (st.oi - 1) % len(st.opts())

    @kb.add("down", filter=~Condition(lambda: st.custom))
    def _down(event):
        st.oi = (st.oi + 1) % len(st.opts())

    @kb.add("left", filter=~Condition(lambda: st.custom))
    def _left(event):
        st.qi = (st.qi - 1) % len(questions)
        st.oi = 0

    @kb.add("right", filter=~Condition(lambda: st.custom))
    def _right(event):
        st.qi = (st.qi + 1) % len(questions)
        st.oi = 0

    @kb.add("space", filter=~Condition(lambda: st.custom))
    def _space(event):
        if questions[st.qi]["multi_select"] and st.oi < len(st.opts()) - 1:
            if st.oi in st.picks[st.qi]:
                st.picks[st.qi].discard(st.oi)
            else:
                st.picks[st.qi].add(st.oi)

    @kb.add("enter", filter=~Condition(lambda: st.custom))
    def _enter(event):
        if st.oi == len(st.opts()) - 1:            # 选了「其他」→ 自定义输入
            st.custom = True
            event.app.layout.focus(buf_window)
            return
        q = questions[st.qi]
        if q["multi_select"]:
            labels = [st.opts()[i]["label"] for i in sorted(st.picks[st.qi])]
            st.answers[st.qi] = "、".join(labels) if labels else st.opts()[st.oi]["label"]
        else:
            st.answers[st.qi] = st.opts()[st.oi]["label"]
        st.advance()
        if st.done:                                # 最后一题答完 → 结束（即 Submit）
            event.app.exit()

    def _cancel(event):
        st.cancelled = True
        st.done = True
        event.app.exit()                           # 取消并结束

    # 取消键逐个单独注册：escape 与普通键塞进同一个 kb.add 时，Escape 的
    # 序列解析（Alt 组合键机制）会把同注册的其他键吞掉
    kb.add("escape", filter=~Condition(lambda: st.custom))(_cancel)
    kb.add("c-c", filter=~Condition(lambda: st.custom))(_cancel)

    # 自定义输入模式：Enter 提交文本，Esc 返回选项列表
    buf_kb = KeyBindings()

    @buf_kb.add("enter")
    def _buf_enter(event):
        text = buf.text.strip()
        if text:
            st.answers[st.qi] = text
            buf.reset()
            st.custom = False
            event.app.layout.focus(opt_window)
            st.advance()
            if st.done:                            # 自定义输入答完最后一题 → 结束
                event.app.exit()

    @buf_kb.add("escape")
    def _buf_esc(event):
        if not st.custom:              # 焦点异常落在 buffer 时，Esc 仍取消并退出
            st.cancelled = True
            st.done = True
            event.app.exit()
            return
        buf.reset()
        st.custom = False
        event.app.layout.focus(opt_window)

    def chips():
        frags = [("class:arrow", "← "),]
        for i, q in enumerate(questions):
            # ✔ 已答 / ❯ 当前 / 未答无标记（灰色）——□ 与多选勾选框撞符号，不用
            mark = "✔" if st.answers[i] is not None else ("❯" if i == st.qi else "")
            cls = "class:chip-done" if st.answers[i] is not None else (
                "class:chip-cur" if i == st.qi else "class:chip-todo")
            frags.append((cls, f" {mark} {q['header']} "))
        frags.append(("class:arrow", " Submit →\n"))
        return frags

    def question_line():
        q = questions[st.qi]
        multi = "（多选：Space 勾选）" if q["multi_select"] else ""
        return [("class:question", f"\n{q['question']} {multi}\n")]

    def option_lines():
        frags = []
        for i, opt in enumerate(st.opts()):
            pointer = "❯ " if i == st.oi else "  "
            if questions[st.qi]["multi_select"] and i < len(st.opts()) - 1:
                pointer = ("❯ ☑ " if i == st.oi else "  ☑ ") if i in st.picks[st.qi] \
                    else ("❯ □ " if i == st.oi else "  □ ")
            cls = "class:opt-cur" if i == st.oi else "class:opt"
            frags.append((cls, f"{pointer}{opt['label']}\n"))
            if opt["description"]:
                frags.append(("class:dim", f"      {opt['description']}\n"))
        return frags

    footer = [("class:dim",
               "\n↑↓ 选项 · Enter 选中/确认 · ←→ 切换题目 · Esc 取消")]

    opt_window = Window(FormattedTextControl(
        lambda: chips() + question_line() + option_lines() + footer,
        focusable=True, show_cursor=False), wrap_lines=True)   # focusable：启动焦点落它
    buf_window = Window(BufferControl(buffer=buf, key_bindings=buf_kb))

    style = Style.from_dict({
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
        layout=Layout(HSplit([
            opt_window,
            ConditionalContainer(buf_window, filter=Condition(lambda: st.custom)),
        ])),
        style=style,
        full_screen=False,  # 内联渲染（对齐 Claude Code）：在对话流下方弹出；
                            # 若左右切换时出现宽字符重绘错位，可换 full_screen=True 或 ASCII 符号
        erase_when_done=True,  # 退出时擦掉渲染区域——提交/取消后界面消失，不留在滚动历史里
        key_bindings=kb,    # 应用级绑定：不依赖窗口焦点——opt 控件不可聚焦时按键也生效
                            # （buffer 聚焦时其自身绑定优先，Enter/Esc 走自定义输入路径）
    )
    app.layout.focus(opt_window)
    app.run()

    if st.cancelled or any(a is None for a in st.answers):
        return None
    return st.answers


# 命名别名（ask_user_question 主入口在上方；Picker 的构建函数独立方便测试）
_Picker = lambda questions: _PickerShell(questions)


class _PickerShell:
    """薄壳：把 _run_picker 包装成 .run() 接口。"""

    def __init__(self, questions):
        self.questions = questions

    def run(self):
        return _run_picker(self.questions)
