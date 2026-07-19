#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 - 多 Agent 轴（一）：Subagent 一次性外包

公式：demo5 = base × 多 Agent
    本文件演示第一条机制 —— Subagent（70% 权重）

核心问题：
    单 Agent 的 messages 越叠越长，遇到相互独立的子任务时上下文污染严重。
    Subagent = 把独立子任务"分包出去"：派一个独立 Agent 干完返回结果即销毁。

关键设计：
    · 独立 context：Subagent 有自己的 messages，与主 Agent 完全隔离
    · 无状态：不注入 Rules / 不注入记忆
    · 结束即销毁：循环结束返回结果摘要，messages/prompt 全部丢弃
    · 工具集去 subagent：子循环看不到 subagent 工具，防无限递归

对应 Claude Code 的 Task tool / Cursor 的 agent / Devin 的子任务派发。

单文件按 4 部分组织：
    Part 1: LLM 客户端初始化（沿用 demo1-react）
    Part 2: 工具定义（demo1 三件套 + subagent）
    Part 3: 工具实现 + 路由表（subagent 不在路由表，主循环单独拦截）
    Part 4: 主循环 + Subagent 循环（共用 _react_loop）

启动：
    python agent_sub.py
"""

import os
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
    print("如需持久化：请改 agent_sub.py 顶部的 API_KEY 变量")
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
# Part 2: 工具定义（demo1 三件套 + subagent）
# ============================================================
# 每次请求随 tools 参数一起发给大模型，相当于一份「工具说明书」。
# 大模型拿到说明书后就知道自己有哪些本地能力，但真正的执行发生在本地代码里。
#
# 三个本地小工具（execute_bash / read_file / write_file）照搬 demo1-react。
# 新增 `subagent`——主 Agent 通过调用它派生一个独立 Agent 循环。

ALL_TOOLS = [
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
        "name": "subagent",
        "description": (
            "委派一个独立的 Subagent 来完成子任务。Subagent 拥有独立的角色（system_prompt）"
            "和独立的上下文（messages），执行完返回结果摘要后即销亡。\n\n"
            "**使用时机**：**相互独立的子任务**才用——每个子任务派一个 Subagent。"
            "当用户输入包含「1) 2) 3)」这种编号列表、且列出 2 个及以上相互独立的子任务时，"
            "**必须**为每个子任务**调用一次 subagent 工具**，由对应 Subagent 完成。\n\n"
            "**不适合的场景**：后一步要用前一步结果的链式任务（例如「先读文件，再把内容排序后写入新文件」），"
            "这种应让主 Agent 自己顺序完成——Subagent 干完就销毁，无法把结果传给下一个 Subagent。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "description": "Subagent 的角色，例如「Python 工程师」/「文件统计员」"},
                "task": {"type": "string", "description": "交给 Subagent 完成的具体任务描述"},
            },
            "required": ["role", "task"],
        },
    },
]

SYSTEM_PROMPT = """你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。

