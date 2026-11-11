#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo7 — 框架对照篇：把 demo6（系列终态手搓 Runtime）交给 LangGraph 重写

demo(N) = base × 轴N；demo7 = base × 框架（不新增能力，换一种表达）。
代码实现三条核心轴的对照（循环 / 约束 / 记忆——LangGraph 的三个招牌能力），
其余部件（MCP / plan / Skill / Subagent / 完整规则表 / Hook）重写麻烦、
教学增量低，见 README §3 映射总表的对应物说明。

    循环轴   手搓 while 循环        → StateGraph（llm 节点 + tools 节点 + 条件边回环）
    约束轴   demo6 的 permission ask → gate 节点 + interrupt() 审批门
    记忆轴   demo2 的 jsonl + /resume → SqliteSaver 落盘 + thread_id + /resume

文件刻意比其他 demo 薄（两个 py 文件）：框架接管循环后，你写的 Runtime
只剩「图定义 + 渲染」。与 demo1 对照着读——

    demo1 agent.py  run_agent()   里的 while 循环 / stop_reason 判停 / 消息追加
    demo7 agent.py  build_graph() 里的 StateGraph / 条件边 / MessagesState reducer

    agent.py     本文件：graph 定义 + REPL + .env 加载
    tools.py     工具层：与 demo1 逐字相同（工具实现零改动是教学点）
