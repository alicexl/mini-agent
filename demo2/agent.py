#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo2 - 带记忆和规划的 Agent

在 demo1（LLM × 工具 × 循环）基础上增加两个能力：
    × 记忆（跨任务长期记忆）
    × 规划（先拆 step 再执行）

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化
    Part 2: 工具定义（Function Calling schema）
    Part 3: 工具实现 + 路由表
    Part 4: 记忆系统（agent_memory.md + 滑动窗口）
    Part 5: 规划系统（get_plan + 防御性降级）
    Part 6: Agent 主循环（run_agent_step + 多步串联）

用法：
    python agent.py            # 直接 ReAct（demo1 模式）
    python agent.py --plan     # Plan 模式（先规划再执行）
"""

import os
import sys
import subprocess
import argparse
from datetime import datetime

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================
# 与 demo1 一致：网关、模型、超时写死，用户只需配置 API Key。
#   1. 直接修改下面的 API_KEY
#   2. 都没设 → 首次运行时交互式提示输入

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


# 模块级占位：实际使用前由 __main__ 调用 init_client() 初始化
client: Anthropic = None  # type: ignore


def init_client() -> None:
    """初始化模块级 client"""
    global client
    config = ensure_config()
    client = Anthropic(
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=config["timeout_ms"] / 1000.0,
    )


# ============================================================
# Part 2: 工具定义（与 demo1 完全一致）
# ============================================================
# 每次请求随 tools 参数一起发给大模型的「工具说明书」。
# demo2 不增加工具 —— 我们的扩展点是记忆和规划，不是工具本身。

TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可用于文件操作、系统命令、grep 搜索等",
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
]

SYSTEM_PROMPT_BASE = """你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。

你有以下工具可以使用：
1. execute_bash: 执行 shell 命令
2. read_file: 读取文件内容
3. write_file: 写入文件内容

请根据用户需求选择合适的工具完成任务，执行完毕后总结结果并回复用户。"""


# ============================================================
# Part 3: 工具实现 + 路由表（与 demo1 完全一致）
# ============================================================

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
    # TODO: 加白名单 / 审批约束，目前 rm -rf 之类危险命令也能直接执行
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
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
        max_length = 10000
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


# 路由表：工具名 → 实际函数
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}


# ============================================================
# Part 4: 记忆系统（demo2 新增）
# ============================================================
# 用一个 Markdown 文件做跨任务长期记忆：
#   - 每次 task 结束，追加「时间 + 任务 + 结果摘要」
#   - 每次构建 system prompt 时，加载最后 50 行作为「Progressive Context」
#   - 这是滑动窗口：窗口大小固定，旧记忆会随新记忆累积被挤出窗口
#
# 揭示的本质：大模型有上下文窗口限制，本地必须把外部存储的信息
#             有选择地搬运进 prompt。所有记忆方案（向量库、压缩、
#             分层）底层都是「存在哪 + 怎么存 + 搬多少」的问题。

MEMORY_FILE          = "agent_memory.md"
MEMORY_WINDOW_LINES  = 50   # 滑动窗口大小（行数）


def load_memory() -> str:
    """
    加载记忆文件最后 N 行。
    第一次运行时文件不存在 → 返回空字符串（无 Progressive Context）。
    """
    # TODO: 文件超过阈值时调用大模型压缩成关键事实摘要，避免 50 行硬截断丢信息
    if not os.path.exists(MEMORY_FILE):
        return ""
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # 滑动窗口：只取最后 N 行
        window = lines[-MEMORY_WINDOW_LINES:]
        return "".join(window)
    except Exception as e:
        print(f"[警告] 读取记忆文件失败: {e}")
        return ""


def append_memory(task: str, result: str) -> None:
    """
    Task 级结束写入记忆。
    结构：时间戳 + 任务原文 + 结果摘要（前 500 字，防止文件膨胀）
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 结果摘要限长，避免一次任务把整个文件撑爆
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
    """
    构建 system prompt = 基础 prompt + Progressive Context（历史记忆后缀）。

    每次任务开始构建一次，整个 task 内不变（任务进行中记忆不会变化，
    只有任务结束后才会追加新条目）。
    """
    memory = load_memory()
    if verbose:
        if memory:
            n_lines = len(memory.splitlines())
            # 统计任务条数（## 标题数量）
            n_tasks = memory.count("\n## [")
            print(f"[记忆] 已加载 {n_tasks} 条历史任务（{n_lines} 行）作为 Progressive Context:")
            # 展示每条任务的一行摘要
            for line in memory.splitlines():
                if line.startswith("## ["):
                    print(f"   {line}")   # 形如: ## [2026-06-30 02:15:48]
                elif line.startswith("**任务**:") or line.startswith("**任务**: "):
                    print(f"     {line}")
        else:
            print(f"[记忆] 无历史记忆（首次运行或文件为空）")

    if not memory.strip():
        return SYSTEM_PROMPT_BASE
    return SYSTEM_PROMPT_BASE + "\n\n## 历史任务记忆（最近）\n\n" + memory


