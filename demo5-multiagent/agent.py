#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 - 多 Agent 轴的 Agent

公式：demo5 = demo4 底座 × 多 Agent

    × subagent（subagent.py：独立子任务分包——派一次性 Subagent 在后台执行，
      独立 context，完成后通知机制送回最终报告；主/子共用 run_react_loop）
    × jobs（jobs.py：后台任务机制——shell job 与 agent job 共用一个注册表，
      日志落盘 + 完成通知注入，对齐 Claude Code 的后台任务）
    × 继承 demo4 的规划轴三件（plan / Skill / ask_user_question，全部自包含）
    × 继承 demo2 全部记忆能力（会话持久化 / MEMORY.md / caching）
    × 手动压缩（/compact；同 demo3/4：无自动压缩阈值）

十二文件结构（demo4 九文件 + 多 Agent 层 + 后台任务层 + TUI 层）：
    agent.py     主入口：客户端 + 主循环（depth=0 调 subagent.run_react_loop）+ 轮询循环
    tools.py     工具层（demo1 四件套，execute_bash 加 run_in_background）
    plan.py      规划层：plan 工具（继承 demo4）
    skill.py     Skill 层：Skill 加载器 + use_skill（继承 demo4）
    ask.py       提问层：ask_user_question 方向键 UI + 非 TTY 降级（继承 demo4）
    subagent.py  多 Agent 层：subagent 工具 + 主/子共用 ReAct 循环（demo5 新增）
    jobs.py      后台任务层：Job 注册表 + 日志 + 通知 + job_kill（demo5 新增）
    tui.py       TUI 层：全屏界面——日志区滚动 + 底部常驻输入框 + ask 弹层（demo5 新增）
    render.py    渲染层（demo2 版 + 嵌套缩进；TUI 模式下输出经重定向进日志区）
    memory.py    记忆层（demo2 版减自动压缩）
    session.py   会话层：memory/<会话ID>.jsonl 持久化 + /resume
    commands.py  命令层：/help /status /tools /skills /memory /resume /new /compact /quit

用法：
    python -X utf8 agent.py
