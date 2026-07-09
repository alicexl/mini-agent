#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4 - Subagent 的分工合作

demo3 的 Agent 不管多少个 step，永远只有「一个」Agent 在干活——上下文越叠越长、
效率也越来越低。现实中遇到相互独立的子任务（前端 / 后端 / 测试），我们更希望
「分包出去」：派一个 Subagent 干一件事，干完把结果交回来，它就消亡。

demo4 在 demo3 基础上做一次**减法 + 一次加法**：
    - 减法：去掉 MCP、去掉 plan
        · 三件本地小工具（add / multiply / weather）从 MCP Server 搬回本地
        · plan 工具与 subagent 工具语义重叠（都是「拆任务」），本次优先验证 subagent 场景
    - 加法：新增 `subagent` 本地工具
        · 大模型通过调 subagent 工具派生一个**独立**的 Agent 循环
        · 每个 subagent 有自己的 system_prompt（角色）、自己的 messages（隔离）、
          自己的工具列表（**去掉 subagent 工具本身，禁止无限递归**）
        · subagent 是一次性的：派生 → 接任务 → 干活 → 返回结果 → 消亡

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化（与 demo1/2/3 一致）
    Part 2: 本地工具定义 + 实现
            execute_bash / read_file / write_file / subagent / add / multiply / weather
    Part 3: Rules 加载器（沿用 demo3）
    Part 4: 记忆系统（沿用 demo2）
    Part 5: 工具路由表（本地函数字典）
    Part 6: Agent 主循环 + Subagent 循环（共享 `_react_loop`）

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
    """环境变量优先于代码默认值"""
    return {
        "api_key":    os.environ.get("ANTHROPIC_API_KEY") or API_KEY,
        "base_url":   BASE_URL,
        "model":      MODEL,
        "timeout_ms": API_TIMEOUT_MS,
    }


def ensure_config() -> dict:
    """配置完整性检查。缺失 API Key 时交互式提示输入。"""
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
    """初始化模块级 LLM client"""
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
# demo3 的 7 工具里：
#   · 三个文件 / shell 工具 → 沿用
#   · 三个 MCP 工具（add / multiply / weather）→ 搬回本地（去掉 MCP 这层）
#   · plan 工具 → 删掉（与 subagent 语义重叠）
#   · 新增 subagent 工具
# 合计 7 个本地工具，没有任何远程 RPC。

# ---- 工具 schema（Anthropic Function Calling 格式）----

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
    {
        "name": "subagent",
        "description": (
            "委派一个独立的 Subagent 来完成子任务。\n"
            "**强制规则**：当用户输入包含「1) 2) 3)」这种编号列表、且列出 2 个及以上子任务时，"
            "**必须**为每个子任务**调用一次 subagent 工具**，由对应 Subagent 完成——"
            "**禁止**主 Agent 自己在循环里连调 add/multiply/weather/write_file 等工具来直接做完。"
            "（即使主 Agent 自己做更省 token，也必须委派——这是 demo4 的演示要求。）\n"
            "Subagent 拥有独立的角色（system_prompt）和独立的上下文（messages），"
            "执行完返回结果摘要后即消亡。\n"
            "不适合 subagent 的场景：后一步要用前一步结果的链式任务（例如「先 a+b，再把结果乘 8」），"
            "这种应让主 Agent 自己顺序完成。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role":  {"type": "string", "description": "Subagent 的角色，例如「加法计算专家」/「Python 工程师」/「测试工程师」"},
                "task":  {"type": "string", "description": "交给 Subagent 完成的具体任务描述"},
            },
            "required": ["role", "task"],
        },
    },
]

# ---- 工具实现 ----

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
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
    """读取文件内容"""
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


def add(a, b) -> str:
    """加法实现（从 demo3 的 MCP Server 搬回本地）"""
    return f"{a} + {b} = {a + b}"


def multiply(a, b) -> str:
    """乘法实现（从 demo3 的 MCP Server 搬回本地）"""
    return f"{a} × {b} = {a * b}"


# 天气演示数据（与 demo3 mcp_server.py 一致，确保行为可对照）
_WEATHER_DB = {
    "北京": ("晴", 25),
    "上海": ("多云", 22),
    "广州": ("雷阵雨", 30),
    "深圳": ("多云", 29),
    "杭州": ("晴", 24),
}


def weather(city: str) -> str:
    """天气查询实现（演示用，非真实接口）"""
    if city in _WEATHER_DB:
        condition, temp = _WEATHER_DB[city]
    else:
        condition = random.choice(["晴", "多云", "阴", "小雨", "大雨", "雷阵雨"])
        temp = random.randint(10, 35)
    return f"{city} 今天天气：{condition}，气温 {temp}°C"


# ============================================================
# Part 3: Rules 加载器（沿用 demo3）
# ============================================================

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent", "rules.md")


def load_rules() -> str:
    """读取 .agent/rules.md；不存在则返回空字符串。"""
    if not os.path.exists(RULES_FILE):
        return ""
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception as e:
        print(f"[警告] 读取 rules 失败: {e}")
        return ""


