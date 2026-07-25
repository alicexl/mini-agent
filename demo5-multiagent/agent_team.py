#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 - 多 Agent 轴（二）：Team 持久项目组

公式：demo5 = base × 多 Agent

在 demo1-react（base）基础上叠加 Team 模式：
    + Agent —— 持久对象（name/role/messages/inbox）
    + Team —— 协调器（recruit/send/broadcast/dismiss）
    + plan_team —— LLM 自主判断是否需要团队，需要则设计角色任务 + 招募 + 执行

单文件 5 个 Part：客户端 / 工具定义 / 工具实现 + Team 基础设施 / 主循环 / 入口

启动：
    python agent_team.py
"""

import os
import random
import re
import subprocess

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================

API_KEY = ""
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"
MODEL          = "glm-5.2"
API_TIMEOUT_MS = 3000000


def load_config() -> dict:
    return {
        "api_key":    os.environ.get("ANTHROPIC_API_KEY") or API_KEY,
        "base_url":   BASE_URL,
        "model":      MODEL,
        "timeout_ms": API_TIMEOUT_MS,
    }


def ensure_config() -> dict:
    config = load_config()
    if config["api_key"]:
        return config
    print("=" * 60)
    print("检测到尚未配置 API Key，请输入（仅本次运行有效）")
    print("如需持久化：请改 agent_team.py 顶部的 API_KEY 变量")
    print("=" * 60)
    api_key = input("\n请输入 API Key: ").strip()
    if not api_key:
        raise SystemExit("未提供 API Key，退出")
    config["api_key"] = api_key
    return config


client: Anthropic = None  # type: ignore


def init_client() -> None:
    global client
    config = ensure_config()
    client = Anthropic(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=config["timeout_ms"] / 1000.0,
    )


# ============================================================
# Part 2: 工具定义（demo1 四件套 + plan_team）
# ============================================================

TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可用于文件操作、系统命令等",
        "input_schema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "要执行的 shell 命令"}},
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取指定路径文件内容，返回文本",
        "input_schema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "要读取的文件路径"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "写入文件，不存在则创建，存在则覆盖",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": "精确替换文件中的一段文本。比 write_file 整文件覆写更精细。",
        "input_schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string",  "description": "要编辑的文件路径"},
                "old":         {"type": "string",  "description": "要替换的原文本（必须精确匹配）"},
                "new":         {"type": "string",  "description": "替换为的新文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配处（默认 false）"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "plan_team",
        "description": (
            "为复杂任务组建多角色团队。你只需要规划角色（role），"
            "任务由用户原始输入驱动，不需要你拆。\n\n"
            "**使用时机**：需要多角色分工的复杂任务。"
            "简单任务直接用其它本地工具完成。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "用户原始任务（可选，传给第一个成员）"},
                "members": {
                    "type": "array",
                    "description": "团队成员列表，每项包含 name、role",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "成员英文名"},
                            "role": {"type": "string", "description": "角色描述（简短，10字以内）"},
                        },
                        "required": ["name", "role"],
                    },
                },
            },
            "required": ["members"],
        },
    },
]


# ============================================================
# Part 3: 工具实现 + Team 基础设施 + 路由表
# ============================================================

def execute_bash(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        output = []
        if result.stdout: output.append(result.stdout)
        if result.stderr: output.append(f"[stderr] {result.stderr}")
        if result.returncode != 0: output.append(f"[exit code: {result.returncode}]")
        return "\n".join(output) if output else "[命令执行成功，无输出]"
    except subprocess.TimeoutExpired:
        return "[错误] 命令执行超时（60 秒）"
    except Exception as e:
        return f"[错误] 命令执行失败: {e}"


def read_file(path: str) -> str:
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        max_length = 20000
        if len(content) > max_length:
            content = content[:max_length] + f"\n\n... [内容已截断，共 {len(content)} 字符]"
        return content
    except UnicodeDecodeError:
        return "[错误] 文件不是有效的文本文件或编码不支持"
    except Exception as e:
        return f"[错误] 读取文件失败: {e}"


def write_file(path: str, content: str) -> str:
    try:
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[成功] 文件已写入: {path} ({len(content)} 字符)"
    except Exception as e:
        return f"[错误] 写入文件失败: {e}"


def edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
    try:
        if not os.path.exists(path): return f"[错误] 文件不存在: {path}"
        if not old: return "[错误] old 不能为空"
        with open(path, "r", encoding="utf-8") as f: content = f.read()
        occurrences = content.count(old)
        if occurrences == 0: return f"[错误] 未找到匹配文本，请用 read_file 确认精确内容"
        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f: f.write(new_content)
        which = f"全部 {occurrences} 处" if replace_all else f"第 1 处（共 {occurrences} 处）"
        return f"[成功] {path} 替换 {which}"
    except Exception as e:
        return f"[错误] 编辑文件失败: {e}"


# ---- plan_team（demo5 新增） ----
def plan_team(members: list, task: str = "") -> str:
    """
    启动多角色团队协作（demo5 新增）。

    LLM 只拆角色，任务内嵌在 role 描述中 → recruit → 消息队列执行 → 返回结果。
    """
    if not members:
        return "[Team] 成员列表为空"

    team = Team()
    for m in members:
        team.recruit(m["name"], m.get("role", "团队成员"))

    # recruit 完成后更新所有 Agent 的 system prompt（registry 完整了）
    for name, agent in team.agents.items():
        members_list = "\n".join(f"  {n}: {r}" for n, r in team.registry.items())
        agent.system_prompt = (
            f"你是 {name}\n\n"
            f"职责: {team.registry[name]}\n\n"
            f"团队成员:\n{members_list}\n\n"
            f"规则:\n"
            f"1. 能完成就自己完成\n"
            f"2. 不能完成就在回复最开头用 [send: 成员名] 转交最合适的人"
        )

    # 随机挑一个——不匹配会 [send:] 转交，展示任务流转
    first = random.choice(members)
    msg = f"你的角色是：{first['role']}。"
    if task:
        msg += f"\n\n任务：{task}"
    team.send("用户", first["name"], msg)

    print(f"\n  [Team] 消息队列启动：{[m['name'] for m in members]}")
    result = team.run(members)
    return result


# ---- Team 基础设施（Agent / Team 类） ----

STEP_MAX_ITERATIONS = 20


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


class Event:
    """消息队列中的事件"""
    def __init__(self, sender: str, receiver: str, message: str):
        self.sender   = sender
        self.receiver = receiver
        self.message  = message


class Agent:
    """持久化 Agent——有 name/role、可路由消息给其他成员。"""

    def __init__(self, name: str, role: str, registry: dict = None):
        self.name     = name
        self.role     = role
        self.messages: list = []

        members = "\n".join(f"  {n}: {r}" for n, r in (registry or {}).items())
        self.system_prompt = (
            f"你是 {name}\n\n"
            f"职责: {role}\n\n"
            f"团队成员:\n{members}\n\n"
            f"规则:\n"
            f"1. 能完成就自己完成\n"
            f"2. 不能完成就在回复最开头用 [send: 成员名] 转交最合适的人"
        )

    def chat(self, event: Event = None) -> str:
        tools = [t for t in TOOLS if t["name"] != "plan_team"]  # 去 plan_team 防递归
        if event:
            self.messages.append({"role": "user", "content":
                f"[来自 {event.sender} 的消息] {event.message}"})
        for _ in range(STEP_MAX_ITERATIONS):
            response = client.messages.create(
                model=MODEL, max_tokens=4096,
                system=self.system_prompt, tools=tools, messages=self.messages,
            )
            if response.stop_reason != "tool_use":
                result = "".join(b.text for b in response.content if b.type == "text")
                self.messages.append({"role": "assistant", "content": response.content})
                return result
            self.messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type != "tool_use": continue
                t_name = block.name; args = block.input or {}
                print(f"    [{self.name}] {t_name}({_preview(str(args), 60)})")
                fn = AVAILABLE_FUNCTIONS.get(t_name)
                if fn:
                    try: r = str(fn(**args))
                    except Exception as e: r = f"[错误] {e}"
                else: r = f"[错误] 未知工具: {t_name}"
                print(f"    [{self.name}] → {_preview(r, 80)}")
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": r})
            self.messages.append({"role": "user", "content": tool_results})
        return "[Agent 未在限定轮数内完成]"


class Team:
    """消息队列驱动的多 Agent 协调器"""

    def __init__(self):
        self.agents: dict = {}
        self.registry: dict = {}    # {name: role}
        self.queue: list = []       # Event 队列

    def recruit(self, name: str, role: str) -> Agent:
        self.registry[name] = role
        agent = Agent(name, role, registry=self.registry)
        self.agents[name] = agent
        print(f"  [Team] 招募 {name}（{role}）")
        return agent

    def send(self, sender: str, receiver: str, message: str) -> None:
        self.queue.append(Event(sender, receiver, message))

    def run(self, members: list) -> str:
        """事件循环：pop → agent.chat → 路由给其他成员"""
        MAX_STEPS = len(members) * 3  # 每人最多被唤醒 3 次
        last_result = ""
        tried = set()
        for _ in range(MAX_STEPS):
            # 队列空 → 兜底：随机找个没试过的成员再发一次
            if not self.queue:
                remaining = [m for m in members if m["name"] not in tried]
                if remaining:
                    target = random.choice(remaining)
                    self.send("系统", target["name"], "请检查是否有未完成的工作。")
                    continue
                break
            event = self.queue.pop(0)
            name = event.receiver
            if name not in self.agents:
                continue
            print(f"\n  [event] {event.sender} → {name}：{_preview(event.message, 80)}")
            result = self.agents[name].chat(event)
            print(f"  [event] {name} 完成：{_preview(result, 100)}")
            last_result = result
            tried.add(name)

            # 检查路由指令：[send: name]
            m_send = re.match(r"\[send:\s*(\w+)\]", result)
            if m_send:
                target = m_send.group(1)
                if target in self.agents:
                    rest = result[m_send.end():].strip()
                    print(f"  [send] {name} → {target}")
                    self.send(name, target, rest or event.message)
                    continue

            # 无路由指令 → 不继续转发，等队列自然空
        return last_result


# 路由表
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "edit":         edit,
    "plan_team":    plan_team,
}


# ============================================================
# Part 4: Agent 主循环
# ============================================================
MAX_ITERATIONS = 30

def _print_messages(messages: list) -> None:
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text": parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use": parts.append(f"[调用工具 {block.get('name')}]")
                else:
                    t = getattr(block, "type", None)
                    if t == "text": parts.append(getattr(block, "text", ""))
                    elif t == "tool_use": parts.append(f"[调用工具 {getattr(block, 'name', '')}]")
            content = "\n".join(parts)
        print(f"  [{i}] {msg.get('role', '?'):<9}: {_preview(content)}")


def run_agent(user_input: str, verbose: bool = True) -> str:
    system_prompt = "你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。"
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"第 {loop_idx} 轮 ReAct 循环")
            print(f"{'=' * 60}")
            _print_messages(messages)

        response = client.messages.create(
            model=MODEL, max_tokens=4096,
            system=system_prompt, tools=TOOLS, messages=messages,
        )

        if verbose:
            print(f"\n[LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text":
                    preview = block.text[:80] + ("..." if len(block.text) > 80 else "")
                    print(f"  - text      : {preview}")
                elif block.type == "tool_use":
                    print(f"  - tool_use  : {block.name}({block.input})")

        if response.stop_reason != "tool_use":
            if verbose:
                print(f"\n[循环结束] 大模型判断任务完成，退出循环")
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            name = block.name
            args = block.input or {}
            fn = AVAILABLE_FUNCTIONS.get(name)
            if fn is None:
                result = f"[错误] 未知工具: {name}"
            else:
                if verbose:
                    print(f"\n[执行工具] {name}({args})")
                try:
                    result = str(fn(**args))
                except Exception as e:
                    result = f"[错误] 工具 {name} 执行失败: {e}"

            if verbose:
                preview = str(result)[:200] + ("..." if len(str(result)) > 200 else "")
                print(f"[工具结果] {preview}")

            tool_results.append({
                "type": "tool_result", "tool_use_id": block.id, "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

    return "[错误] 超过最大循环次数（{}），可能陷入死循环".format(MAX_ITERATIONS)


# ============================================================
# 交互式入口
# ============================================================

def main():
    init_client()
    print("=" * 60)
    print("Demo5 (Team) 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"工具:   {', '.join(t['name'] for t in TOOLS)}")
    print("=" * 60)
    print("其中 `plan_team` 可启动多角色团队协作。")
    print("quit / exit 退出")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n用户: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break
        if not user_input: continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("再见！")
            break
        try:
            final = run_agent(user_input, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
