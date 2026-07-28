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

# 网关、模型、超时均写死，用户只需配置 API Key（两种方式）：
#   1. 直接修改下面的 API_KEY
#   2. 都没设 → 运行时交互式提示输入（不持久化，每次都要重输）
# 默认走智谱 BigModel 的 Anthropic 兼容网关 + glm-5.2 模型。

# ↓↓↓ 只需改这一行 ↓↓↓
API_KEY = ""

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"   # 智谱 BigModel Anthropic 兼容网关
MODEL          = "glm-5.2"                                  # 模型名
API_TIMEOUT_MS = 3000000                                    # 单次请求超时（毫秒），3000000ms = 50 分钟


def load_config() -> dict:
    """环境变量优先于代码默认值（仅 API_KEY 走环境变量有用）"""
    return {
        "api_key":       os.environ.get("ANTHROPIC_API_KEY") or API_KEY,
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
    print("如需持久化：请改 agent_team.py 顶部的 API_KEY 变量")
    print("=" * 60)

    api_key = input("\n请输入 API Key: ").strip()
    if not api_key:
        raise SystemExit("未提供 API Key，退出")

    config["api_key"] = api_key
    return config


# 模块级占位：实际使用前由 __main__ 调用 init_client() 初始化
client: Anthropic = None  # type: ignore


def init_client() -> None:
    """初始化模块级 client（在 __main__ 中调用）"""
    global client
    config = ensure_config()
    kwargs = {
        "api_key": config["api_key"],
        "base_url": config["base_url"],
        # Anthropic SDK 接收秒为单位的超时
        "timeout": config["timeout_ms"] / 1000.0,
    }
    client = Anthropic(**kwargs)


# ============================================================
# Part 2: 工具定义（demo1 四件套 + get_weather + plan_team）
# ============================================================

TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可用于文件操作、系统命令等",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令",
                }
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
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径",
                }
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
                "path": {"type": "string", "description": "要写入的文件路径"},
                "content": {"type": "string", "description": "要写入的内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit",
        "description": (
            "精确替换文件中的一段文本（string replacement）。"
            "比 write_file 整文件覆写更精细，适合改一行 / 改一个值。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string",  "description": "要编辑的文件路径"},
                "old":         {"type": "string",  "description": "要替换的原文本（必须精确匹配，含空格/缩进）"},
                "new":         {"type": "string",  "description": "替换为的新文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配处（默认 false，只替换第一处）"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "get_weather",
        "description": "查询指定城市的天气数据（教学演示用，返回模拟数据，不联网）。需要天气信息时优先用本工具，不要用 execute_bash 调 curl。",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名，如「北京」",
                }
            },
            "required": ["city"],
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
# 每个工具是一个普通 Python 函数：
#   - 错误信息也字符串化返回给大模型，让它自己看到错误后调整策略
#   - 设置超时，防止死循环或长时间阻塞

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding="utf-8",      # GBK Windows 下 text=True 会崩，显式 UTF-8
            errors="replace",
            timeout=60,            # 防止死循环 / 长时间阻塞
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
    """读取文件内容"""
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
    """写入文件"""
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
    """精确替换文件中的文本"""
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        if not old:
            return "[错误] old 不能为空"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        occurrences = content.count(old)
        if occurrences == 0:
            return f"[错误] 未找到匹配文本，请用 read_file 确认精确内容"
        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        which = f"全部 {occurrences} 处" if replace_all else f"第 1 处（共 {occurrences} 处）"
        return f"[成功] {path} 替换 {which}"
    except Exception as e:
        return f"[错误] 编辑文件失败: {e}"


def get_weather(city: str) -> str:
    """返回指定城市的模拟天气数据（教学演示用，非真实数据，不联网）"""
    return (
        f"【模拟数据 · {city}】\n"
        f"当前：多云，26°C，体感 29°C，湿度 89%，南风 4km/h\n"
        f"未来三天最高温：34°C / 35°C / 36°C（持续升温）\n"
        f"紫外线指数：7-9（强），空气质量：轻度污染（PM2.5 偏高）\n"
        f"季节特点：盛夏高温期，7 月下旬至 8 月上旬为主汛期，多雷阵雨"
    )


