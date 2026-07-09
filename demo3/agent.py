#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo3 - Rules + MCP 的 Agent

在 demo2（LLM × 工具 × 循环 × 记忆 × 规划）基础上做两个增强：
    + Rules（规则文件 .agent/rules.md）：不改代码、通过上下文约束大模型行为
    + MCP（Model Context Protocol）：工具不再硬编码在 Agent 进程，由独立的
      HTTP 服务通过 JSON-RPC 2.0 暴露，Agent 远程发现并调用

同时把 demo2 的「独立 plan 命令」重新设计为 `run_agent` 顶层的自动决策分叉 —— 大模型在第 1 轮决策是否拆步骤，拆完后共享 messages 逐步执行。

单文件按 7 个 Part 组织：
    Part 1: LLM 客户端初始化（与 demo1/demo2 一致）
    Part 2: 本地工具定义 + 实现（read_file / write_file / execute_bash / plan）
    Part 3: Rules 加载器（从 .agent/rules.md 注入 system prompt）
    Part 4: 记忆系统（沿用 demo2 的 agent_memory.md 滑动窗口）
    Part 5: MCP 客户端（mcp_send：JSON-RPC 2.0 over HTTP）
    Part 6: 工具合并（本地 4 + MCP 3 = 7 个工具）+ 路由表
    Part 7: Agent 主循环（顶层决策 plan or 直接执行，再走共享 messages 的 ReAct）

启动顺序：
    1. 先在另一个终端启动 MCP Server：  python mcp_server.py
    2. 再启动 Agent：                    python agent.py