遇到相互独立的子任务时（如「1) 统计文件数；2) 读某文件首行」这种编号列表），
请为每个子任务派一个 Subagent——它们各自独立干完汇报。
有链式依赖的任务（先 A 再 B）请自己顺序做。"""


# ============================================================
# Part 3: 工具实现 + 路由表
# ============================================================
# 每个工具是一个普通 Python 函数：
#   - 错误信息也字符串化返回给大模型，让它自己看到错误后调整策略
#   - 设置超时，防止死循环或长时间阻塞
#   - shell=True 让命令拥有更强能力（风险换能力）
#
# 注意：subagent 不在 LOCAL_FUNCTIONS——它需要"启动一个独立 Agent 循环"的特殊逻辑，
# 在 _react_loop 里单独拦截。

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,            # 让命令拥有更强能力
            capture_output=True,
            text=True,
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


# 路由表：工具名 → 实际函数（调度核心）
# 当大模型说「我要调用 execute_bash」时，Agent 通过这张表把名字映射到具体函数并执行。
# 注：subagent 不在此表——它在 _react_loop 里单独拦截（启动独立 Agent 循环）。
LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}


def build_subagent_system_prompt(role: str) -> str:
    """
    Subagent 的 system prompt = 角色化指令 + "只做被吩咐的事"。

    与主 Agent 相比：
      · 不注入 Rules（demo5 不做规则轴）
      · 不注入记忆（避免无关历史任务干扰，保持专注）
    """
    return (
        f"你是一个被委派来的 Subagent。你的角色是：**{role}**。\n"
        f"请专注于交给你完成的任务，做完后用一两句话汇报结果。"
    )


# ============================================================
# Part 4: 主循环 + Subagent 循环（共用 _react_loop）
# ============================================================
# 把 demo1 的 run_agent 抽象成一个通用 ReAct 循环——主 Agent 和 Subagent 都跑它，
# 差别只在：
#   · messages 是否独立：主 Agent 用一份；每次调 subagent 新建一份
#   · system_prompt 是否带角色：主 Agent 用全局 SYSTEM_PROMPT；Subagent 只拼角色
#   · 工具集是否含 subagent：主 Agent 含；Subagent **去掉 subagent 防递归**

STEP_MAX_ITERATIONS = 10   # 单个 ReAct 循环的最大轮数


def _preview(text, limit: int = 60) -> str:
    """截取字符串预览，超长加省略号"""
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        print(f"  [{i}] {msg['role']:<9}: {_preview(msg['content'])}")
    print()


def _react_loop(
    messages: list,
    tools: list,
    local_fns: dict,
    system_prompt: str,
    tools_for_subagent: list,
    depth: int,
    verbose: bool,
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
        indent:                  打印缩进，让 subagent 轨迹可视化区分

    每个 ReAct 迭代：
        1. 发请求拿响应
        2. 若 stop_reason != tool_use → 返回文本（这一层循环结束）
        3. 遍历响应里的 tool_use 块：
           · subagent → 转手 _run_subagent 起一个独立子循环
           · 其它 → 走 local_fns 函数调用
        4. 把 tool_result 灌回 messages，进入下一轮
    """
    response = None
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
                    print(f"{indent}  [工具 · 本地] {name}")
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
        · 独立的 messages：从 [{"role":"user","content":task}] 开始，
          与主 Agent 的 messages 完全隔离——拿不到主 Agent 的历史对话，
          也不会污染主 Agent 的上下文
        · 独立 system_prompt：基于 role 拼一个角色化系统提示，不注入 Rules / 记忆
        · 工具集去掉 subagent：阻止 Subagent 派生下一层 Subagent（防无限递归）
        · 一次性生命周期：循环结束 → 返回结果摘要 → messages / prompt 全部丢弃
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


def run_agent(user_input: str, verbose: bool = True) -> str:
    """
    主 Agent 循环。

    与 demo1 的差异：
        - LLM 看到工具列表里有 subagent，遇到相互独立的子任务时会主动委派
        - tools_for_subagent 在所有工具里去掉 subagent——一旦 LLM 调 subagent，
          就用这份工具集启动子循环（子循环不会再调 subagent，堵死递归）
    """
    messages = [{"role": "user", "content": user_input}]

    if verbose:
        print(f"\n{'=' * 60}")
        print(f"主 Agent 启动 ReAct 循环")
        print(f"{'=' * 60}")
        _print_messages(messages)

    # 给 subagent 准备的工具集：去掉 subagent 自身
    tools_for_subagent = [t for t in ALL_TOOLS if t.get("name") != "subagent"]

    return _react_loop(
        messages=messages,
        tools=ALL_TOOLS,
        local_fns=LOCAL_FUNCTIONS,
        system_prompt=SYSTEM_PROMPT,
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
    print("Demo5 (Subagent) 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"工具:   {', '.join(t['name'] for t in ALL_TOOLS)}")
    print("        其中 `subagent` 可派生独立 Agent（已自动禁止递归）")
    print("=" * 60)
    print("输入 quit / exit 退出")
    print("建议任务：1) 统计 demo5-multiagent 下 .py 文件数；2) 读 agent_sub.py 第 1 行注释")

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
            final = run_agent(user_input, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
