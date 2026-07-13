#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo3-tools - 工具扩展轴的 Agent

在 demo2-memory（base × 记忆）基础上叠加「工具轴」：
    + 本地工具扩展：edit（string replacement）—— 比 read+write 整文件覆盖更精细
    + MCP（Model Context Protocol）—— 工具不再硬编码在 Agent 进程，
      由独立的 HTTP 服务通过 JSON-RPC 2.0 暴露，Agent 远程发现并调用

公式：demo3 = base × 工具

「工具扩展」的两类增量：
    (A) 能力维度扩展：edit 提供 read+write 做不到的精细修改（只发改动部分）
    (B) 协议维度扩展：MCP 让工具可以跨进程 / 跨机器 / 跨语言复用

单文件按 7 个 Part 组织：
    Part 1: LLM 客户端初始化（同 demo1/2）
    Part 2: 本地工具定义（execute_bash / read_file / write_file + 新增 edit）
    Part 3: 本地工具实现 + 路由表
    Part 4: 长期记忆 + 上下文管理（沿用 demo2-memory：compact + caching）
    Part 5: MCP 客户端（mcp_send：JSON-RPC 2.0 over HTTP）
    Part 6: 工具合并（本地 4 + MCP N）+ 路由扩展
    Part 7: Agent 主循环（ReAct + compact + caching，本地/MCP 统一分发）

启动顺序：
    1. 先在另一个终端启动 MCP Server：  python mcp_server.py
    2. 再启动 Agent：                    python agent.py
"""

import os
import subprocess
from datetime import datetime
from typing import Optional

import requests
from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化（同 demo1/2）
# ============================================================

# ↓↓↓ 只需改这一行 ↓↓↓
API_KEY = ""

# 默认配置（一般无需修改）
BASE_URL       = "https://open.bigmodel.cn/api/anthropic"
MODEL          = "glm-5.2"
API_TIMEOUT_MS = 3000000

# Prompt caching 开关（同 demo2-memory）
USE_CACHE_CONTROL = True

# MCP Server 地址（对应 mcp_server.py 默认监听）
MCP_URL = "http://127.0.0.1:8888/mcp"


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
# Part 2: 本地工具定义（demo3 新增 edit）
# ============================================================
# demo1 的 3 件套（execute_bash / read_file / write_file）保留不变。
# demo3 在此基础上**新增 1 个本地工具 edit** —— 精细修改（string replacement）。
#
# 为什么需要 edit？
#   read_file + write_file 改文件的唯一方式是「读全文 → 改一处 → 写全文」。
#   对 10k 行的文件，每次改一行都要重发 10k 行内容给 LLM + 重写 10k 行到磁盘。
#   edit 只需要发送「old 段 + new 段」两小段，磁盘上也只重写差异。
#   这就是 Claude Code 的 Edit 工具的核心设计动机。

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
        "description": "写入文件，不存在则创建，存在则覆盖（整文件覆写）",
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
        # === demo3 新增 ===
        "name": "edit",
        "description": (
            "精确替换文件中的一段文本（string replacement）。"
            "比 write_file 整文件覆写更精细，适合改一行 / 改一个标识符 / 改一个值。"
            "若 old 在文件中出现多次，默认只替换第一处；replace_all=true 替换全部。"
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
]

SYSTEM_PROMPT_BASE = """你是一个有用的助手，可以通过本地工具与远程 MCP 服务完成用户任务。

本地工具：
1. execute_bash: 执行 shell 命令
2. read_file: 读取文件内容
3. write_file: 写入文件（整文件覆写）
4. edit: 精确替换文件中的一段文本（适合小改动，比 write_file 节省 token）

远程 MCP 工具：见启动时打印的工具列表（add / multiply / weather 等）。

请根据任务选择最合适的工具。改文件小改动时优先用 edit 而非 write_file。"""


# ============================================================
# Part 3: 本地工具实现 + 路由表
# ============================================================

def execute_bash(command: str) -> str:
    """执行 shell 命令（安全约束见 demo6）"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
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
    """写入文件（整文件覆写）"""
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
    """
    精确替换文件中的一段文本（demo3 新增）。

    与 write_file 的核心区别：
        - write_file：发整文件内容 → 重写整文件
        - edit：只发 old + new 两段 → 在原文件上做 string replacement

    Args:
        path:        目标文件
        old:         要被替换的原文本（必须精确匹配，含空格/缩进/换行）
        new:         替换为的新文本
        replace_all: True=替换全部匹配；False=只替换第一处（默认）

    Returns:
        操作结果字符串（含替换处数 + 新文件字符数）
    """
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        if not old:
            return "[错误] old 不能为空字符串（会无限匹配）"

        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        occurrences = content.count(old)
        if occurrences == 0:
            return f"[错误] 未在 {path} 中找到要替换的文本。请用 read_file 确认精确内容（含空格/缩进）。"

        if replace_all:
            new_content = content.replace(old, new)
            which = f"全部 {occurrences} 处"
        else:
            new_content = content.replace(old, new, 1)
            which = f"第 1 处（共 {occurrences} 处匹配，未替换的可用 replace_all=true）"

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return (
            f"[成功] {path} 替换 {which}；"
            f"文件 {len(content)} → {len(new_content)} 字符"
        )
    except Exception as e:
        return f"[错误] edit 失败: {e}"


