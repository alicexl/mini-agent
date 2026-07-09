#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 - Subagent 的协作与编排（Team 模式）

demo4 的 subagent 是"临时工"——一次性、无记忆、不能通信。现实里更多场景是
"正式员工"：有名字、有角色、有记忆、能互相通信，一直活到整个项目结束才解散。

demo5 在 demo4 基础上做一次**大改造**：

    - 减法：去掉 demo4 的 subagent 工具
        · 临时工模式（一次性函数调用）已经演不下去了
    - 加法：新增 Agent 类 + Team 类
        · **Agent 类**：从"一次性函数"升级为"持久化对象"
            - self.name / self.role          → 固定身份
            - self.messages                  → 长期记忆（跨多轮 chat 累积）
            - self.inbox                     → 收件箱（其他 agent 发来的消息）
            - chat(task)                     → 消化 inbox → 执行任务 → ReAct 循环
            - receive(sender, message)       → 被其他 agent 调用，往 inbox 加消息
        · **Team 类**：协调多个 Agent
            - recruit(name, role)            → 招募成员
            - send(a, b, msg)                → 一对一通信
            - broadcast(sender, msg)         → 群发（成员完成任务后通报全员）
            - dismiss()                      → 项目结束，解散团队
            - run_team(user_input)           → 事件驱动协作入口
              · 任务状态机：pending → reviewing → passed/failed
              · 质检员持续监听，任务一完成立即质检
              · 单任务最多 3 次质检，3 次不过 = failed
              · 所有任务进入终止态后统计项目状态，解散

单文件按 7 个 Part 组织：
    Part 1: LLM 客户端初始化（沿用 demo1-4）
    Part 2: 本地工具定义 + 实现（沿用 demo4，去掉 subagent 工具）
    Part 3: Rules 加载器（沿用 demo3，启动时加载；agent 级注入留给后续 demo）
    Part 4: 记忆系统（沿用 demo2，只保留 append）
    Part 5: Agent 类（核心新增）
    Part 6: Team 类（核心新增）
    Part 7: 交互式入口

启动：
    python agent.py