"""

import os
import sys
import json
import subprocess
from datetime import datetime
from typing import Optional

import requests
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

# MCP Server 地址（对应 mcp_server.py 的默认监听）
MCP_URL        = "http://127.0.0.1:8888/mcp"


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
# 与 demo1/demo2 完全一致的三个工具：read_file / write_file / execute_bash
# 加上 demo2 里独立命令 plan 重新设计成的顶层决策工具 → 本地共 4 个工具。

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
        "name": "plan",
        "description": (
            "任务规划工具。当用户的任务复杂、需要拆解成多个有序步骤时调用。"
            "大模型通过此工具返回结构化的 steps 列表，由 Agent 逐步执行。"
            "简单任务无需调用此工具。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-5 个有序步骤，每步是一个具体的可执行子任务",
                    "minItems": 1,
                    "maxItems": 10,
                }
            },
            "required": ["steps"],
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


# ============================================================
# Part 3: Rules 加载器（demo3 新增）
# ============================================================
# 把 .agent/rules.md 文件内容作为 system prompt 的后缀注入。
# 大模型在生成代码 / 选工具时，会参考这份"规范"——
# 这是不改代码、不改工具，仅通过上下文约束 Agent 行为的最简方式。

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
# Part 5: MCP 客户端（demo3 新增）
# ============================================================
# 一个最小的 MCP client：所有调用都通过 mcp_send 走 JSON-RPC 2.0 over HTTP。
#
# MCP 协议规定的三个核心 method：
#   - initialize : 握手 + 协议版本协商（演示版不做鉴权）
#   - tools/list : 拿到 server 端的完整工具 schema 列表
#   - tools/call : 按名字 + arguments 调用具体工具，返回 content 包装的结果

class MCPClient:
    def __init__(self, url: str):
        self.url = url
        self._id = 0
        self.initialized = False

    def _next_id(self) -> int:
        """生成 JSON-RPC 请求 id（自增整数）"""
        self._id += 1
        return self._id

    def send(self, method: str, params: Optional[dict] = None) -> dict:
        """
        发送一次 JSON-RPC 2.0 请求并返回 result 字段。

        - 网络异常 / HTTP 非 2xx / JSON-RPC error 字段 → 抛 RuntimeError
        - 成功 → 返回 response["result"]
        """
        payload = {
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  method,
            "params":  params or {},
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"MCP 网络错误 ({method}): {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(
                f"MCP HTTP {resp.status_code} ({method}): {resp.text[:200]}"
            )

        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"MCP 调用失败 ({method}): {err}")

        return data.get("result", {})

    # ---- 上层封装：三个核心 method ----

    def initialize(self) -> dict:
        """握手：拿协议版本 + server 能力。真实场景还会做鉴权。"""
        result = self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities":    {},
            "clientInfo":      {"name": "demo3-agent", "version": "1.0.0"},
        })
        self.initialized = True
        return result

    def list_tools(self) -> list:
        """发现工具：返回 server 端完整工具 schema 列表。"""
        result = self.send("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        """
        调用工具：按 name + arguments 执行，提取 content[].text 拼接后返回。
        MCP 协议规定 tools/call 的返回值用 content 数组包装，这里只取文本。
        """
        result = self.send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts) if texts else "[MCP 工具无文本返回]"


# ============================================================
# Part 6: 工具合并 + 路由表
# ============================================================
# 启动时一次性拉取 MCP server 的工具列表，与本地工具合并，得到完整的工具集。
# 拉取失败时降级为「只用本地工具」，保证 Agent 仍可运行。

def merge_tools(local_tools: list, mcp_tools: list) -> list:
    """两端 schema 格式一致，直接拼接。"""
    return list(local_tools) + list(mcp_tools)


# ============================================================
# Part 7: Agent 主循环
# ============================================================

STEP_MAX_ITERATIONS = 10   # 单个 step 子循环 / 非 plan 场景 ReAct 的最大轮数


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览，不需要精细解析每种 block 类型。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def build_system_prompt(verbose: bool = False) -> str:
    """组合 system prompt = 基础说明 + Rules + 记忆"""
    parts = [
        "你是一个有用的助手，可以通过工具与本地系统、远程 MCP 服务交互。",
    ]

    # 注入 Rules
    rules = load_rules()
    if rules:
        if verbose:
            print(f"[Rules] 已加载 {RULES_FILE}（{len(rules)} 字符）")
        parts.append("\n## 项目规范（Rules）\n\n" + rules)
    elif verbose:
        print(f"[Rules] 未找到 {RULES_FILE}，跳过")

    # 注入记忆
    memory = load_memory()
    if memory:
        if verbose:
            n_tasks = memory.count("\n## [")
            print(f"[记忆] 已加载 {n_tasks} 条历史任务作为 Progressive Context")
        parts.append("\n## 历史任务记忆（最近）\n\n" + memory)
    elif verbose:
        print(f"[记忆] 无历史记忆（首次运行或文件为空）")

    return "\n".join(parts)


def _dispatch_tool(
    name: str,
    args: dict,
    local_fns: dict,
    mcp_client: MCPClient,
    verbose: bool,
) -> str:
    """
    普通工具分发器：本地 or MCP。

    plan 不在这里 —— 由 run_agent 顶层拦截后遍历 steps，避免嵌套。
    """
    if name in local_fns:
        if verbose:
            print(f"  [工具 · 本地] {name}({_preview(args, 80)})")
        try:
            return str(local_fns[name](**args))
        except Exception as e:
            return f"[错误] 本地工具 {name} 执行失败: {e}"

    if verbose:
        print(f"  [工具 · MCP]  {name}({_preview(args, 80)})")
    try:
        return mcp_client.call_tool(name, args)
    except Exception as e:
        return f"[错误] MCP 工具 {name} 调用失败: {e}"


def run_agent_steps(
    messages: list,
    tools: list,
    local_fns: dict,
    mcp_client: MCPClient,
    system_prompt: str,
    verbose: bool,
    initial_response=None,
) -> str:
    """
    ReAct 子循环。**共享 messages**：每轮的 assistant / tool_result 都追加到
    同一份 messages 列表，跨轮 / 跨 step 积累上下文（与 demo2 一致）。

    若传入 initial_response，则把它作为第 1 轮（不再额外调一次 LLM）；
    否则发起新请求。tools 已由调用方决定（step 内调用方应去掉 plan）。
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

        # 判停：大模型认为这一步做完了
        if response.stop_reason != "tool_use":
            result = "".join(b.text for b in response.content if b.type == "text")
            if verbose:
                print(f"  [迭代 {i} 完成] {_preview(result, 120)}")
            return result

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if verbose:
                print(f"  [LLM] {block.name}({_preview(block.input, 80)})")
            # step 内 name 不可能是 "plan"（step_tools 已去掉），统一走 _dispatch_tool
            result = _dispatch_tool(
                block.name, block.input or {},
                local_fns, mcp_client, verbose,
            )
            if verbose:
                print(f"  [结果] {_preview(result, 120)}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })
        messages.append({"role": "user", "content": tool_results})

        response = None  # 下一轮重新调 LLM

    return f"[Step 未在 {STEP_MAX_ITERATIONS} 轮内完成]"


def run_agent(
    user_input: str,
    all_tools: list,
    local_fns: dict,
    mcp_client: MCPClient,
    system_prompt: str,
    verbose: bool = True,
) -> str:
    """
    Agent 主循环：先做 1 轮顶层决策，按 LLM 是否调 plan 分两条路径。

    - **Plan 场景**：LLM 调 plan → 提取 steps → 共享 messages 遍历执行
      （step_tools 去掉 plan，禁止嵌套）→ 最后让 LLM 做最终总结
    - **非 Plan 场景**：LLM 没调 plan → 把首轮响应直接灌进 run_agent_steps
      继续共享 messages 的 ReAct 循环

    两种场景都共享同一份 messages，跨轮 / 跨 step 积累上下文。
    """
    messages = [{"role": "user", "content": user_input}]

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"顶层决策：plan or 直接执行")
        print(f"{'=' * 60}")
        _print_messages(messages)

    # 顶层决策：让 LLM 用全部工具（含 plan）选一条路
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=system_prompt,
        tools=all_tools,
        messages=messages,
    )

    if verbose:
        print(f"[LLM 决策] stop_reason = {response.stop_reason}")
        for block in response.content:
            if block.type == "text":
                print(f"  - text     : {_preview(block.text, 80)}")
            elif block.type == "tool_use":
                print(f"  - tool_use : {block.name}({block.input})")

    # 找 plan tool_use（若有）
    plan_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "plan"),
        None,
    )

    if plan_block:
        # ===== Plan 场景：遍历 steps，共享 messages =====
        steps = plan_block.input.get("steps", []) or []
        if verbose:
            print(f"\n[Plan] LLM 拆解 {len(steps)} 个步骤，共享 messages 逐步执行")

        # 把 plan 的 tool_use 当作"已规划完成"的 tool_result 回灌（保持消息序列合法）
        messages.append({"role": "assistant", "content": response.content})
        messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": plan_block.id,
                "content": (
                    f"已规划 {len(steps)} 个步骤。我会**逐个**发给你下一步任务，"
                    f"请严格遵守：\n"
                    f"- **只执行当前这一步**描述的任务，完成后立即 end_turn\n"
                    f"- **不要预先做后续 step**（它们会单独发给你）\n"
                    f"- 已在前面 step 调用过的工具，若当前 step 不再需要，不要重复调用\n"
                ),
            }],
        })

        # step_tools 去掉 plan，禁止嵌套
        step_tools = [t for t in all_tools if t.get("name") != "plan"]

        # 遍历 steps，共享 messages
        for i, step in enumerate(steps, 1):
            if verbose:
                print(f"\n{'─' * 60}")
                print(f"[Step {i}/{len(steps)}] {step}")
                print(f"{'─' * 60}")
            step_msg = (
                f"【Step {i}/{len(steps)}】请只执行这一步：\n{step}\n\n"
                f"完成后立即 end_turn，不要预先做后续 step。"
            )
            messages.append({"role": "user", "content": step_msg})
            run_agent_steps(
                messages, step_tools, local_fns, mcp_client,
                system_prompt, verbose,
            )

        # 所有 step 完成，让 LLM 看完整上下文做最终总结
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"[Plan] 所有步骤完成，请求 LLM 最终总结")
            print(f"{'=' * 60}")
        return run_agent_steps(
            messages, step_tools, local_fns, mcp_client,
            system_prompt, verbose,
        )

    # ===== 非 Plan 场景：把首轮响应灌进 ReAct 子循环 =====
    if verbose:
        print(f"\n[非 Plan] 直接进入 ReAct 循环（共享 messages）")
    return run_agent_steps(
        messages, all_tools, local_fns, mcp_client,
        system_prompt, verbose, initial_response=response,
    )


