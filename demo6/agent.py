#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo6 - 上下文压缩（Context Compression）

demo1–5 我们一直在「加东西」——工具、记忆、MCP、subagent、Team。但有一个矛盾一直
被回避：**多轮 ReAct 会让 messages 越攒越多**。每一轮 = 大模型回复 1 条 + 工具结果
1 条，几个 step 下来轻松破 30 条。任何大模型的上下文窗口都有上限，一旦撞顶就崩。

demo6 不再加新能力，转而解决「怎么不让 messages 撞顶」——**动态压缩对话历史**。

四个思路（详见讲稿第 2 章）：
    1. 扩窗口：换更大 context 的模型（如 GLM-5.2 的 1M）——治标
    2. 限循环：限制 step 次数，爆了就重启——粗暴
    3. 阶段化：只留最近 N 条（demo2 的滑动窗口思想）——易丢关键信息
    4. 压缩：把旧消息让大模型做成摘要，保留最近几条原文 ← demo6 选这条

基于 demo3 改造（一减一加）：
    - 减法：
        · 去掉 MCP（Part 5 整段砍掉，工具全部本地化）
        · 去掉 demo2 的 agent_memory.md 滑动窗口（Part 4 换成压缩机制）
    - 加法：
        · 新增 `compact_messages()`——按阈值把旧消息压成一条 summary
        · 在 Plan 每个 step 开头先调一次 compact_messages

单文件按 6 个 Part 组织：
    Part 1: LLM 客户端初始化（沿用 demo1-5）
    Part 2: 本地工具定义 + 实现（沿用 demo3 的 4 个工具，去掉 MCP）
    Part 3: Rules 加载器（沿用 demo3）
    Part 4: 上下文压缩 compact_messages（核心新增）
    Part 5: Agent 主循环（沿用 demo3 的 Plan 决策 + 共享 messages ReAct，
            但每个 step 开头先 compact）
    Part 6: 交互式入口

压缩参数（为演示效果故意调低，生产环境请调大）：
    COMPACT_THRESHOLD  = 8   # messages 达到 8 条就触发压缩
    KEEP_RECENT        = 4   # 保留最近 4 条不动

启动：
    python agent.py
