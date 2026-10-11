#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo6 约束层 — Permission + Hook + dispatch_tool（约束轴核心）

在 LLM 决策和原始工具执行之间插入两层 Runtime Control，让危险操作
可配置、可观测、可拦截——而不是硬编码在某个工具函数里：

    Permission（规则引擎）  策略层：工具调用前的访问控制（allow / deny / ask）
    Hook（Lifecycle 扩展）  扩展层：PreToolUse / PostToolUse 可插拔回调
                           （Pre 可拦截 / 改写 input，Post 可替换 / 补改 output）

dispatch_tool 串接两层：Pre hooks → Permission → 原始工具 → Post hooks。
接入点在共用 ReAct 循环的工具执行处（subagent.py）——主 Agent 与 Subagent
一体受控（Runtime Control 长在循环上，主/子同构地过安检）。

ask（需人工确认）的两种送达方式——**审批冒泡**：
    主 Agent（前台轮次）  direct：同线程直接弹确认层（复用 ask 层 UI：
                          TUI 弹层 / 方向键 UI / 管道降级，与 ask_user_question 同基建）
    Subagent（后台线程）  bubble：入队 + Event 挂起，等主循环空闲时弹层，
                          结果回填唤醒——后台线程不能碰 UI/stdin，且完成时机
                          随机，不能按 subagent 的节奏打断用户（对齐 Claude Code
                          的审批冒泡）
    挂起期间被 job_kill（stop_event 置位）或管道模式（无交互 UI）→ 自动拒绝