# ============================================================
# 交互式入口
# ============================================================

# 本地工具路由表（plan 不在这里 —— 它由 run_agent 顶层拦截，遍历 steps 走共享 messages）
LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}


def main():
    init_client()

    print("=" * 60)
    print("Demo3 Agent 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"MCP:    {MCP_URL}")
    print(f"Rules:  {RULES_FILE}")
    print("=" * 60)

    # ---- 初始化 MCP 客户端 ----
    mcp_client = MCPClient(MCP_URL)
    mcp_tools = []
    try:
        info = mcp_client.initialize()
        print(f"[MCP] 握手成功：{info.get('serverInfo', {})} "
              f"协议版本 {info.get('protocolVersion')}")
        mcp_tools = mcp_client.list_tools()
        print(f"[MCP] 发现 {len(mcp_tools)} 个工具："
              f"{', '.join(t['name'] for t in mcp_tools)}")
    except Exception as e:
        # MCP 不可用 → 降级为只用本地工具
        print(f"[MCP] 连接失败，降级为仅本地工具模式。原因: {e}")
        print(f"[MCP] 请确认已在另一个终端运行：python mcp_server.py")

    # ---- 合并工具 ----
    all_tools = merge_tools(LOCAL_TOOLS, mcp_tools)
    print(f"[Tools] 合并后共 {len(all_tools)} 个工具："
          f"{', '.join(t['name'] for t in all_tools)}")

    # ---- 构建 system prompt（Rules + 记忆）----
    system_prompt = build_system_prompt(verbose=True)

    print("\n输入 quit / exit 退出")
    print("=" * 60)

    # ---- REPL ----
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
                all_tools=all_tools,
                local_fns=LOCAL_FUNCTIONS,
                mcp_client=mcp_client,
                system_prompt=system_prompt,
                verbose=True,
            )
            print(f"\n助手: {final}")
            # Task 级结束写入记忆
            append_memory(user_input, final)
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