"""

import os
import random
import subprocess

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
# Part 2: 本地工具定义 + 实现（沿用 demo3，去掉 MCP）
# ============================================================
# 4 个本地工具：execute_bash / read_file / write_file / plan
# 与 demo3 完全一致，只是不再从 MCP server 拉远程工具。

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


# 天气演示数据（与 demo4/5 一致，确保行为可对照）
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


LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "weather":      weather,
}


# ============================================================
# Part 3: Rules 加载器（沿用 demo3）
# ============================================================
# .agent/rules.md 内容作为 system prompt 的后缀注入。
# 大模型在生成代码 / 选工具时，会参考这份"规范"——
# 这是不改代码、不改工具，仅通过上下文约束 Agent 行为的最简方式。

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
# Part 4: 上下文压缩 compact_messages（核心新增）
# ============================================================
# 为什么需要压缩？
#   每轮 ReAct = 1 条 assistant（含 tool_use）+ 1 条 user（含 tool_result），
#   多个 step 累积下来轻松破 30 条，撞顶即崩。
#
# 压缩原理：
#   1) 检查 len(messages) 是否达到阈值，没到就原样返回
#   2) 永远保留 system prompt（不在 messages 数组里，是 client.messages.create
#      的 system 参数——本函数只管 messages 数组）
#   3) 保留最近 KEEP_RECENT 条不动（这些是最精确的当前上下文）
#   4) 找"安全边界"——切点不能落在 tool_use / tool_result 中间
#      （Anthropic API 要求 tool_use 紧跟 tool_result，切断会报错）
#   5) 旧消息拼成纯文本 → 调 LLM 生成摘要
#   6) 重组：[summary as user] + [过渡 tool 消息] + [最近 KEEP_RECENT 条]
#
# 重组后的结构：
#   [user: summary] [user: 过渡通知] [recent-3] [recent-2] [recent-1] [recent-0]
#
# 注意：过渡消息故意做成「assistant 文本 + user 工具结果」的对子，
#   这样大模型不会因为消息序列突变而困惑（看起来像它刚刚跑完一个工具）。

# ---- 压缩参数（演示用，故意调低便于看到效果）----
COMPACT_THRESHOLD = 8        # messages 达到 8 条触发压缩
KEEP_RECENT       = 4        # 保留最近 4 条不动


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _is_tool_use_block(block) -> bool:
    """判断一个 content block 是不是 tool_use（兼容 dict / 对象）"""
    t = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
    return t == "tool_use"


def _find_safe_boundary(messages: list, ideal_cut: int) -> int:
    """
    从 ideal_cut 向前回溯，跳过以 assistant(tool_use) 结尾的消息——
    tool_use 必须和后面的 tool_result 在同一区，切断会报 API 错误。

    只需检查 tool_use：tool_result 必跟在 tool_use 之后，
    只要 old 区不以 tool_use 结尾，配对就不会被切断。
    （old 区以 tool_result 结尾是安全的——它的 tool_use 在更前面，同在 old 区。）
    """
    cut = ideal_cut
    while cut > 1:
        msg = messages[cut - 1]
        content = msg.get("content")
        if (msg.get("role") == "assistant" and isinstance(content, list)
                and any(_is_tool_use_block(b) for b in content)):
            cut -= 1
        else:
            break
    return cut


def _messages_to_text(messages: list) -> str:
    """把若干条 message 序列化成供 LLM 做摘要的纯文本。
    content 可能是 str / list[Block] / list[dict]，f-string 自动 str() 即可——
    摘要 LLM 足够聪明，不需要逐 block 类型精细解析。
    """
    return "\n".join(
        f"[{msg.get('role', '?')}]: {msg.get('content', '')}"
        for msg in messages
        if msg.get("content")
    )


def _summarize(old_messages: list) -> str:
    """调一次 LLM，把旧消息做成摘要。返回纯文本摘要。"""
    transcript_text = _messages_to_text(old_messages)
    summary_prompt = (
        "你是一个对话历史压缩器。下面是 Agent 之前的多轮工具调用对话历史（已序列化）。\n"
        "请生成一段**紧凑摘要**，要求：\n"
        "1. 保留**关键信息**：用户最初的目标、已经完成的步骤、已经创建/修改的文件、"
        "已得到的关键数据 / 结论\n"
        "2. 保留任何**未完成的承诺**或**待办事项**\n"
        "3. 丢弃冗余的工具调用细节（具体命令、返回的原始字节、重复的中间结果）\n"
        "4. 用第三人称、陈述句，300 字以内\n\n"
        "不要输出任何解释 / markdown 标题 / 前后缀，直接写摘要正文。\n"
        "---\n对话历史：\n"
        + transcript_text
    )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=800,
        system="你是一个精确的对话摘要助手。",
        messages=[{"role": "user", "content": summary_prompt}],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def compact_messages(
    messages: list,
    threshold: int = COMPACT_THRESHOLD,
    keep_recent: int = KEEP_RECENT,
    verbose: bool = True,
) -> list:
    """
    检查并按需压缩 messages。

    - 不足 threshold → 原样返回（不复制）
    - 达到 threshold → 把「除了最近 keep_recent 条之外」的旧消息压成一条 summary，
      返回新组装的 messages（[summary] + [过渡] + [最近 keep_recent 条]）

    注意：本函数**不**原地修改 messages，而是返回一个新 list，由调用方替换。
    （因为切点可能涉及 tool_use/tool_result 对，原地改容易出错。）
    """
    n = len(messages)
    if n < threshold:
        if verbose:
            print(f"[compact] {n} < {threshold}，无需压缩")
        return messages

    if verbose:
        print(f"\n[compact] {'─' * 50}")
        print(f"[compact] 触发压缩：{n} 条 ≥ 阈值 {threshold}")

    # 理想切点：从第 keep_recent 条往前切
    # messages 索引：[0 .. n-keep_recent-1] = 旧消息；[n-keep_recent .. n-1] = 最近
    ideal_cut = max(1, n - keep_recent)
    cut = _find_safe_boundary(messages, ideal_cut)

    if verbose and cut != ideal_cut:
        print(f"[compact] 安全边界调整：理想切点 {ideal_cut} → 实际 {cut}"
              f"（避免切断 tool_use/tool_result 对）")

    old_messages = messages[:cut]
    recent_messages = messages[cut:]

    if not old_messages:
        if verbose:
            print(f"[compact] 旧消息为空（切点已退到 0），跳过")
        return messages

    if verbose:
        print(f"[compact] 旧消息 {len(old_messages)} 条 → 送 LLM 摘要")
        print(f"[compact] 最近保留 {len(recent_messages)} 条（含安全边界调整）")

    # 调 LLM 生成摘要
    summary = _summarize(old_messages)
    if verbose:
        print(f"[compact] 摘要生成完毕（{len(summary)} 字符）：")
        print(f"         {_preview(summary, 200)}")

    # 组装新 messages
    # 1) summary 作为一条新的 user 消息（相当于"压缩后的历史"）
    # 2) 一条「过渡消息」——伪装成 assistant 已经收到摘要并准备继续
    #    这里用一个简单的 assistant 文本 + user 文本对子，避免引入假的 tool_use
    new_messages = []
    new_messages.append({
        "role": "user",
        "content": f"[对话历史摘要]\n{summary}",
    })
    new_messages.append({
        "role": "assistant",
        "content": [{
            "type": "text",
            "text": (
                "好的，我已读取历史摘要，了解了之前的进展。"
                "请给我最新的任务，我会基于这份摘要继续。"
            ),
        }],
    })
    new_messages.extend(recent_messages)

    if verbose:
        print(f"[compact] 重组完成：{n} 条 → {len(new_messages)} 条")
        print(f"[compact] {'─' * 50}\n")

    return new_messages


# ============================================================
# Part 5: Agent 主循环（沿用 demo3 的 Plan 决策，加入压缩钩子）
# ============================================================
# 与 demo3 的结构基本一致：
#   - 顶层决策 1 轮：LLM 选 plan or 直接执行
#   - Plan 场景：遍历 steps，共享 messages，每个 step 走 ReAct
#   - 非 Plan 场景：直接走 ReAct
#
# 唯一的区别：**在每个 step 开始前先调 compact_messages(messages)**。
# 这样多 step 任务会在执行过程中自动触发若干次压缩，把旧消息始终压在阈值以下。

STEP_MAX_ITERATIONS = 10


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览，不需要精细解析每种 block 类型。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def build_system_prompt(verbose: bool = False) -> str:
    """组合 system prompt = 基础说明 + Rules（不再注入 agent_memory.md）"""
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

    return "\n".join(parts)


def _dispatch_tool_local(
    name: str,
    args: dict,
    local_fns: dict,
    verbose: bool,
) -> str:
    """本地工具分发器（demo6 没有 MCP，全部走本地）。"""
    if verbose:
        print(f"  [工具 · 本地] {name}({_preview(args, 80)})")
    try:
        return str(local_fns[name](**args))
    except Exception as e:
        return f"[错误] 本地工具 {name} 执行失败: {e}"


def run_agent_steps(
    messages: list,
    tools: list,
    local_fns: dict,
    system_prompt: str,
    verbose: bool,
    initial_response=None,
) -> str:
    """
    ReAct 子循环。共享 messages：每轮的 assistant / tool_result 都追加到同一份
    messages 列表，跨轮 / 跨 step 积累上下文。
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
            result = _dispatch_tool_local(
                block.name, block.input or {}, local_fns, verbose,
            )
            if verbose:
                print(f"  [结果] {_preview(result, 120)}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": str(result),
            })
        messages.append({"role": "user", "content": tool_results})

        response = None

    return f"[Step 未在 {STEP_MAX_ITERATIONS} 轮内完成]"