约束轴的全部代码长在本文件。_auto_approve_all（本会话全允许）由确认层
第三选项置位，/new 时经 reset_session() 重置。
"""

import fnmatch
import re
import threading
from dataclasses import dataclass, field

import ask


# ============================================================
# Permission：声明式规则引擎
# ============================================================
# 规则格式：(tool_name, pattern, action)
#   tool_name: 工具名（demo6 全量 9 个）
#   pattern:   fnmatch shell 通配符，匹配工具的关键参数——
#              execute_bash 匹配 command，read_file/write_file/edit 匹配 path，
#              其余工具（plan/use_skill/ask_user_question/subagent/job_kill）
#              无关键参数，用空串匹配 "*"
#   action:    "allow" / "deny" / "ask"
# 匹配顺序：从上到下，first-match wins；无命中走 DEFAULT_POLICY。

PERMISSION_RULES = [
    # —— 显式 deny —— 绝不让 LLM 跑的命令（Unix + Windows 原生各一套：
    #    实测 glm 会按环境选命令——Windows 下写 rmdir /s /q 而不是 rm -rf）
    ("execute_bash", "rm -rf *",       "deny"),
    ("execute_bash", "rmdir /s *",     "deny"),
    ("execute_bash", "rd /s *",        "deny"),
    ("execute_bash", "format*",        "deny"),
    ("execute_bash", "mkfs.*",         "deny"),
    ("execute_bash", "shutdown*",      "deny"),
    ("execute_bash", "curl *| *sh*",   "deny"),

    # —— 显式 allow —— 安全只读类，免确认
    ("execute_bash", "ls *",           "allow"),
    ("execute_bash", "dir *",          "allow"),
    ("execute_bash", "cat *",          "allow"),
    ("execute_bash", "grep *",         "allow"),

    # —— 其他 execute_bash：问一下
    ("execute_bash", "*",              "ask"),

    # —— 文件类工具：默认放行（写保护演示靠 deny 规则扩展）
    ("read_file",    "*",              "allow"),
    ("write_file",   "*",              "allow"),
    ("edit",         "*",              "allow"),

    # —— demo5 继承的派发 / 纯本地工具：放行（真正的把关在各自内部执行时）
    ("plan",              "*",        "allow"),
    ("use_skill",         "*",        "allow"),
    ("ask_user_question", "*",        "allow"),
    ("subagent",          "*",        "allow"),
    ("job_kill",          "*",        "allow"),
]

DEFAULT_POLICY = "ask"

# Permission 的 pattern 匹配用关键参数（与工具 schema 对应）
_TOOL_KEY_FIELD = {
    "execute_bash": "command",
    "read_file":    "path",
    "write_file":   "path",
    "edit":         "path",
    # 以下工具无关键参数：key_value 取空串，"*" 照样匹配
    "plan":              "",
    "use_skill":         "",
    "ask_user_question": "",
    "subagent":          "",
    "job_kill":          "",
}

# 本会话全允许（确认层选「本会话全允许」后置 True，本会话所有 ask 自动通过）
_auto_approve_all = False


def reset_session() -> None:
    """会话重置（/new）：清「本会话全允许」。挂起中的审批由 shutdown 杀 job
    触发 stop_event，子线程自行醒来按拒绝处理。"""
    global _auto_approve_all
    _auto_approve_all = False


def _match_permission(tool_name: str, key_value: str) -> str:
    """返回命中的 action（allow / deny / ask）；无命中返回 DEFAULT_POLICY。"""
    for rule_tool, pattern, action in PERMISSION_RULES:
        if rule_tool == tool_name and fnmatch.fnmatchcase(key_value, pattern):
            return action
    return DEFAULT_POLICY


# ============================================================
# 审批冒泡：PendingApproval 队列 + Event 挂起
# ============================================================

@dataclass
class PendingApproval:
    origin: str          # 请求方描述（"主 Agent" / "Subagent「xx」"）
    tool_name: str
    key_value: str
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


_pending_approvals: list = []
_pending_lock = threading.Lock()


def has_pending_approval() -> bool:
    with _pending_lock:
        return bool(_pending_approvals)


def _confirm_ui(origin: str, tool_name: str, key_value: str) -> bool:
    """弹确认层（必须在主循环线程调用）。复用 ask 层 UI 链，与 ask_user_question
    同基建：TUI 弹层 → 方向键 UI → 管道降级。返回是否放行；
    选「本会话全允许」时置位全局开关并放行。"""
    global _auto_approve_all
    preview = key_value if len(key_value) <= 80 else key_value[:80] + "..."
    question = [{
        "question": f"{origin} 请求执行 {tool_name}",
        "header": "权限确认",
        "options": [
            {"label": "允许", "description": preview or "（无参数预览）"},
            {"label": "拒绝", "description": "返回拒绝说明，Agent 自行换方式完成"},
            {"label": "本会话全允许", "description": "本次起本会话内的确认全部自动放行"},
        ],
        "multi_select": False,
    }]
    answers = ask.collect(question)
    label = answers[0] if answers else "拒绝"      # 取消 / 自由文本 → 保守拒绝
    if label == "允许":
        return True
    if label == "本会话全允许":
        _auto_approve_all = True
        return True
    return False


def resolve_pending_approval() -> None:
    """主循环空闲时调（agent.py 心跳）：弹队首审批，结果回填并唤醒挂起的子线程。"""
    with _pending_lock:
        pa = _pending_approvals.pop(0) if _pending_approvals else None
    if pa is None:
        return
    pa.approved = _confirm_ui(pa.origin, pa.tool_name, pa.key_value)
    pa.event.set()


def _request_approval(origin: str, tool_name: str, key_value: str,
                      stop_event=None) -> bool:
    """action=ask 的确认入口。主 Agent（stop_event=None）同线程直接弹；
    Subagent（带 stop_event）走冒泡：入队挂起，等主循环空闲时弹层。"""
    global _auto_approve_all
    if _auto_approve_all:
        return True

    if stop_event is None:                          # 主 Agent：前台轮次，直接弹
        return _confirm_ui(origin, tool_name, key_value)

    # Subagent：后台线程不能碰 UI——入队挂起，等主循环空闲弹层（审批冒泡）
    if ask.PICKER_HOOK is None and not ask._is_tty():
        return False    # 管道模式无交互 UI（后台线程更不能读 stdin）：自动拒绝
    pa = PendingApproval(origin=origin, tool_name=tool_name, key_value=key_value)
    with _pending_lock:
        _pending_approvals.append(pa)
    while not pa.event.wait(timeout=0.5):           # 循环版 wait：挂起期间可被 job_kill
        if stop_event.is_set():
            with _pending_lock:                     # 从队列摘掉自己，主循环不再弹
                try:
                    _pending_approvals.remove(pa)
                except ValueError:
                    pass
            return False
    return pa.approved


# ============================================================
# Hook：Lifecycle Extension（PreToolUse / PostToolUse 事件回调）
# ============================================================
# pre_hook(tool_name, tool_input) -> dict
#     {"decision": "pass"|"block"|"modify", ...}（modify 带 modified_input）
# post_hook(tool_name, tool_input, tool_output) -> dict
#     {"modified_output": "...", "output_delta": "..."}（替换整个 output / 追加回灌）
#
# 两个示例 hook：
#   1. inject_shebang：Pre，给 write_file 写的 .sh 脚本补 #!/bin/bash（modify）
#   2. scan_output：Post，把输出里的疑似密钥脱敏成 [xxxxx]（modified_output）

_REDACT_RE = re.compile(
    r'((?:password|api_key|apikey|secret)\s*[:=]\s*)\S+',
    re.IGNORECASE,
)


def hook_inject_shebang(tool_name: str, tool_input: dict) -> dict:
    """Pre 示例：给 write_file 写的 .sh 脚本补上 #!/bin/bash shebang 再放行。

    Hook 区别于 Permission 的杀手锏——Permission 只能 allow/deny/ask，改不了
    input；Hook 能「改写」。
    """
    if tool_name != "write_file":
        return {"decision": "pass"}
    path = tool_input.get("path", "")
    if not path.endswith(".sh"):
        return {"decision": "pass"}
    content = tool_input.get("content", "")
    if content.startswith("#!"):
        return {"decision": "pass"}
    new_input = dict(tool_input)
    new_input["content"] = "#!/bin/bash\n" + content
    return {
        "decision": "modify",
        "modified_input": new_input,
        "message": "Hook 注入：写入 .sh 脚本，已在开头补上 #!/bin/bash shebang",
    }


def hook_scan_output(tool_name: str, tool_input: dict, tool_output: str) -> dict:
    """Post 示例：把工具输出里的疑似密钥/凭证脱敏成 [xxxxx]，再补「已脱敏」提示。

    read_file 读出的明文密钥（password=hunter2）原样回灌有外传风险——Post 在
    「回灌前」把值换掉。
    """
    redacted, n = _REDACT_RE.subn(r"\1[xxxxx]", tool_output)
    if n == 0:
        return {"message": "no secrets detected", "output_delta": ""}
    delta = f"\n\nℹ️ [Post hook] 输出含疑似凭证，已将 {n} 处敏感值脱敏为 [xxxxx]。"
    return {"message": f"redacted {n}", "modified_output": redacted, "output_delta": delta}


HOOKS = {
    "PreToolUse":  [hook_inject_shebang],
    "PostToolUse": [hook_scan_output],
}


def run_hooks(event: str, tool_name: str, *args) -> dict:
    """运行某 event 下所有 hook，合并结果。
    PreToolUse: block 立即终止；modify 链式改写（后一个 hook 看到改写后的 input）。
    PostToolUse: 收集 output_delta；modified_output 取最后一个非空（替换整个 output）。"""
    aggregated = {"decision": "pass", "messages": [], "output_delta": "", "modified_output": None}
    current_input = args[0] if (event == "PreToolUse" and args) else None
    for hook in HOOKS.get(event, []):
        try:
            hook_args = (current_input,) if event == "PreToolUse" else args
            result = hook(tool_name, *hook_args)
        except Exception as e:
            result = {"message": f"hook 异常: {e}"}

        if event == "PreToolUse":
            decision = result.get("decision", "pass")
            if decision == "block":
                aggregated["decision"] = "block"
                aggregated["block_message"] = result.get("message", "hook 拦截")
                break
            if decision == "modify" and result.get("modified_input") is not None:
                current_input = result["modified_input"]
                aggregated["decision"] = "modify"
        if event == "PostToolUse" and result.get("modified_output") is not None:
            aggregated["modified_output"] = result["modified_output"]
        if result.get("message"):
            aggregated["messages"].append(result["message"])
        if result.get("output_delta"):
            aggregated["output_delta"] += result["output_delta"]
    if aggregated["decision"] == "modify":
        aggregated["modified_input"] = current_input
    return aggregated


# ============================================================
# dispatch_tool：串接两层 Runtime Control 的调度入口
# ============================================================

def dispatch_tool(tool_name: str, tool_input: dict, fn,
                  origin: str = "主 Agent", stop_event=None) -> str:
    """
    demo6 核心调度入口：Pre hooks → Permission → 原始工具 → Post hooks。

    fn 是路由表里查到的工具函数（由共用循环传入——主/子的函数表不同）；
    stop_event 由子循环传入（冒泡挂起时可被 job_kill 唤醒）。
    返回字符串（与裸工具一致），拒绝/错误信息也字符串化回灌大模型。
    """
    # ---- 1. PreToolUse hooks（观察 / 拦截 / 改写 input）----
    pre = run_hooks("PreToolUse", tool_name, tool_input)
    if pre["decision"] == "block":
        return f"[Hook 拦截] {pre.get('block_message', 'PreToolUse hook 拦截')}"
    if pre["decision"] == "modify":
        tool_input = pre["modified_input"]

    # ---- 2. Permission（主 Agent 直接弹层确认，Subagent 冒泡）----
    key_field = _TOOL_KEY_FIELD.get(tool_name, "")
    key_value = str(tool_input.get(key_field, ""))
    action = _match_permission(tool_name, key_value)
    if action == "deny":
        return f"[Permission 拒绝] {tool_name}({key_value!r}) 命中 deny 规则"
    if action == "ask" and not _request_approval(origin, tool_name, key_value, stop_event):
        return (f"[Permission 拒绝] {origin} 的 {tool_name}({key_value!r}) 未获用户允许——"
                "请换不需要审批的方式完成任务，或在报告中说明这一步没有做")

    # ---- 3. 执行原始工具（Pre 改写后的 input）----
    try:
        output = str(fn(**tool_input))
    except Exception as e:
        return f"[错误] 工具 {tool_name} 执行失败: {e}"

    # ---- 4. PostToolUse hooks（观察 / 替换 / 补改 output）----
    post = run_hooks("PostToolUse", tool_name, tool_input, output)
    if post.get("modified_output") is not None:
        output = post["modified_output"]
    if post.get("output_delta"):
        output += post["output_delta"]
    return output