# ============================================================
# Part 5: 规划系统（demo2 新增）
# ============================================================
# 通过一次独立的大模型调用，让模型先把任务拆成 3-5 个可执行 step，
# 然后再逐步执行。每个 step 内部仍然是 demo1 的 ReAct 循环。
#
# 设计要点：
#   1. 通过 Function Calling 强制结构化返回（json schema）
#   2. 解析失败 / 异常 → 防御性降级到单步执行（不阻塞主流程）

PLAN_TOOL = {
    "name": "submit_plan",
    "description": "提交任务执行规划。把用户任务拆成有序的、可执行的步骤列表。",
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "items": {"type": "string"},
                "description": "3-5 个有序步骤，每个步骤是一个具体的子任务描述",
                "minItems": 1,
                "maxItems": 10,
            }
        },
        "required": ["steps"],
    },
}

PLAN_SYSTEM_PROMPT = """你是任务规划助手。你的工作是把用户的复杂任务拆解成 3-5 个有序、可执行的步骤。

要求：
1. 必须通过 submit_plan 工具返回规划，不要直接输出文本
2. 步骤之间应该有清晰的先后顺序
3. 每个步骤描述要具体，让执行者无需更多上下文就能完成
4. 简单任务可以只返回 1 个步骤（与原任务等价）
5. 步骤数量不超过 10 个"""


def get_plan(user_input: str, verbose: bool = False) -> list:
    """
    调用大模型生成规划。返回步骤列表。

    防御性降级：任何异常或解析失败 → 返回 [user_input]，相当于走 demo1 单步模式。
    """
    # TODO: 拆完后让用户逐 step 确认，避免大模型拆错直接全跑废
    if verbose:
        print(f"\n[规划] 调用大模型拆解任务...")

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=PLAN_SYSTEM_PROMPT,
            tools=[PLAN_TOOL],
            messages=[{"role": "user", "content": user_input}],
        )

        # 从 tool_use block 里提取结构化规划
        for block in response.content:
            if block.type == "tool_use" and block.name == "submit_plan":
                steps = block.input.get("steps", [])
                if isinstance(steps, list) and 1 <= len(steps) <= 10:
                    if verbose:
                        print(f"[规划] 拆解出 {len(steps)} 个步骤:")
                        for i, s in enumerate(steps, 1):
                            preview = str(s)[:60] + ("..." if len(str(s)) > 60 else "")
                            print(f"   Step {i}: {preview}")
                    return steps

        # 大模型没调工具（极端情况）→ 降级
        if verbose:
            print(f"[规划] 大模型未返回结构化规划，降级为单步执行")
        return [user_input]

    except Exception as e:
        # 任何异常 → 降级到 demo1 单步模式
        if verbose:
            print(f"[规划] 规划阶段异常 ({e})，降级为单步执行")
        return [user_input]


# ============================================================
# Part 6: Agent 主循环（多步串联）
# ============================================================
# 与 demo1 的核心区别：
#   - demo1: run_agent(user_input) 内部维护 messages 局部变量，task 结束丢弃
#   - demo2: messages 从 run_agent 外部传入 / 传出，多 step 之间共享
#
# 两层结构：
#   run_agent_step(step, messages) → (result, messages)
#       单个 step 的 ReAct 循环（基本是 demo1 的 run_agent）
#   run_agent(user_input, use_plan) → result
#       编排层：可选规划 → 串联多 step → task 结束写入记忆

MAX_ITERATIONS = 30