"""

import os
import sys
from pathlib import Path

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from tools import AVAILABLE_FUNCTIONS, TOOLS


# ============================================================
# Part 0: 配置 — Key 三级回退（.env 文件 → 环境变量 → 交互式输入）
# ============================================================
# 与 demo1 的差异：优先读同目录 .env（模板见 .env.example，已被 .gitignore 覆盖），
# 真实环境变量仍最高优先——env -u ANTHROPIC_* 双 auth 坑与 demo1 相同（见仓库 CLAUDE.md）。

BASE_URL = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL    = "glm-5.2"

# 对照 demo1 的 MAX_ITERATIONS = 30。口径差异：LangGraph 数的 recursion_limit 是
# 「超级步」（一个节点执行 = 一步），一轮 ReAct ≈ 3 步（llm → gate → tools），
# 60 步 ≈ demo1 的 20 轮，同样留了安全垫。
RECURSION_LIMIT = 60

SYSTEM_PROMPT = (
    "你是一个有用的助手，可以通过工具读写文件、执行命令，帮用户完成任务。"
    "工作流程：先理解需求，再动手实现，实现完后必须运行验证。"
)

# 约束轴教学最小版：只有 execute_bash 过审批门。扩展成 demo6 的
# (工具, fnmatch 模式, action) 规则表只需替换 needs_approval 的判定，图结构不变。
APPROVAL_TOOLS = {"execute_bash"}


def load_env_file(env_path: Path) -> dict:
    """极简 .env 解析：只认 KEY=VALUE 行，# 注释与空行跳过，不做变量展开"""
    values = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def load_config() -> dict:
    """Key 三级回退：环境变量 > demo7/.env > 交互式输入"""
    file_key = load_env_file(Path(__file__).parent / ".env").get("ANTHROPIC_API_KEY", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or file_key
    if not api_key:
        api_key = input("请输入 API Key: ").strip()
    if not api_key:
        raise SystemExit("未提供 API Key，退出")
    return {"api_key": api_key, "base_url": BASE_URL, "model": MODEL}


# ============================================================
# Part 1: 图定义 — 手搓 while 循环的 LangGraph 表达
# ============================================================
# demo1 的 while 循环里三件事：决策（发 messages 给 LLM）、判停（stop_reason）、
# 行动+感知（执行工具、结果追加回 messages）。图上一一对应：
#     决策      → llm 节点
#     判停      → route_after_llm 条件边（有 tool_calls 走 gate，否则 END）
#     行动+感知 → tools 节点（ToolNode 执行工具并自动生成配对的 ToolMessage）
# messages 的「追加」不再手写 history.append——MessagesState 自带 add_messages
# reducer，节点返回什么就往状态里合并什么。

class AgentState(MessagesState):
    """MessagesState 只带 messages；加一个 gate_decision 供条件边路由读取。
    框架约束：条件边函数只能读 state，所以节点里的决定必须先写回状态。"""
    gate_decision: str


def wrap_tools() -> list:
    """demo1 的工具函数包成 LangChain Tool——框架侧唯一要付的「壳税」。
    schema（name/description/参数）直接复用 tools.py 里的说明书。"""
    return [
        StructuredTool.from_function(
            func=AVAILABLE_FUNCTIONS[t["name"]],
            name=t["name"],
            description=t["description"],
        )
        for t in TOOLS
    ]


def route_after_llm(state: AgentState) -> str:
    """条件边 = demo1 的判停：stop_reason != "tool_use" 即退出循环。
    LangChain 消息没有 stop_reason，等价判据是 AIMessage 上有没有 tool_calls。"""
    last = state["messages"][-1]
    return "gate" if getattr(last, "tool_calls", None) else END


def needs_approval(tool_calls: list) -> list:
    """筛出要过审批门的调用（教学最小版：execute_bash）"""
    return [c for c in tool_calls if c["name"] in APPROVAL_TOOLS]


def apply_gate_decision(ai_msg, decision: str) -> dict:
    """审批结果的落账：
    allow → 放行（gate_decision=allow，路由去 tools 真执行）
    deny  → 给这条 AIMessage 上的**每个** tool_call 配一条拒绝 ToolMessage
            （Anthropic 协议要求 tool_use/tool_result 严格配对，拒绝也不能缺位），
            直接路由回 llm——模型看到拒绝会自己换路子"""
    if decision == "allow":
        return {"gate_decision": "allow"}
    denials = [
        ToolMessage(
            content="[用户拒绝] 该工具调用未获审批，请换一种不涉险的方式完成任务",
            tool_call_id=c["id"],
        )
        for c in ai_msg.tool_calls
    ]
    return {"gate_decision": "deny", "messages": denials}


def gate(state: AgentState) -> dict:
    """约束轴：审批门 = demo6 的 permission ask，换成了 LangGraph 的 interrupt()。

    interrupt 的语义是「整张图暂停落盘，等人类用 Command(resume=...) 回答」——
    与 demo6 在 REPL 里弹 input 相比，暂停点、状态、恢复值全部由框架管理：
    暂停点任意（节点内任意位置调用都行）、状态自动进 checkpointer（重启也能恢复）、
    恢复值就是你 resume 传的值，interrupt() 调用处原地收到。"""
    last = state["messages"][-1]
    dangerous = needs_approval(last.tool_calls)
    if not dangerous:
        return {"gate_decision": "allow"}
    decision = interrupt({
        "type": "approval",
        "calls": [{"name": c["name"], "args": c["args"]} for c in dangerous],
    })
    return apply_gate_decision(last, decision)


def route_after_gate(state: AgentState) -> str:
    return "tools" if state["gate_decision"] == "allow" else "llm"


def build_graph(llm: ChatAnthropic, tools_lc: list, checkpointer=None):
    """组装三节点图（对照 demo1 run_agent 的 while 骨架）：

        START → llm ──(无 tool_calls)──→ END            ← 判停出口
                 └─(有)→ gate ──(allow)→ tools ──→ llm   ← 循环回环
                          └─(deny)──→ llm               ← 拒绝也回环
    """
    llm_with_tools = llm.bind_tools(tools_lc)

    def call_model(state: AgentState) -> dict:
        """llm 节点 = demo1 的「决策」：全量 messages 发给模型（含 system）"""
        response = llm_with_tools.invoke(
            [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
        )
        return {"messages": [response]}

    builder = StateGraph(AgentState)
    builder.add_node("llm", call_model)
    builder.add_node("gate", gate)
    builder.add_node("tools", ToolNode(tools_lc))
    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", route_after_llm, ["gate", END])
    builder.add_conditional_edges("gate", route_after_gate, ["tools", "llm"])
    builder.add_edge("tools", "llm")
    return builder.compile(checkpointer=checkpointer)


# ============================================================
# Part 2: 渲染 — rich 分色（管道自动去色），对照 demo1 render.py 的精简版
# ============================================================

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.rule import Rule

console = Console()

_STYLES = {
    "assistant":   ("●", "green"),
    "tool_call":   ("⏺", "yellow"),
    "tool_return": ("✔", "cyan"),
    "approval":    ("⚠", "red"),
}


def _preview(text: str, limit: int = 0) -> str:
    text = str(text).replace("\n", " ").strip()
    if limit and len(text) > limit:
        return text[:limit] + "..."
    return text


def print_step(kind: str, text: str, limit: int = 0) -> None:
    icon, style = _STYLES[kind]
    console.print(f"[{style}]{icon} {kind:<11}[/] | {escape(_preview(text, limit))}")


def print_divider(title: str = "") -> None:
    console.print(Rule(title, style="grey50"))


def print_markdown(text: str) -> None:
    console.print(Markdown(text))


def print_error(text: str) -> None:
    console.print(f"[red]{escape(text)}[/]")


if sys.stdin.isatty() and sys.stdout.isatty():
    try:
        from prompt_toolkit import PromptSession
        _prompt_session = PromptSession()
    except ImportError:
        _prompt_session = None
else:
    _prompt_session = None


def read_user_input():
    """读一行输入（prompt_toolkit 编辑手感 / 管道降级 input()）；None 表示退出"""
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


# ============================================================
# Part 3: REPL — 跑图、渲染、审批弹层、/resume
# ============================================================

def extract_interrupt(event: dict):
    """updates 流里 interrupt 的形状：{'__interrupt__': (Interrupt(...),)}，
    也可能挂在节点名下——两种都认。返回 Interrupt 或 None。"""
    if "__interrupt__" in event:
        return event["__interrupt__"][0]
    for value in event.values():
        if isinstance(value, dict) and "__interrupt__" in value:
            return value["__interrupt__"][0]
    return None


def render_event(event: dict) -> None:
    """把一个节点更新渲染成 demo1 风格的步骤行"""
    for node, update in event.items():
        if node == "__interrupt__" or not isinstance(update, dict):
            continue
        for msg in update.get("messages", []):
            if isinstance(msg, ToolMessage):
                print_step("tool_return", msg.content, limit=200)
            elif getattr(msg, "type", "") == "ai":
                content = msg.content
                if isinstance(content, str):
                    if content:
                        print_step("assistant", content, limit=80)
                else:
                    # glm 会自发输出 thinking 块：content 是块列表，按 type 挑 text 渲染
                    # （对照 demo1 遍历 content blocks；thinking 不展示，与 USE_THINKING 默认关一致）
                    for block in content:
                        btype = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
                        if btype == "text":
                            text = block.get("text", "") if isinstance(block, dict) else block.text
                            if text:
                                print_step("assistant", text, limit=80)
                for c in getattr(msg, "tool_calls", None) or []:
                    print_step("tool_call", f"{c['name']}({c['args']})")


def ask_approval(value: dict) -> str:
    """审批弹层 = demo6 的 ask 冒泡。图已暂停，人在终端答 y/n，
    REPL 再用 Command(resume="allow"/"deny") 把答案传回 interrupt() 调用处。
    管道（非交互）时默认拒绝——安全优先，自动跑通 deny 分支也是完整演示。"""
    for call in value["calls"]:
        print_step("approval", f"{call['name']}({call['args']})")
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print_error("[非交互模式] 审批默认拒绝（deny）")
        return "deny"
    while True:
        answer = input("⚠ 允许执行以上命令？(y/n): ").strip().lower()
        if answer in ("y", "n"):
            return "allow" if answer == "y" else "deny"


def run_stream(graph, payload, config) -> None:
    """跑一次任务：stream 逐节点渲染；遇到 interrupt 弹审批，resume 后继续。
    payload 第一次是用户输入 dict，之后（若审批过）是 Command(resume=...)。"""
    while True:
        interrupted = None
        for event in graph.stream(payload, config, stream_mode="updates"):
            intr = extract_interrupt(event)
            if intr is not None:
                interrupted = intr
                break
            render_event(event)
        if interrupted is None:
            return
        decision = ask_approval(interrupted.value)
        payload = Command(resume=decision)


def list_threads(saver) -> list:
    """记忆轴：列出 checkpoints.sqlite 里的历史会话（thread_id + 首条用户消息）。
    注意 saver.list 枚举的是**检查点**（每个超级步存一个），同一 thread 会出现多次——
    demo2 手搓 jsonl 时一会话一文件天然去重；框架粒度更细，去重自己写。"""
    items, seen = [], set()
    for tup in saver.list(None, limit=200):   # 时间倒序，首见即该 thread 最新快照
        tid = tup.config["configurable"]["thread_id"]
        if tid in seen:
            continue
        seen.add(tid)
        msgs = tup.checkpoint.get("channel_values", {}).get("messages", [])
        first_user = next((m.content for m in msgs if getattr(m, "type", "") == "human"), "")
        items.append((tid, _preview(first_user, 30)))
    return items


def make_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}


def handle_command(user_input: str, config: dict, saver, graph) -> str:
    """斜杠命令（对照 demo1 commands.py 的精简版）：
    返回 "continue" 继续读输入 / "break" 退出；/resume 的换 thread 就地改 config"""
    if user_input == "/quit":
        return "break"
    if user_input == "/help":
        print("命令: /status 会话状态 | /resume 接续历史会话 | /quit 退出")
        return "continue"
    if user_input == "/status":
        snap = graph.get_state(config)
        print(f"thread_id: {config['configurable']['thread_id']}  "
              f"messages: {len(snap.values.get('messages', []))}  模型: {MODEL}")
        return "continue"
    if user_input == "/resume":
        items = list_threads(saver)
        if not items:
            print("（暂无历史会话）")
            return "continue"
        for i, (tid, preview) in enumerate(items, 1):
            print(f"  [{i}] {tid}  {preview}")
        choice = input("接续哪个会话？输入序号（回车取消）: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(items):
            chosen = items[int(choice) - 1][0]
            config["configurable"]["thread_id"] = chosen   # 就地换 thread，后续 stream 自动带历史
            snap = graph.get_state(config)
            print(f"[已接续] {chosen}（messages {len(snap.values.get('messages', []))} 条）")
        return "continue"
    print(f"未知命令: {user_input}（/help 查看可用命令）")
    return "continue"


def repl(graph, saver) -> None:
    """交互主循环。会话 = thread_id：checkpointer 按 thread 存全部状态，
    同一 thread 下次 stream 自动带上历史——demo2 手搓 jsonl + /resume 的框架版。"""
    thread_id = os.urandom(6).hex()
    config = make_config(thread_id)
    print(f"Demo7 LangGraph Agent 已启动（thread {thread_id}）")
    print(f"模型: {MODEL}  网关: {BASE_URL}")
    print("命令: /help 查看命令，/quit 退出")

    while True:
        user_input = read_user_input()
        if user_input is None:
            print("再见！")
            break
        if not user_input:
            continue

        if user_input.startswith("/"):
            if handle_command(user_input, config, saver, graph) == "break":
                print("再见！")
                break
            continue

        try:
            run_stream(graph, {"messages": [("user", user_input)]}, config)
        except GraphRecursionError:
            print_error(f"[错误] 超过 recursion_limit（{RECURSION_LIMIT}），可能陷入死循环")
        except Exception as e:
            print_error(f"[错误] {e}")


if __name__ == "__main__":
    cfg = load_config()
    llm = ChatAnthropic(
        model=MODEL,
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        max_tokens=4096,
    )
    os.makedirs("memory", exist_ok=True)
    with SqliteSaver.from_conn_string("memory/checkpoints.sqlite") as saver:
        graph = build_graph(llm, wrap_tools(), saver)
        repl(graph, saver)