# ============================================================
# Part 4: 记忆系统（沿用 demo2）
# ============================================================

MEMORY_FILE         = "agent_memory.md"
MEMORY_WINDOW_LINES = 50


def load_memory() -> str:
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(lines[-MEMORY_WINDOW_LINES:])
    except Exception as e:
        print(f"[警告] 读取记忆文件失败: {e}")
        return ""


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
# Part 5: 工具路由表
# ============================================================
# 主 Agent 可见的本地函数字典。
# 注意：subagent 不在这里 —— 它需要特殊的"启动一个独立 Agent 循环"的逻辑，
# 在 `_react_loop` 内单独拦截。其它工具走纯函数调用。

LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "add":          add,
    "multiply":     multiply,
    "weather":      weather,
}


# ============================================================
# Part 6: Agent 主循环 + Subagent 循环
# ============================================================
# demo4 把 demo3 的 `run_agent` / `run_agent_steps` 简化为一个公共子程序 `_react_loop`。
# 主 Agent 和 Subagent 都跑它，差别只在：
#   · **messages 是否独立**：主 Agent 用一份；每次调 subagent 新建一份
#   · **system_prompt 是否带角色**：主 Agent 拼 Rules + 记忆；Subagent 只拼角色
#   · **工具集是否含 subagent**：主 Agent 含；Subagent **去掉 subagent 防递归**

STEP_MAX_ITERATIONS = 10   # 单个 ReAct 循环的最大轮数


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览，不需要精细解析每种 block 类型。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def build_main_system_prompt(verbose: bool = False) -> str:
    """主 Agent 的 system prompt = 基础说明 + Rules + 记忆"""
    parts = [
        "你是一个有用的助手，可以通过工具与本地系统交互。",
    ]

    rules = load_rules()
    if rules:
        if verbose:
            print(f"[Rules] 已加载 {RULES_FILE}（{len(rules)} 字符）")
        parts.append("\n## 项目规范（Rules）\n\n" + rules)
    elif verbose:
        print(f"[Rules] 未找到 {RULES_FILE}，跳过")

    memory = load_memory()
    if memory:
        if verbose:
            n_tasks = memory.count("\n## [")
            print(f"[记忆] 已加载 {n_tasks} 条历史任务作为 Progressive Context")
        parts.append("\n## 历史任务记忆（最近）\n\n" + memory)
    elif verbose:
        print(f"[记忆] 无历史记忆（首次运行或文件为空）")

    return "\n".join(parts)


def build_subagent_system_prompt(role: str) -> str:
    """
    Subagent 的 system prompt = 角色化指令 + "只做被吩咐的事"。

    与主 Agent 相比：
      · **不注入 Rules**（demo4 简化省略；真实场景可注入全局规范）
      · **不注入记忆**（避免被无关历史任务干扰，保持专注）
    """
    return (
        f"你是一个被委派来的 Subagent。你的角色是：**{role}**。\n"
        f"请专注于交给你完成的任务，做完后用一两句话汇报结果。"
    )