"""

import os
import sys
import json
import random
import subprocess
from datetime import datetime

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================

# ↓↓↓ 只需改这一行 ↓↓↓
API_KEY = ""

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL          = "glm-5.2"                                  # 模型名
API_TIMEOUT_MS = 3000000                                    # 单次请求超时（毫秒），50 分钟


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
    print("如需持久化：请改 agent.py 顶部的 API_KEY 变量")
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
# Part 2: 本地工具定义 + 实现
# ============================================================
# demo5 沿用 demo4 的 6 个本地工具，**去掉 subagent 工具**：
#   · execute_bash / read_file / write_file / add / multiply / weather
# 协调工作交给 Team 类（外部编排），不再靠 LLM 调 subagent 工具

LOCAL_TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可用于文件操作、系统命令、grep 搜索等",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "读取指定路径文件内容，返回文本",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要读取的文件路径"},
            },
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
        "name": "add",
        "description": "计算两个数字的和",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个加数"},
                "b": {"type": "number", "description": "第二个加数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "multiply",
        "description": "计算两个数字的乘积",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个乘数"},
                "b": {"type": "number", "description": "第二个乘数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "weather",
        "description": "查询指定城市的天气（演示用，返回写死/随机数据）",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，如 北京 / 上海"},
            },
            "required": ["city"],
        },
    },
]


def execute_bash(command: str) -> str:
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=60,
        )
        output = []
        if result.stdout:
            output.append(result.stdout)
        if result.stderr:
            output.append(f"[stderr] {result.stderr}")
        if result.returncode != 0:
            output.append(f"[exit code: {result.returncode}]")
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
        if len(content) > 10000:
            content = content[:10000] + f"\n\n... [内容已截断，共 {len(content)} 字符]"
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


def add(a, b) -> str:
    return f"{a} + {b} = {a + b}"


def multiply(a, b) -> str:
    return f"{a} × {b} = {a * b}"


_WEATHER_DB = {
    "北京": ("晴", 25),
    "上海": ("多云", 22),
    "广州": ("雷阵雨", 30),
    "深圳": ("多云", 29),
    "杭州": ("晴", 24),
}


def weather(city: str) -> str:
    if city in _WEATHER_DB:
        condition, temp = _WEATHER_DB[city]
    else:
        condition = random.choice(["晴", "多云", "阴", "小雨", "大雨", "雷阵雨"])
        temp = random.randint(10, 35)
    return f"{city} 今天天气：{condition}，气温 {temp}°C"


LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "add":          add,
    "multiply":     multiply,
    "weather":      weather,
}


# ============================================================
# Part 3: Rules 加载器（沿用 demo3）
# ============================================================
# Rules 是项目级行为规范——不改代码、不改工具，通过"上下文约束"约束大模型输出。
# demo5 启动时 load_rules() 检查文件状态并打印；**暂不注入到每个 Agent**
# （agent 级 rules 注入留给后续 demo）。文件就位后，未来可在 build_agent_system_prompt
# 或 _plan_team 里把它拼到 prompt 里。

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent", "rules.md")


def load_rules() -> str:
    if not os.path.exists(RULES_FILE):
        return ""
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"[警告] 读取 rules 失败: {e}")
        return ""


# ============================================================
# Part 4: 记忆系统（沿用 demo2，只保留 append）
# ============================================================

MEMORY_FILE         = "agent_memory.md"
MEMORY_WINDOW_LINES = 50


def append_memory(task: str, result: str) -> None:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    preview = (result or "").strip()
    if len(preview) > 500:
        preview = preview[:500] + "..."
    entry = (
        f"\n## [{timestamp}]\n"
        f"**任务**: {task}\n"
        f"**结果**: {preview}\n"
    )
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[警告] 写入记忆文件失败: {e}")


# ============================================================
# Part 5: Agent 类（核心新增）
# ============================================================
# 与 demo4 的 _run_subagent 函数相比，Agent 是一个**持久化对象**：
#   · self.name / self.role          → 固定身份（demo4 是临时拼角色）
#   · self.messages                  → 长期记忆（demo4 是一次用完即丢）
#   · self.inbox                     → 收件箱（demo4 没有通信机制）
#
# 一个 Agent 可以被 chat() 多次——每次 chat 消化 inbox → 把新消息和任务
# 追加到 messages → 走一个完整的 ReAct 循环 → 返回响应。messages 跨多次
# chat 累积，构成"我之前做了什么、别人给我发了什么"的长期记忆。

STEP_MAX_ITERATIONS = 10


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def build_agent_system_prompt(name: str, role: str) -> str:
    """单个 Agent 的角色化 system prompt"""
    return (
        f"你是团队成员「{name}」，你的角色是：**{role}**。\n"
        f"请专注完成交给你的任务。如果收到其他成员的消息，把它当作上下文输入。\n"
        f"做完后用一两句话汇报结果。"
    )


class Agent:
    """
    一个有身份、有记忆、有收件箱的 Agent。

    生命周期：
        创建（team.recruit）→ 多次 chat → 项目结束（team.dismiss）→ 销毁
    """

    def __init__(self, name: str, role: str, tools: list, local_fns: dict,
                 verbose: bool = True):
        self.name        = name
        self.role        = role
        self.tools       = tools                              # 可用工具列表
        self.local_fns   = local_fns                          # 本地函数字典
        self.verbose     = verbose
        self.indent      = "    "                             # 打印缩进，让轨迹可视化

        self.inbox: list[tuple[str, str]] = []                # 收件箱：[(sender, message), ...]
        self.messages: list[dict] = []                        # 长期记忆（跨多次 chat 累积）
        self.system_prompt = build_agent_system_prompt(name, role)

        if verbose:
            print(f"{self.indent}[Agent · {name}] 已创建，角色：{role}")

    # ------------------------------------------------------------
    # 通信：被其他 Agent（或 Team 编排器）调用
    # ------------------------------------------------------------
    def receive(self, sender: str, message: str) -> None:
        """往 inbox 里塞一条消息。下次 chat() 时会被消化。"""
        self.inbox.append((sender, message))
        if self.verbose:
            print(f"{self.indent}[Agent · {self.name}] 📨 收到来自 {sender} 的消息："
                  f"{_preview(message, 80)}")

    # ------------------------------------------------------------
    # 核心方法：一次 chat = 消化 inbox → 执行任务 → 走 ReAct
    # ------------------------------------------------------------
    def chat(self, task=None) -> str:
        """
        Args:
            task: 本次要做的任务。可以是 None——仅消化 inbox 不接新任务。

        Returns:
            Agent 本次 chat 的最终回复文本
        """
        # 1) 消化 inbox：把所有未读消息包装成 user message 灌进 messages
        if self.inbox:
            for sender, msg in self.inbox:
                wrapped = f"[来自 {sender} 的消息] {msg}"
                self.messages.append({"role": "user", "content": wrapped})
                if self.verbose:
                    print(f"{self.indent}[Agent · {self.name}] 消化收件箱："
                          f"{_preview(wrapped, 100)}")
            self.inbox.clear()

        # 2) 追加本次任务
        if task:
            self.messages.append({"role": "user", "content": task})
            if self.verbose:
                print(f"{self.indent}[Agent · {self.name}] 接到任务："
                      f"{_preview(task, 100)}")

        # 3) 走 ReAct 循环（与 demo4 的 _react_loop 同构）
        return self._react_loop()

    # ------------------------------------------------------------
    # ReAct 循环——和 demo4 主循环同构，只是挂到 Agent 实例上
    # ------------------------------------------------------------
    def _react_loop(self) -> str:
        if self.verbose:
            print(f"{self.indent}{'─' * 50}")
            print(f"{self.indent}[Agent · {self.name}] 启动 ReAct 循环")
            print(f"{self.indent}{'─' * 50}")

        response = None
        for i in range(1, STEP_MAX_ITERATIONS + 1):
            if response is None:
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=4096,
                    system=self.system_prompt,
                    tools=self.tools,
                    messages=self.messages,
                )

            if response.stop_reason != "tool_use":
                result = "".join(b.text for b in response.content if b.type == "text")
                if self.verbose:
                    print(f"{self.indent}[Agent · {self.name}] [迭代 {i} 完成] "
                          f"{_preview(result, 120)}")
                # 把最终回复也加入记忆（assistant turn）
                self.messages.append({"role": "assistant", "content": response.content})
                return result

            # 打印思考文本
            if self.verbose:
                for block in response.content:
                    if block.type == "text" and block.text.strip():
                        print(f"{self.indent}  [LLM 思考] {_preview(block.text, 200)}")

            self.messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                name = block.name
                args = block.input or {}
                if self.verbose:
                    print(f"{self.indent}  [LLM] {name}({_preview(args, 80)})")

                if name in self.local_fns:
                    if self.verbose:
                        print(f"{self.indent}  [工具 · 本地] {name}")
                    try:
                        result = str(self.local_fns[name](**args))
                    except Exception as e:
                        result = f"[错误] 本地工具 {name} 执行失败: {e}"
                else:
                    result = f"[错误] 未知工具: {name}"

                if self.verbose:
                    print(f"{self.indent}  [结果] {_preview(result, 120)}")
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })
            self.messages.append({"role": "user", "content": tool_results})

            response = None

        return f"[Agent · {self.name}] ReAct 循环未在 {STEP_MAX_ITERATIONS} 轮内完成"


# ============================================================
# Part 6: Team 类（核心新增）
# ============================================================
# Team 是 Agent 的协调器——demo5 的"项目组"。
# 它提供 4 个核心动作：
#   · recruit(name, role)        招募成员
#   · send(a, b, msg)            一对一通信（依赖注入 / 质检反馈打回）
#   · broadcast(sender, msg)     群发（成员完成任务后通报全员）
#   · dismiss()                  解散团队
#
# run_team(user_input) 采用**事件驱动**调度（单线程模拟，避免 Agent.messages
# 的线程安全问题）：
#   1) LLM 当项目经理拆任务（强制含 1 名质检员）+ 注入 team 级 rules
#   2) recruit 全员
#   3) 事件循环——每轮按优先级处理：
#      优先级 1：待质检任务 → 质检员立即介入（**质检员一直在监听**）
#      优先级 2：待重做任务 → 立即重做（assignee 收到反馈）
#      优先级 3：可启动新任务 → 派发（依赖全 passed 才启动）
#      优先级 4：死锁检查 → 依赖了 failed 的 pending → 标记 failed
#   4) 所有任务进入终止态（passed / failed）→ 统计项目状态 → dismiss
#
# 任务状态机：
#   pending → reviewing → passed (终态)
#              ↑     ↘ failed (终态，3 次质检不过)
#              redoing


# 任务状态机
TASK_PENDING   = "pending"    # 等待执行（依赖未满足）
TASK_REVIEWING = "reviewing"  # 执行完，待质检员验收
TASK_REDOING   = "redoing"    # 质检未过，待重做
TASK_PASSED    = "passed"     # 质检通过（终态）
TASK_FAILED    = "failed"     # 3 次质检不过，或依赖失败（终态）

TERMINAL_STATES = {TASK_PASSED, TASK_FAILED}
MAX_REVIEW_ATTEMPTS = 3       # 单任务最多质检 3 次


class _Task:
    """一个任务的状态机封装。"""
    def __init__(self, task_dict: dict):
        self.assignee: str       = task_dict["assignee"]
        self.task_text: str      = task_dict["task"]
        self.depends_on: list    = task_dict.get("depends_on", [])
        self.status: str         = TASK_PENDING
        self.review_attempts: int = 0
        self.result: str         = ""

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATES


class Team:
    """一组互相协作的 Agent + 一个外部编排器。"""

    def __init__(self, tools: list, local_fns: dict, verbose: bool = True):
        self.tools     = tools
        self.local_fns = local_fns
        self.verbose   = verbose
        self.agents: dict[str, Agent] = {}
        self.rules     = load_rules()                  # Team 级 rules，注入到项目经理 prompt

        if verbose:
            print(f"\n{'=' * 60}")
            print(f"Team 已创建（尚未招募成员）")
            print(f"{'=' * 60}")

    # ------------------------------------------------------------
    # 4 个核心动作
    # ------------------------------------------------------------
    def recruit(self, name: str, role: str) -> Agent:
        """招募一个新成员"""
        if name in self.agents:
            print(f"[Team] 警告：成员 {name} 已存在，跳过")
            return self.agents[name]
        agent = Agent(
            name=name, role=role,
            tools=self.tools, local_fns=self.local_fns,
            verbose=self.verbose,
        )
        self.agents[name] = agent
        return agent

    def send(self, sender: str, receiver: str, message: str) -> None:
        """一对一通信：sender → receiver（依赖注入、质检反馈打回）"""
        if receiver not in self.agents:
            print(f"[Team] 错误：接收方 {receiver} 不存在")
            return
        if sender not in self.agents:
            print(f"[Team] 警告：发送方 {sender} 不在团队中（仍允许）")
        self.agents[receiver].receive(sender, message)

    def broadcast(self, sender: str, message: str) -> None:
        """群发：sender → 所有其他成员（成员完成任务后通报全员）"""
        for name, agent in self.agents.items():
            if name == sender:
                continue
            agent.receive(sender, message)

    def dismiss(self) -> None:
        """项目结束，解散团队"""
        if self.verbose:
            print(f"\n{'=' * 60}")
            print(f"Team 解散——成员销毁，记忆丢失")
            for name in self.agents:
                print(f"  · {name}（{self.agents[name].role}）已离职")
            print(f"{'=' * 60}")
        self.agents.clear()

    # ------------------------------------------------------------
    # 事件驱动协作入口（任务状态机 + 单线程事件循环）
    # ------------------------------------------------------------
    def run_team(self, user_input: str) -> dict:
        """
        事件驱动调度：
          Step 1: 项目经理拆任务（强制含质检员）
          Step 2: recruit 全员
          Step 3: 事件循环——质检优先，重做次之，新任务最后
          Step 4: 所有任务进入终止态 → 统计项目状态 → dismiss
        """
        if self.verbose:
            print(f"\n[run_team] 用户输入：{user_input}")

        # Step 1: 项目经理拆任务
        plan = self._plan_team(user_input)
        reviewer = self._find_reviewer(plan["members"])
        if not reviewer:
            raise RuntimeError(
                "项目经理未分配质检员——members 里必须有 1 名 role 含「质检员/验收员/review」的成员"
            )
        if self.verbose:
            print(f"\n[run_team] 项目经理给出的拆解：")
            print(f"  成员清单（👑 = 质检员）：")
            for m in plan["members"]:
                tag = "  👑" if m["name"] == reviewer else ""
                print(f"    · {m['name']}（{m['role']}）{tag}")
            print(f"  任务序列：")
            for t in plan["tasks"]:
                deps = t.get("depends_on", [])
                dep_str = f" ← 依赖 {deps}" if deps else ""
                print(f"    · [{t['assignee']}] {t['task']}{dep_str}")

        # Step 2: recruit
        if self.verbose:
            print(f"\n[run_team] 开始招募团队成员...")
        for m in plan["members"]:
            self.recruit(m["name"], m["role"])

        # Step 3: 事件循环
        tasks = [_Task(t) for t in plan["tasks"]]
        task_by_assignee = {t.assignee: t for t in tasks}
        self._event_loop(tasks, task_by_assignee, reviewer)

        # Step 4: 统计 + dismiss
        passed = [t for t in tasks if t.status == TASK_PASSED]
        failed = [t for t in tasks if t.status == TASK_FAILED]
        if self.verbose:
            print(f"\n{'=' * 60}")
            print(f"[项目结束] ✅ 通过 {len(passed)} 个 / ❌ 失败 {len(failed)} 个")
            for t in tasks:
                mark = "✅" if t.status == TASK_PASSED else "❌"
                print(f"  {mark} [{t.assignee}] "
                      f"(质检 {t.review_attempts} 次)：{_preview(t.result, 80)}")
            print(f"{'=' * 60}")

        results = {t.assignee: t.result for t in tasks}
        results[reviewer] = f"✅ 通过 {len(passed)} / ❌ 失败 {len(failed)}"
        self.dismiss()
        return results

    # ------------------------------------------------------------
    # 事件循环——单线程模拟"质检员一直在监听"
    # ------------------------------------------------------------
    def _event_loop(self, tasks: list, task_by_assignee: dict, reviewer: str) -> None:
        """
        每轮按优先级处理一个事件：
          优先级 1：待质检任务（reviewing）→ 质检员立即介入
          优先级 2：待重做任务（redoing）→ 立即重做
          优先级 3：可启动新任务（pending 且依赖全 passed）→ 派发
          优先级 4：死锁检查（依赖 failed 的 pending → 标记 failed）
        终止：所有任务进入 TERMINAL_STATES
        """
        loop_count = 0
        while not all(t.terminal for t in tasks):
            loop_count += 1

            # 优先级 1：质检员一直在监听——拿到 reviewing 立即质检
            reviewable = [t for t in tasks if t.status == TASK_REVIEWING]
            if reviewable:
                t = reviewable[0]
                if self.verbose:
                    print(f"\n[loop {loop_count}] 📨 质检员捕获到 {t.assignee} 完成事件，立即质检")
                self._review_one_task(t, reviewer)
                continue

            # 优先级 2：重做任务
            redoing = [t for t in tasks if t.status == TASK_REDOING]
            if redoing:
                t = redoing[0]
                if self.verbose:
                    print(f"\n[loop {loop_count}] 🔁 {t.assignee} 开始重做（第 {t.review_attempts + 1} 次执行）")
                t.result = self.agents[t.assignee].chat(
                    f"请按质检反馈重做。原始任务：{t.task_text}"
                )
                self.broadcast(t.assignee,
                               f"{t.assignee} 重做完成：{t.result}")
                t.status = TASK_REVIEWING
                continue

            # 优先级 3：可启动的新任务（依赖全 passed）
            runnable = [
                t for t in tasks
                if t.status == TASK_PENDING
                and all(
                    task_by_assignee[dep].status == TASK_PASSED
                    for dep in t.depends_on
                    if dep in task_by_assignee
                )
            ]
            if runnable:
                t = runnable[0]
                if self.verbose:
                    print(f"\n[loop {loop_count}] 🚀 启动新任务 [{t.assignee}]：{_preview(t.task_text, 60)}")
                # 注入依赖结果
                for dep in t.depends_on:
                    if dep in task_by_assignee:
                        dep_result = task_by_assignee[dep].result
                        self.send(dep, t.assignee, f"{dep} 的结果：{dep_result}")
                # 派任务（chat 内部走 _react_loop）
                t.result = self.agents[t.assignee].chat(t.task_text)
                # 完成后广播全员
                self.broadcast(t.assignee,
                               f"{t.assignee} 完成任务「{t.task_text}」：{t.result}")
                t.status = TASK_REVIEWING
                continue

            # 优先级 4：死锁检查——依赖了 failed 的 pending → 标记 failed
            stuck = False
            for t in tasks:
                if t.status == TASK_PENDING:
                    failed_deps = [
                        dep for dep in t.depends_on
                        if dep in task_by_assignee
                        and task_by_assignee[dep].status == TASK_FAILED
                    ]
                    if failed_deps:
                        t.status = TASK_FAILED
                        t.result = f"[依赖失败] 依赖的 {failed_deps} 未通过质检，本任务无法执行"
                        stuck = True
                        if self.verbose:
                            print(f"\n[loop {loop_count}] ⚠️ [{t.assignee}] 依赖失败，标记 failed")
            if stuck:
                continue

            # 真死锁（理论上不该到这）——强制结束
            if self.verbose:
                print(f"\n[loop {loop_count}] ⚠️ 调度死锁，强制终止剩余任务")
            for t in tasks:
                if not t.terminal:
                    t.status = TASK_FAILED
                    t.result = t.result or "[调度死锁]"
            break

    # ------------------------------------------------------------
    # 质检员对一个任务做一次质检（走 ReAct）
    # ------------------------------------------------------------
    def _review_one_task(self, task: "_Task", reviewer: str) -> None:
        """对单个 reviewing 状态的任务跑一次质检。结果：passed / redoing / failed"""
        task.review_attempts += 1
        attempt = task.review_attempts

        # 质检员接收完成通知（receive → 下次 chat 消化）
        self.agents[reviewer].receive(
            task.assignee,
            f"[待验收任务（第 {attempt} 次质检）]\n"
            f"任务：{task.task_text}\n"
            f"执行结果：{task.result}"
        )
        # reviewer 走 ReAct——可以用 read_file / execute_bash 复查
        verdict_text = self.agents[reviewer].chat(
            "请严格验收上条任务。可以调用 read_file / execute_bash 实际复查。\n"
            "严格只输出 JSON（不要 markdown 代码块、不要任何解释）：\n"
            '{"pass": true|false, "feedback": "若不通过，说明具体怎么改"}'
        )
        # 直接解析——prompt 已要求纯 JSON。解析失败视为不通过，原文当 feedback
        try:
            v = json.loads(verdict_text.strip())
            passed = bool(v.get("pass") is True)
            feedback = v.get("feedback") or ""
        except json.JSONDecodeError:
            passed = False
            feedback = f"[质检员输出不是合法 JSON，默认不通过] 原文：{verdict_text}"

        if passed:
            task.status = TASK_PASSED
            self.broadcast(reviewer,
                           f"✅ {task.assignee} 通过质检（第 {attempt} 次）")
            if self.verbose:
                print(f"[review] ✅ {task.assignee} 通过（第 {attempt} 次）")
            return

        # 不通过
        if attempt >= MAX_REVIEW_ATTEMPTS:
            task.status = TASK_FAILED
            task.result += f"\n[质检失败：{MAX_REVIEW_ATTEMPTS} 次未通过。最后反馈：{feedback}]"
            self.broadcast(reviewer,
                           f"❌ {task.assignee} {MAX_REVIEW_ATTEMPTS} 次质检未通过，任务标记失败")
            if self.verbose:
                print(f"[review] ❌ {task.assignee} 耗尽 {MAX_REVIEW_ATTEMPTS} 次重试，标记 failed")
        else:
            # 单独 send 给 assignee 反馈，让其重做
            self.send(reviewer, task.assignee,
                      f"质检未通过（第 {attempt} 次）。反馈：{feedback}")
            task.status = TASK_REDOING
            if self.verbose:
                print(f"[review] ⚠️ {task.assignee} 第 {attempt} 次未过，打回重做")
                print(f"         反馈：{_preview(feedback, 150)}")

    # ------------------------------------------------------------
    # 项目经理：用 LLM 拆解用户任务（强制含质检员 + 注入 team 级 rules）
    # ------------------------------------------------------------
    def _plan_team(self, user_input: str) -> dict:
        """
        调一次 LLM，让它当项目经理——把用户任务拆成「成员清单 + 任务序列」。
        强制要求：members 里必须有且仅有 1 名质检员，其余全是执行者（role 取值仅两种）。
        返回结构：
            {
                "members": [{"name": "A1", "role": "执行者"},
                            {"name": "A2", "role": "执行者"},
                            {"name": "Q1", "role": "质检员"}],
                "tasks": [
                    {"assignee": "A1", "task": "...", "depends_on": []}
                ]
            }
        依赖通过 depends_on 引用前序 assignee 的 name——编排器会把对应结果
        通过 team.send() 注入到下一位的 inbox。
        """
        planning_prompt = (
            "你是一个项目规划师。请把用户的任务拆解成多个角色（成员）和子任务序列，输出严格 JSON。\n\n"
            "规则：\n"
            "1) 每个成员有简短的 name（如 A1、A2、B1）和 role（角色）。\n"
            "   role 只能取两种值：\n"
            "     · 「执行者」——承担具体任务（A1、A2、B1...）\n"
            "     · 「质检员」——承担验收任务（只设 1 名，name 建议 Q1）\n"
            "2) 每个任务标注 assignee（执行者 name）和 task（具体要做的事）\n"
            "3) 如果某任务依赖前序任务的结果，在 depends_on 数组里列出前序 assignee 的 name\n"
            "4) 任务应覆盖用户问题所有需要完成的部分\n"
            "5) **必须**分配且仅分配 1 名质检员——在 members 里加一个 name（如 Q1）、"
            "role 写「质检员」。质检员**不接 task**（不在 tasks 的 assignee 里出现），"
            "它由编排器在事件循环里对每个完成的任务做验收。\n\n"
            "输出格式（严格 JSON，不要加 markdown 代码块）：\n"
            "{\n"
            '  "members": [{"name": "A1", "role": "执行者"}, {"name": "A2", "role": "执行者"}, '
            '{"name": "Q1", "role": "质检员"}],\n'
            '  "tasks": [\n'
            '    {"assignee": "A1", "task": "...", "depends_on": []},\n'
            '    {"assignee": "A2", "task": "...", "depends_on": ["A1"]}\n'
            '  ]\n'
            "}\n"
        )

        # team 级 rules 注入——让项目经理拆任务时守规矩
        if self.rules:
            planning_prompt += (
                f"\n---\n以下是项目级行为规范，拆任务时必须遵守：\n{self.rules}"
            )

        response = client.messages.create(
            model=MODEL,
            max_tokens=2000,
            system=planning_prompt,
            messages=[{"role": "user", "content": user_input}],
        )
        text = "".join(b.text for b in response.content if b.type == "text").strip()

        # prompt 已要求纯 JSON——直接解析。失败就报错带原文便于排查
        try:
            plan = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"项目经理 JSON 解析失败：{e}\n原文：{text}")

        # 简单校验
        if "members" not in plan or "tasks" not in plan:
            raise RuntimeError(f"项目经理输出缺少必需字段：{plan}")
        return plan

    # ------------------------------------------------------------
    # 辅助：从 members 里找质检员（role 含「质检/验收/review」关键字）
    # ------------------------------------------------------------
    def _find_reviewer(self, members: list):
        keywords = ("质检", "验收", "review", "qa", "审")
        for m in members:
            role_lower = str(m.get("role", "")).lower()
            if any(kw in role_lower for kw in keywords):
                return m["name"]
        return None




# ============================================================
# Part 7: 交互式入口
# ============================================================


def run_one(user_input: str) -> None:
    """跑一次完整的 Team 协作流程，结果落盘到 agent_memory.md"""
    team = Team(tools=LOCAL_TOOLS, local_fns=LOCAL_FUNCTIONS, verbose=True)
    results = team.run_team(user_input)
    print(f"\n[最终结果] {len(results)} 个成员完成各自任务：")
    for name, r in results.items():
        print(f"  · {name}: {r}")
    append_memory(user_input, json.dumps(results, ensure_ascii=False))


def main():
    init_client()

    print("=" * 60)
    print("Demo5 Agent 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    rules_status = "已加载" if load_rules() else "未配置"
    print(f"Rules:  {RULES_FILE} ({rules_status})")
    print("=" * 60)
    print("本节演示 Team 模式——多 Agent 协作 + 必含质检员总闸门。")
    print("命令：")
    print("  quit   退出")
    print("  其它   当作新任务输入")
    print("=" * 60)

    while True:
        try:
            user_input = input("\n用户: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("再见！")
            break

        try:
            run_one(user_input)
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