"""

import os
import sys
import queue as _queue
import threading

# TUI 输出重定向：必须在 render 等模块 import 之前——rich Console 创建时捕获
# 当时的 sys.stdout；换掉后所有 print / rich 输出自动流进 TUI 日志队列
_TTY_MODE = sys.stdin.isatty() and sys.stdout.isatty()
if _TTY_MODE:
    import tui
    tui.redirect_stdout()

from anthropic import Anthropic

from tools import TOOLS as BASE_TOOLS, AVAILABLE_FUNCTIONS as BASE_FUNCTIONS
import subagent
import plan
import skill
import ask
import jobs
from memory import (
    MEMORY_FILE, MEMORY_WINDOW_LINES, USE_CACHE_CONTROL,
    build_system_prompt, build_system_param, review_memory,
)
import session
from render import (
    print_banner, print_divider, print_error, print_markdown, print_step,
    read_user_input,
)
from commands import SessionState, handle_command


# ============================================================
# Part 1: 配置 + LLM 客户端初始化（同 demo2/demo3/demo4）
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


# 模块级占位：实际使用前由 __main__ 初始化
client: Anthropic = None  # type: ignore


def init_client() -> None:
    """初始化模块级 client（在 __main__ 中调用），并注入 subagent 层"""
    global client
    config = ensure_config()
    kwargs = {
        "api_key": config["api_key"],
        "base_url": config["base_url"],
        # Anthropic SDK 接收秒为单位的超时
        "timeout": config["timeout_ms"] / 1000.0,
    }
    client = Anthropic(**kwargs)
    # 注入多 Agent 层依赖：subagent 循环需要 client / MODEL
    subagent.client = client
    subagent.MODEL = MODEL


# ============================================================
# Part 2: Agent 主循环（depth=0 的共用 ReAct 循环）
# ============================================================
# 与 demo2 的核心区别：
#   - 工具集 = 本地四件套 + subagent（多 Agent 轴）
#   - 主循环走 subagent.run_react_loop——主 Agent 与 Subagent 跑同一个循环，
#     差别只在 messages / system / tools（主/子同构）

def run_agent(user_input: str, history: list, verbose: bool = True):
    """
    在会话历史上跑一轮 ReAct（主循环，depth=0 调共用循环）。

    通知注入：已完成的 job 结果在每次请求前搭车注入 messages（工作时通知）。
    user_input 为空串时不再追加用户消息（空闲唤醒的汇报轮走这条路）。

    Returns:
        (最终回复, 本轮新增消息列表)
    """
    # 通知注入：把待通知的 job 结果作为 user 消息追加——对模型来说和用户消息没有区别
    for note in jobs.REGISTRY.build_notifications():
        history.append({"role": "user", "content": note})

    system_prompt = build_system_prompt(verbose=verbose)
    system_prompt += skill.build_skill_metadata_section()   # Skills 元信息（不触发也不影响）
    system_prompt += ask.ASK_GUIDANCE                       # 提问指引（继承 demo4）
    system_prompt += jobs.JOBS_GUIDANCE                     # 后台任务引导（demo5 新增）
    system_param = build_system_param(system_prompt)

    before = len(history)
    if user_input:
        history.append({"role": "user", "content": user_input})

    final = subagent.run_react_loop(
        messages=history,
        tools=list(ALL_TOOLS),   # 传副本——plan 调用后移除不能误改全量工具集
        local_fns=AVAILABLE_FUNCTIONS,
        system=system_param,
        depth=0,
        verbose=verbose,
    )

    return final, history[before:]


# ============================================================
# 交互式入口
# ============================================================

if __name__ == "__main__":
    init_client()

    # 工具合并：本地四件套 + 规划轴三件 + subagent + job_kill（schema 格式一致，直接拼接）
    ALL_TOOLS = BASE_TOOLS + plan.PLAN_TOOLS + skill.SKILL_TOOLS + [ask.ASK_TOOL] \
                + [subagent.SUBAGENT_TOOL, jobs.JOB_KILL_TOOL]
    AVAILABLE_FUNCTIONS = {**BASE_FUNCTIONS, **plan.PLAN_FUNCTIONS, **skill.SKILL_FUNCTIONS,
                           "ask_user_question": ask.ask_user_question,
                           "subagent": subagent.subagent,
                           "job_kill": jobs.job_kill}

    # 启动时加载 Skills（skill.py 模块级 _SKILLS，use_skill 运行时读取）
    skill._SKILLS = skill.load_skills()

    # 注入 subagent 层依赖：subagent() 需要全量工具做防递归过滤 + 分发路由
    subagent.ALL_TOOLS = ALL_TOOLS
    subagent.FUNCTIONS = AVAILABLE_FUNCTIONS

    print(f"\n[Tools] 共 {len(ALL_TOOLS)} 个本地工具："
          f"{', '.join(t['name'] for t in ALL_TOOLS)}")

    if skill._SKILLS:
        print(f"[Skills] 加载 {len(skill._SKILLS)} 个：")
        for name, info in skill._SKILLS.items():
            print(f"  - {name}: {info['description'][:60]}")
            print(f"    触发词: {', '.join(info['triggers'])}")
    else:
        print(f"[Skills] 未在 {skill.SKILLS_DIR} 找到任何 .md 文件（Agent 仍可运行）")

    state = SessionState(
        model=MODEL,
        base_url=BASE_URL,
        client=client,
        session_id=session.new_session_id(),
        tools=ALL_TOOLS,
        extra_lines=[
            f"工具:   {len(ALL_TOOLS)} 个（四件套 + plan/use_skill/ask + subagent/job_kill）",
            f"Skills: {len(skill._SKILLS)} 个（元信息常驻，正文按需加载）",
            f"项目记忆: {MEMORY_FILE}（窗口 {MEMORY_WINDOW_LINES} 行）",
            "压缩:   仅手动 /compact（无自动压缩）",
            f"缓存:   cache_control={'on' if USE_CACHE_CONTROL else 'off'}",
            f"思考:   {'on（预算 ' + str(subagent.THINKING_BUDGET_TOKENS) + '）' if subagent.USE_THINKING else 'off'}",
        ],
    )
    print_banner("Demo5 Agent 已启动（多 Agent 轴）", [
        f"模型:   {MODEL}",
        f"网关:   {BASE_URL}",
        *state.extra_lines,
        "命令:   /help 查看命令，/quit 退出",
    ])

    # 输入两条路径（demo5 起）：
    #   TTY：tui.py 全屏界面——输入框常驻底部，输出进滚动日志区
    #   非 TTY（管道）：裸 input() 线程——无渲染，无冲突
    if _TTY_MODE:
        import tui
        input_queue = tui.input_queue
    else:
        input_queue = _queue.Queue()
    input_ack = threading.Event()   # 放行门（仅管道输入线程用；TUI 下 set 无害）
    _main_busy = threading.Event()  # 主循环正在跑轮次：job 完成提示延后到本轮结束
    _pending_notices: list = []

    def _job_completed(text: str) -> None:
        """job 完成的终端提示。
        主循环忙（正在跑轮次）→ 延后到本轮结束再打印（不插进轮次输出中间）；
        空闲 → 直接打印（TUI 进日志区，管道进终端）+ 放行输入线程重弹提示符。"""
        if _main_busy.is_set():
            _pending_notices.append(text)
        else:
            print_step("tool_return", text, limit=200)
            input_ack.set()

    jobs.REGISTRY.on_complete = _job_completed

    def _input_reader() -> None:
        """管道模式输入线程：读一条入队 → 等主循环放行 → 再读下一条"""
        while True:
            try:
                inp = read_user_input()
            except Exception:
                inp = None
            input_queue.put(inp)
            if inp is None:
                break   # EOF：线程自行退出
            input_ack.wait()
            input_ack.clear()

    WAKEUP = object()   # 唤醒标记：后台 job 全部完成 → 触发通知汇报轮

    # 任务结束回顾挪后台线程（fire-and-forget）：回顾是一次同步 LLM 调用，
    # 原地跑会让交互卡顿 1-3 秒；后台跑轮次立即返回。锁保证同一时刻只有一个
    # 回顾在写 MEMORY.md（连续提问时不互相覆盖）。
    _review_lock = threading.Lock()

    def _review_async(history: list) -> None:
        def _run() -> None:
            with _review_lock:
                try:
                    review_memory(history, client, MODEL, verbose=True)
                except Exception:
                    pass   # 回顾失败不影响主流程
        threading.Thread(target=_run, daemon=True).start()

    def _clean_exit() -> None:
        """退出：清理 job。管道模式直接 os._exit（输入线程可能仍阻塞在 prompt 上，
        Windows 解释器正常收尾会因 daemon 线程持 stdout 锁而 fatal）；
        TUI 模式通知 TUI 退出后本线程结束（主线程收尾）。"""
        jobs.REGISTRY.shutdown()
        if _TTY_MODE:
            tui.request_exit()
            raise SystemExit
        sys.stdout.flush()
        os._exit(0)

    def _repl_loop(state):
        """共享的轮询主循环（TTY 在控制器线程跑，管道在主线程跑）：
        等用户输入，或等后台 job 全部完成（自动唤醒）"""
        while True:
            try:
                user_input = input_queue.get(timeout=0.5)
                # 完成待通知的 job 优先于排队输入：先跑汇报轮，输入放回下一轮处理
                if jobs.REGISTRY.has_unnotified() and not jobs.REGISTRY.has_running():
                    input_queue.put(user_input)
                    user_input = WAKEUP
            except _queue.Empty:
                if jobs.REGISTRY.has_unnotified() and not jobs.REGISTRY.has_running():
                    user_input = WAKEUP
                else:
                    continue   # job 还在跑：继续等（输入随时可来）

            # 自动唤醒：不追加用户消息——通知在 run_agent 开头注入
            if user_input is WAKEUP:
                _main_busy.set()
                try:
                    final, new_msgs = run_agent("", state.history, verbose=True)
                    session.append_messages(state.session_id, new_msgs)
                    print_markdown(final)
                    _review_async(list(state.history))   # 后台回顾：轮次立即返回
                except Exception as e:
                    print_error(f"[错误] {e}")
                finally:
                    _main_busy.clear()
                    for text in _pending_notices:      # 轮次期间完成的 job：此刻补打印
                        print_step("tool_return", text, limit=200)
                    _pending_notices.clear()
                    input_ack.set()
                continue

            if user_input is None:
                print("再见！")
                _clean_exit()

            if not user_input:
                continue

            _main_busy.set()
            action = handle_command(user_input, state)
            _main_busy.clear()
            for text in _pending_notices:      # 命令执行期间完成的 job：此刻补打印
                print_step("tool_return", text, limit=200)
            _pending_notices.clear()
            if action == "break":
                _clean_exit()
            if action == "continue":
                input_ack.set()
                continue

            _main_busy.set()
            try:
                final, new_msgs = run_agent(user_input, state.history, verbose=True)
                session.append_messages(state.session_id, new_msgs)
                print_markdown(final)
                _review_async(list(state.history))   # 后台回顾：轮次立即返回
            except Exception as e:
                print_error(f"[错误] {e}")
            finally:
                _main_busy.clear()
                for text in _pending_notices:      # 轮次期间完成的 job：此刻补打印
                    print_step("tool_return", text, limit=200)
                _pending_notices.clear()
                input_ack.set()   # 本轮处理完：放行输入线程显示下一个提示符（管道模式）

    if _TTY_MODE:
        tui.run(lambda: _repl_loop(state))   # 主线程进 TUI；控制器线程跑 _repl_loop
        jobs.REGISTRY.shutdown()          # 兜底（控制器退出时已清过，幂等）
        sys.__stdout__.flush()
        os._exit(0)
    else:
        threading.Thread(target=_input_reader, daemon=True).start()
        _repl_loop(state)