# ---- plan_team（demo5 新增） ----
def plan_team(members: list, task: str = "") -> str:
    """
    启动多角色团队协作（demo5 新增）。

    LLM 只拆角色，任务内嵌在 role 描述中 → recruit → 消息队列执行 → 返回结果。
    """
    if not members:
        return "[Team] 成员列表为空"

    team = Team()
    for m in members:                              # ① 阶段一·招募：注册到 registry
        team.recruit(m["name"], m.get("role", "团队成员"))
    team.initialize()                              # ② 阶段二·初始化：registry 完整，统一构建 system_prompt

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

    def __init__(self, name: str, role: str):
        self.name     = name
        self.role     = role
        self.messages: list = []
        self.system_prompt = ""   # 由 Team.initialize() 在全员注册完后统一构建

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
        # 阶段一·招募：只注册基本信息，不构建 prompt（registry 此时可能还不全）
        self.registry[name] = role
        self.agents[name] = Agent(name, role)
        print(f"  [Team] 招募 {name}（{role}）")
        return self.agents[name]

    def initialize(self) -> None:
        """阶段二·初始化：全员注册完后（registry 完整）统一构建 system_prompt。

        每个 Agent 都看到完整名册 + 两条规则。prompt 构建集中在这里，
        修改规则措辞只改这一处。
        """
        members_list = "\n".join(f"  {n}: {r}" for n, r in self.registry.items())
        for name, agent in self.agents.items():
            agent.system_prompt = (
                f"你是 {name}\n\n"
                f"职责: {self.registry[name]}\n\n"
                f"团队成员:\n{members_list}\n\n"
                f"规则:\n"
                f"1. 只做属于你职责范围内的工作，不要越权代劳别人的活\n"
                f"2. `[send: 成员名]` 是**字面协议标记**，不是自然语言。只要任务里还有不属于你职责的部分"
                f"（刚接手的、或本职做完后剩余的），你的回复**第一行**就必须是 `[send: 成员名]`"
                f"（填名册里的成员英文名），其后跟一句转交说明；"
                f"不要用「转交给X」之类的自然语言或加粗替代，也不要顺手把别人的活做了。"
                f"只有整个任务都落在你职责范围内时，才自己做完并结束"
            )

    def send(self, sender: str, receiver: str, message: str) -> None:
        self.queue.append(Event(sender, receiver, message))

    def run(self, members: list) -> str:
        """事件循环：pop → agent.chat → 检查 [send:] 路由 → 队列自然排空即结束"""
        MAX_STEPS = len(members) * 3  # 防御性上限，防 [send:] 循环死循环
        last_result = ""
        for _ in range(MAX_STEPS):
            if not self.queue:
                break  # 队列自然排空 → 结束
            event = self.queue.pop(0)
            name = event.receiver
            if name not in self.agents:
                continue
            print(f"\n  [event] {event.sender} → {name}：{_preview(event.message, 80)}")
            result = self.agents[name].chat(event)
            print(f"  [event] {name} 完成：{_preview(result, 100)}")
            last_result = result

            # 检查路由指令：[send: name]（[^\]]+ 兼容中英文名）。
            # 用 search 扫描全文而非只看开头——LLM 不总把标记写在第一行（常先分析再转交）
            stripped = result.strip()
            m_send = re.search(r"\[send:\s*([^\]]+)\]", stripped)
            if m_send:
                target = m_send.group(1).strip()
                if target in self.agents:
                    rest = stripped[m_send.end():].strip()
                    print(f"  [send] {name} → {target}")
                    self.send(name, target, rest or event.message)
                    continue
            # 无路由指令 → 不转发，等队列自然排空
        return last_result


# 路由表：工具名 → 实际函数（调度核心）
# 当大模型说「我要调用 execute_bash」时，Agent 通过这张表把名字映射到具体函数并执行。
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "edit":         edit,
    "get_weather":  get_weather,
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
                    elif block.get("type") == "tool_result": parts.append(str(block.get("content", ""))[:100])
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
        plan_team_result = None
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
            if name == "plan_team":
                plan_team_result = result

        messages.append({"role": "user", "content": tool_results})

        # plan_team 是终端委派工具：团队产出即最终成果，直接结束主循环，
        # 不再让 LLM 决策下一轮（用控制流保证主 Agent 不越权重做团队的活，而非靠 prompt）
        if plan_team_result is not None:
            if verbose:
                print(f"\n[循环结束] plan_team 已返回团队成果，主 Agent 直接汇报")
            return plan_team_result

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