def run_agent(
    user_input: str,
    all_tools: list,
    local_fns: dict,
    system_prompt: str,
    verbose: bool = True,
) -> str:
    """
    Agent 主循环：先做 1 轮顶层决策，按 LLM 是否调 plan 分两条路径。

    与 demo3 的区别：**每个 step 开头先调 compact_messages**。
    """
    messages = [{"role": "user", "content": user_input}]

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"顶层决策：plan or 直接执行")
        print(f"{'=' * 60}")
        _print_messages(messages)

    # 顶层决策前也压一次（首条消息不会触发，但保持习惯一致）
    messages = compact_messages(messages, verbose=verbose)

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

    plan_block = next(
        (b for b in response.content if b.type == "tool_use" and b.name == "plan"),
        None,
    )

    if plan_block:
        # ===== Plan 场景：遍历 steps，共享 messages =====
        steps = plan_block.input.get("steps", []) or []
        if verbose:
            print(f"\n[Plan] LLM 拆解 {len(steps)} 个步骤，共享 messages 逐步执行")

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

        step_tools = [t for t in all_tools if t.get("name") != "plan"]

        # 遍历 steps——每个 step 开头先压缩
        for i, step in enumerate(steps, 1):
            if verbose:
                print(f"\n{'─' * 60}")
                print(f"[Step {i}/{len(steps)}] {step}")
                print(f"{'─' * 60}")
            # ★ demo6 核心：step 开头先压缩
            messages = compact_messages(messages, verbose=verbose)
            step_msg = (
                f"【Step {i}/{len(steps)}】请只执行这一步：\n{step}\n\n"
                f"完成后立即 end_turn，不要预先做后续 step。"
            )
            messages.append({"role": "user", "content": step_msg})
            run_agent_steps(
                messages, step_tools, local_fns,
                system_prompt, verbose,
            )

        # 最后一次压缩 + 最终总结
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"[Plan] 所有步骤完成，请求 LLM 最终总结")
            print(f"{'=' * 60}")
        messages = compact_messages(messages, verbose=verbose)
        return run_agent_steps(
            messages, step_tools, local_fns,
            system_prompt, verbose,
        )

    # ===== 非 Plan 场景 =====
    if verbose:
        print(f"\n[非 Plan] 直接进入 ReAct 循环（共享 messages）")
    return run_agent_steps(
        messages, all_tools, local_fns,
        system_prompt, verbose, initial_response=response,
    )


# ============================================================
# Part 6: 交互式入口
# ============================================================


def main():
    init_client()

    print("=" * 60)
    print("Demo6 Agent 已启动（上下文压缩版）")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"Rules:  {RULES_FILE}")
    print(f"压缩:   threshold={COMPACT_THRESHOLD} 条触发 / keep_recent={KEEP_RECENT} 条保留")
    print("=" * 60)
    print("本节演示上下文压缩——多 step 任务会在执行中触发压缩。")
    print("命令：")
    print("  quit   退出")
    print("  其它   当作新任务输入（建议给一个多步骤任务以观察压缩过程）")
    print("=" * 60)

    system_prompt = build_system_prompt(verbose=True)

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
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