def _preview(text: str, limit: int = 60) -> str:
    """截取字符串预览，超长加省略号"""
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览，不需要精细解析每种 block 类型。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def run_agent_step(
    step: str,
    messages: list,
    system_prompt: str,
    verbose: bool = True,
) -> tuple:
    """
    执行单个 step（demo1 的 run_agent 改造版）。

    关键变化：
        - messages 从外部传入（不再每次新建）
        - messages 通过返回值传出（让下一个 step 接力）
        - system_prompt 也从外部传入（包含记忆，整个 task 内不变）

    Returns:
        (result_text, messages) — 结果文本 + 累积的 messages
    """
    # TODO: 当前多 step 只能串行，独立 step 可以并发执行（需要结果不互相依赖）
    # 把当前 step 作为新一条 user 消息追加进共享上下文
    messages = messages + [{"role": "user", "content": step}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n  ----- Step 内 ReAct 第 {loop_idx} 轮 -----")
            _print_messages(messages)

        # 1. 决策
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"  [LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text":
                    print(f"    - text     : {_preview(block.text, 80)}")
                elif block.type == "tool_use":
                    print(f"    - tool_use : {block.name}({block.input})")

        # 2. 判停
        if response.stop_reason != "tool_use":
            if verbose:
                print(f"  [Step 结束] 大模型判断本步骤完成")
            result = "".join(b.text for b in response.content if b.type == "text")
            return result, messages

        # 3. 行动 + 4. 感知
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                fn = AVAILABLE_FUNCTIONS.get(block.name)
                if fn is None:
                    result = f"[错误] 未知工具: {block.name}"
                else:
                    if verbose:
                        print(f"  [执行工具] {block.name}({block.input})")
                    result = fn(**block.input)

                if verbose:
                    print(f"  [工具结果] {_preview(result, 200)}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        messages.append({"role": "user", "content": tool_results})

    return "[错误] Step 超过最大循环次数", messages


def run_agent(user_input: str, use_plan: bool = False, verbose: bool = True) -> str:
    """
    编排：可选规划 → 多步串联执行 → 任务结束写入记忆。

    Args:
        user_input: 用户的原始任务
        use_plan: 是否启用 Plan 模式（True=先拆 step 再执行，False=直接 ReAct）
    """
    # 1. 构建带记忆的 system prompt（整个 task 内不变）
    system_prompt = build_system_prompt(verbose=verbose)

    # 2. 规划阶段（可选）
    if use_plan:
        steps = get_plan(user_input, verbose=verbose)
    else:
        steps = [user_input]
        if verbose:
            print(f"\n[模式] 直接执行（无规划阶段）")

    # 3. 多步串联执行
    #    关键：messages 在多个 step 之间共享 —— step1 的所有工具调用结果
    #    都累积在 messages 中，step2 的大模型能直接看到 step1 的完整轨迹
    messages = []
    final_result = ""

    for i, step in enumerate(steps, 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"执行 Step {i}/{len(steps)}: {step}")
            print(f"{'=' * 60}")

        final_result, messages = run_agent_step(
            step=step,
            messages=messages,
            system_prompt=system_prompt,
            verbose=verbose,
        )

        if verbose:
            preview = _preview(final_result, 120)
            print(f"\n[Step {i} 完成] {preview}")

    # 4. Task 级结束：把整个任务（不是单个 step）写入长期记忆
    append_memory(user_input, final_result)
    if verbose:
        print(f"\n[记忆] 已写入 {MEMORY_FILE}（任务 + 结果摘要）")

    return final_result


# ============================================================
# 交互式入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Demo2 Agent - 带记忆和规划")
    parser.add_argument(
        "--plan",
        action="store_true",
        help="启用 Plan 模式：先调用大模型拆解任务为多 step，再逐步执行",
    )
    args = parser.parse_args()

    # 未配置 API Key 时会交互式提示输入
    init_client()

    print("=" * 60)
    print("Demo2 Agent 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"记忆:   {MEMORY_FILE}（窗口 {MEMORY_WINDOW_LINES} 行）")
    print(f"模式:   {'Plan（先规划再执行）' if args.plan else '直接 ReAct（输入 /plan 切换）'}")
    print("输入 quit / exit 退出")
    print("=" * 60)

    use_plan = args.plan

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

        # REPL 内切换 Plan 模式
        if user_input.lower() in {"/plan", "/p"}:
            use_plan = not use_plan
            print(f"[模式切换] Plan 模式: {'开启' if use_plan else '关闭'}")
            continue
        if user_input.lower() in {"/no-plan", "/np"}:
            use_plan = False
            print(f"[模式切换] Plan 模式: 关闭")
            continue
        if user_input.lower() in {"/memory", "/m"}:
            print(f"\n--- {MEMORY_FILE} 内容 ---")
            print(load_memory() or "(空)")
            print(f"--- end ---")
            continue

        try:
            final = run_agent(user_input, use_plan=use_plan, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