# 本地工具路由表（MCP 工具在 Part 6 通过 _dispatch_tool 统一分发）
LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "edit":         edit,
}


# ============================================================
# Part 4: 长期记忆 + 上下文管理（沿用 demo2-memory）
# ============================================================
# 这部分代码与 demo2-memory 完全一致：compact_messages + cache_control。
# demo3 的轴是「工具」，但保留 demo2 的记忆机制让 Agent 仍然能记得过去 / 不爆上下文。
# 详见 demo2-memory/agent.py Part 4-5 和讲稿第 3-4 章。

MEMORY_FILE          = "agent_memory.md"
MEMORY_WINDOW_LINES  = 50

COMPACT_THRESHOLD_MESSAGES = 20
COMPACT_KEEP_RECENT        = 6

COMPACT_SYSTEM_PROMPT = """你是上下文压缩助手。把下面的 Agent 对话历史压缩成一段简洁的事实摘要。

要求：
1. 保留：用户意图、关键决策、工具调用的核心结果（文件路径/数字/结论）
2. 丢弃：重复的试错、冗长的工具原始输出、无关细节
3. 用一段 200-400 字的连贯叙述输出，不要分点列条
4. 不要加任何前缀说明，直接输出摘要内容"""


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
    result_preview = (result or "").strip()
    if len(result_preview) > 500:
        result_preview = result_preview[:500] + "..."
    entry = (
        f"\n## [{timestamp}]\n"
        f"**任务**: {task}\n"
        f"**结果**: {result_preview}\n"
    )
    try:
        with open(MEMORY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as e:
        print(f"[警告] 写入记忆文件失败: {e}")


def build_system_prompt(verbose: bool = False) -> str:
    """组合 system prompt = 基础说明 + 记忆后缀"""
    memory = load_memory()
    if verbose:
        if memory:
            n_tasks = memory.count("\n## [")
            print(f"[记忆] 已加载 {n_tasks} 条历史任务作为 Progressive Context")
        else:
            print(f"[记忆] 无历史记忆（首次运行或文件为空）")

    if not memory.strip():
        return SYSTEM_PROMPT_BASE
    return SYSTEM_PROMPT_BASE + "\n\n## 历史任务记忆（最近）\n\n" + memory


def _build_system_param(system_prompt: str):
    """system 参数：支持 cache_control 时用 blocks，否则用纯字符串"""
    if not USE_CACHE_CONTROL:
        return system_prompt
    return [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                t = block.get("type")
                if t == "text":
                    parts.append(block.get("text", ""))
                elif t == "tool_use":
                    parts.append(f"[调用工具 {block.get('name')}]")
                elif t == "tool_result":
                    parts.append(str(block.get("content", ""))[:200])
            else:
                t = getattr(block, "type", None)
                if t == "text":
                    parts.append(getattr(block, "text", ""))
                elif t == "tool_use":
                    parts.append(f"[调用工具 {getattr(block, 'name', '')}]")
    return "\n".join(parts)


def estimate_messages_tokens(messages: list) -> int:
    total = 0
    for msg in messages:
        total += len(_extract_text(msg.get("content", ""))) // 3
        total += 10
    return total


def should_compact(messages: list) -> bool:
    return len(messages) >= COMPACT_THRESHOLD_MESSAGES


def compact_messages(messages: list, verbose: bool = False) -> list:
    """动态压缩 messages（同 demo2-memory）"""
    if len(messages) < COMPACT_THRESHOLD_MESSAGES:
        return messages

    old_messages = messages[:-COMPACT_KEEP_RECENT]
    recent_messages = messages[-COMPACT_KEEP_RECENT:]

    if verbose:
        old_tokens = estimate_messages_tokens(old_messages)
        print(f"\n[compact] 触发：{len(old_messages)} 条老消息（~{old_tokens} tokens）→ 摘要")
        print(f"[compact] 保留最近 {COMPACT_KEEP_RECENT} 条原始消息")

    transcript_parts = []
    for msg in old_messages:
        role = msg.get("role", "?")
        text = _extract_text(msg.get("content", ""))
        transcript_parts.append(f"### {role}\n{text}")
    transcript = "\n\n".join(transcript_parts)

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=COMPACT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"对话历史：\n\n{transcript}\n\n请输出压缩摘要："}],
        )
        summary = "".join(b.text for b in response.content if b.type == "text")

        if verbose:
            preview = summary.replace("\n", " ")[:200]
            print(f"[compact] 摘要: {preview}...")

        summary_msg = {"role": "user", "content": f"[历史对话已压缩，摘要如下]\n{summary}"}
        ack_msg = {"role": "assistant", "content": "好的，我已了解历史对话摘要，继续执行当前任务。"}
        new_messages = [summary_msg, ack_msg] + recent_messages
        if verbose:
            new_tokens = estimate_messages_tokens(new_messages)
            print(f"[compact] 压缩后：{len(new_messages)} 条消息（~{new_tokens} tokens）")
        return new_messages
    except Exception as e:
        if verbose:
            print(f"[compact] 摘要失败 ({e})，保留原 messages 不压缩")
        return messages