def _react_loop(
    messages: list,
    tools: list,
    local_fns: dict,
    system_prompt: str,
    tools_for_subagent: list,
    depth: int,
    verbose: bool,
    initial_response=None,
    indent: str = "",
) -> str:
    """
    通用 ReAct 循环——主 Agent 和 Subagent 共用。

    Args:
        messages:                该循环专属的 messages（主 Agent / Subagent 各自独立）
        tools:                   本循环 LLM 能看到的工具列表
        local_fns:               本循环可调用的本地函数字典
        system_prompt:           本循环的 system prompt（主 Agent / Subagent 不同）
        tools_for_subagent:      若 LLM 调 subagent，传给子循环的工具集（已去掉 subagent）
        depth:                   当前的递归深度（主 Agent = 0，Subagent = 1+）
        verbose:                 是否打印轨迹
        initial_response:        若已有首轮响应，直接用，不重新请求
        indent:                  打印缩进，让 subagent 轨迹可视化区分

    每个 ReAct 迭代：
        1. 若没有 initial_response，发请求拿响应
        2. 若 stop_reason != tool_use → 返回文本（这一层循环结束）
        3. 遍历响应里的 tool_use 块：
           · subagent → 转手 `_run_subagent` 起一个独立子循环
           · 其它 → 走 local_fns 函数调用
        4. 把 tool_result 灌回 messages，进入下一轮
    """
    response = initial_response
    for i in range(1, STEP_MAX_ITERATIONS + 1):
        if response is None:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )

        # 判停
        if response.stop_reason != "tool_use":
            result = "".join(b.text for b in response.content if b.type == "text")
            if verbose:
                print(f"{indent}  [迭代 {i} 完成] {_preview(result, 120)}")
            return result

        # 打印 LLM 的思考文本（如果有）——让"为什么调这个工具"可见
        if verbose:
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"{indent}  [LLM 思考] {_preview(block.text, 200)}")

        messages.append({"role": "assistant", "content": response.content})

        # 遍历 tool_use 块：subagent 起独立子循环，其它走 local_fns
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            name = block.name
            args = block.input or {}
            if verbose:
                print(f"{indent}  [LLM] {name}({_preview(args, 80)})")

            if name == "subagent":
                if verbose:
                    print(f"{indent}  [工具 · Subagent] role={args.get('role')!r} "
                          f"task={_preview(args.get('task', ''), 80)}")
                result = _run_subagent(
                    role=args.get("role", "通用助手"),
                    task=args.get("task", ""),
                    tools=tools_for_subagent,
                    local_fns=local_fns,
                    depth=depth + 1,
                    verbose=verbose,
                )
            elif name in local_fns:
                if verbose:
                    print(f"{indent}  [工具 · 本地] {name}({_preview(args, 80)})")
                try:
                    result = str(local_fns[name](**args))
                except Exception as e:
                    result = f"[错误] 本地工具 {name} 执行失败: {e}"
            else:
                result = f"[错误] 未知工具: {name}"

            if verbose:
                print(f"{indent}  [结果] {_preview(result, 120)}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })
        messages.append({"role": "user", "content": tool_results})

        response = None

    return f"[ReAct 循环未在 {STEP_MAX_ITERATIONS} 轮内完成]"


def _run_subagent(
    role: str,
    task: str,
    tools: list,
    local_fns: dict,
    depth: int,
    verbose: bool,
) -> str:
    """
    启动一个独立的 Subagent ReAct 循环。

    关键设计：
        · **独立的 messages**：从 `[{"role":"user","content":task}]` 开始，
          与主 Agent 的 messages 完全隔离——拿不到主 Agent 的历史对话，
          也不会污染主 Agent 的上下文
        · **独立 system_prompt**：基于 `role` 拼一个角色化系统提示，不注入 Rules / 记忆
        · **工具集去掉 subagent**：阻止 Subagent 派生下一层 Subagent（防无限递归）
        · **一次性生命周期**：循环结束 → 返回结果摘要 → messages / prompt 全部丢弃
    """
    indent = "    " * depth  # 缩进打印，让 subagent 轨迹嵌套可视化

    if verbose:
        print(f"{indent}{'─' * 50}")
        print(f"{indent}[Subagent · depth={depth}] role={role!r}")
        print(f"{indent}[Subagent · depth={depth}] task={_preview(task, 100)}")
        print(f"{indent}{'─' * 50}")

    sub_messages = [{"role": "user", "content": task}]
    sub_system_prompt = build_subagent_system_prompt(role)

    final = _react_loop(
        messages=sub_messages,
        tools=tools,
        local_fns=local_fns,
        system_prompt=sub_system_prompt,
        tools_for_subagent=tools,   # 已经去掉 subagent，再传下去也无妨
        depth=depth,
        verbose=verbose,
        indent=indent,
    )

    if verbose:
        print(f"{indent}[Subagent · depth={depth}] 返回主 Agent，messages 即刻销毁")

    return f"[Subagent · {role}] 任务：{task}\n结果：{final}"


def run_agent(
    user_input: str,
    all_tools: list,
    local_fns: dict,
    system_prompt: str,
    verbose: bool = True,
) -> str:
    """
    主 Agent 循环。

    与 demo3 的差异：
        - **没有顶层 plan 决策分叉**——主 Agent 直接进 ReAct 循环
        - LLM 看到工具列表里有 `subagent`，遇到相互独立的子任务时会主动委派
        - `tools_for_subagent` 在所有工具里去掉 subagent——一旦 LLM 调 subagent，
          就用这份工具集启动子循环（子循环不会再调 subagent，堵死递归）
    """
    messages = [{"role": "user", "content": user_input}]

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"主 Agent 启动 ReAct 循环")
        print(f"{'=' * 60}")
        _print_messages(messages)

    # 给 subagent 准备的工具集：去掉 subagent 自身
    tools_for_subagent = [t for t in all_tools if t.get("name") != "subagent"]

    return _react_loop(
        messages=messages,
        tools=all_tools,
        local_fns=local_fns,
        system_prompt=system_prompt,
        tools_for_subagent=tools_for_subagent,
        depth=0,
        verbose=verbose,
    )


# ============================================================
# 交互式入口
# ============================================================

def main():
    init_client()

    print("=" * 60)
    print("Demo4 Agent 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"Rules:  {RULES_FILE}")
    print("=" * 60)

    # 构建 system prompt（Rules + 记忆）
    system_prompt = build_main_system_prompt(verbose=True)

    print(f"\n[Tools] 共 {len(LOCAL_TOOLS)} 个本地工具："
          f"{', '.join(t['name'] for t in LOCAL_TOOLS)}")
    print("[Tools] 其中 `subagent` 工具可派生独立 Agent（已自动禁止递归）")

    print("\n输入 quit / exit 退出")
    print("=" * 60)

    # REPL
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
            final = run_agent(
                user_input=user_input,
                all_tools=LOCAL_TOOLS,
                local_fns=LOCAL_FUNCTIONS,
                system_prompt=system_prompt,
                verbose=True,
            )
            print(f"\n助手: {final}")
            append_memory(user_input, final)
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