# ============================================================
# Part 5: MCP 客户端（demo3 核心新增之一）
# ============================================================
# 一个最小的 MCP client：所有调用都通过 JSON-RPC 2.0 over HTTP。
#
# MCP 协议规定的三个核心 method：
#   - initialize : 握手 + 协议版本协商（演示版不做鉴权）
#   - tools/list : 拿到 server 端的完整工具 schema 列表
#   - tools/call : 按名字 + arguments 调用具体工具，返回 content 包装的结果
#
# MCP 的本质：**工具的能力边界从「同一进程的函数调用」
#             扩展到「跨进程 / 跨机器的 RPC 调用」**。
# 工具不需要被 Agent 进程 import，可以是任何语言写的、跑在任何地方的独立服务。

class MCPClient:
    def __init__(self, url: str):
        self.url = url
        self._id = 0
        self.initialized = False

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def send(self, method: str, params: Optional[dict] = None) -> dict:
        """发送 JSON-RPC 2.0 请求并返回 result 字段。失败抛 RuntimeError。"""
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
            raise RuntimeError(f"MCP HTTP {resp.status_code} ({method}): {resp.text[:200]}")

        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"MCP 调用失败 ({method}): {err}")

        return data.get("result", {})

    def initialize(self) -> dict:
        """握手：拿协议版本 + server 能力。真实场景还会做鉴权。"""
        result = self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities":    {},
            "clientInfo":      {"name": "demo3-tools-agent", "version": "1.0.0"},
        })
        self.initialized = True
        return result

    def list_tools(self) -> list:
        """发现工具：返回 server 端完整工具 schema 列表。"""
        result = self.send("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用工具：按 name + arguments 执行，提取 content[].text 拼接后返回。"""
        result = self.send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts) if texts else "[MCP 工具无文本返回]"


# ============================================================
# Part 6: 工具合并 + 统一分发
# ============================================================
# 启动时一次性拉取 MCP server 的工具列表，与本地工具合并，得到完整工具集。
# 拉取失败时降级为「只用本地工具」，保证 Agent 仍可运行（graceful degradation）。
#
# 工具调用的统一入口是 _dispatch_tool：
#   - 本地工具名（execute_bash / read_file / write_file / edit）→ 调本地函数
#   - 其他工具名（add / multiply / weather / ...）→ 走 MCP RPC

def merge_tools(local_tools: list, mcp_tools: list) -> list:
    """两端 schema 格式一致（都用 input_schema），直接拼接。"""
    return list(local_tools) + list(mcp_tools)


def _dispatch_tool(
    name: str,
    args: dict,
    local_fns: dict,
    mcp_client: MCPClient,
    verbose: bool,
) -> str:
    """统一工具分发：本地 or MCP。LLM 不需要知道工具在哪，只按名字调用。"""
    if name in local_fns:
        if verbose:
            arg_preview = _preview(str(args), 80)
            print(f"  [工具 · 本地] {name}({arg_preview})")
        try:
            return str(local_fns[name](**args))
        except Exception as e:
            return f"[错误] 本地工具 {name} 执行失败: {e}"

    # 不在本地 → 走 MCP
    if verbose:
        arg_preview = _preview(str(args), 80)
        print(f"  [工具 · MCP]  {name}({arg_preview})")
    try:
        return mcp_client.call_tool(name, args)
    except Exception as e:
        return f"[错误] MCP 工具 {name} 调用失败: {e}"


# ============================================================
# Part 7: Agent 主循环（ReAct + compact + caching + 本地/MCP 统一分发）
# ============================================================
# 与 demo2-memory 的核心区别：
#   - 工具集从 3 个本地扩展到 4 本地 + N MCP（N 由 server 决定）
#   - 工具调用通过 _dispatch_tool 统一分发，LLM 视角下无差异

MAX_ITERATIONS = 30


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        text = _extract_text(msg.get("content", ""))
        print(f"  [{i}] {msg.get('role', '?'):<9}: {_preview(text)}")
    print()


def _print_cache_stats(usage, verbose: bool = True) -> None:
    if not verbose or usage is None:
        return
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read   = getattr(usage, "cache_read_input_tokens", 0) or 0
    input_tokens = getattr(usage, "input_tokens", 0) or 0

    if cache_create > 0:
        print(f"  [cache] 创建缓存 {cache_create} tokens（1.25× 计费）+ 输入 {input_tokens}")
    elif cache_read > 0:
        print(f"  [cache] 命中缓存 {cache_read} tokens（0.1× 计费）+ 输入 {input_tokens} ✓")
    elif USE_CACHE_CONTROL:
        print(f"  [cache] 未命中 / 输入 {input_tokens} tokens")
    else:
        print(f"  [cache] caching 关闭 / 输入 {input_tokens} tokens")


def run_agent(
    user_input: str,
    all_tools: list,
    local_fns: dict,
    mcp_client: MCPClient,
    system_prompt: str,
    verbose: bool = True,
) -> str:
    """ReAct 主循环，集成记忆轴 + 工具轴所有机制。"""
    system_param = _build_system_param(system_prompt)
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n----- ReAct 第 {loop_idx} 轮 -----")
            _print_messages(messages)

        if should_compact(messages):
            messages = compact_messages(messages, verbose=verbose)

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_param,
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
            _print_cache_stats(response.usage, verbose=verbose)

        if response.stop_reason != "tool_use":
            if verbose:
                print(f"[任务结束] 大模型判断完成")
            result = "".join(b.text for b in response.content if b.type == "text")
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                # 统一分发：本地 or MCP
                result = _dispatch_tool(
                    block.name,
                    block.input or {},
                    local_fns,
                    mcp_client,
                    verbose,
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})
    else:
        result = "[错误] 超过最大循环次数"

    append_memory(user_input, result)
    if verbose:
        print(f"\n[记忆] 已写入 {MEMORY_FILE}（任务 + 结果摘要）")
    return result


# ============================================================
# 交互式入口
# ============================================================

def main():
    init_client()

    print("=" * 60)
    print("Demo3-tools Agent 已启动（工具扩展轴）")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"MCP:    {MCP_URL}")
    print(f"缓存:   cache_control={'on' if USE_CACHE_CONTROL else 'off'}")
    print("=" * 60)

    # ---- MCP 握手 + 发现工具 ----
    mcp_client = MCPClient(MCP_URL)
    mcp_tools = []
    try:
        info = mcp_client.initialize()
        print(f"[MCP] 握手成功：{info.get('serverInfo', {})} 协议版本 {info.get('protocolVersion')}")
        mcp_tools = mcp_client.list_tools()
        print(f"[MCP] 发现 {len(mcp_tools)} 个远程工具：{', '.join(t['name'] for t in mcp_tools)}")
    except Exception as e:
        print(f"[MCP] 连接失败，降级为仅本地工具模式。原因: {e}")
        print(f"[MCP] 请确认已在另一个终端运行：python mcp_server.py")

    # ---- 合并工具 ----
    all_tools = merge_tools(LOCAL_TOOLS, mcp_tools)
    print(f"[Tools] 合并后共 {len(all_tools)} 个工具：{', '.join(t['name'] for t in all_tools)}")

    # ---- 构建 system prompt（含记忆）----
    system_prompt = build_system_prompt(verbose=True)

    print("\n命令:   /memory 查看记忆 / /tools 查看工具 / quit 退出")
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

        if user_input.lower() in {"/memory", "/m"}:
            print(f"\n--- {MEMORY_FILE} 内容 ---")
            print(load_memory() or "(空)")
            print(f"--- end ---")
            continue

        if user_input.lower() in {"/tools", "/t"}:
            print(f"\n--- 当前 {len(all_tools)} 个工具 ---")
            for t in all_tools:
                src = "本地" if t["name"] in local_fns else "MCP"
                print(f"  [{src}] {t['name']}: {t.get('description', '')[:60]}")
            continue

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
        except Exception as e:
            print(f"\n[错误] {e}")


# /tools 命令需要查 local_fns，提前引用
local_fns = LOCAL_FUNCTIONS


if __name__ == "__main__":
    main()
