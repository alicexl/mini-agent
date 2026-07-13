#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo1 - 最简 Agent 实现

演示 Agent 的底层原理 = LLM (大脑) + 工具 (手脚) + 循环 (ReAct)

单文件按 4 部分组织（对应教学音频的「全局架构」章节）：
    Part 1: LLM 客户端初始化
    Part 2: 工具定义（Function Calling schema）
    Part 3: 工具实现 + 路由表（调度核心）
    Part 4: Agent 主循环（感知 / 行动 / 决策）
"""

import os
import subprocess

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================
# 网关、模型、超时均写死，用户只需配置 API Key（两种方式）：
#   1. 直接修改下面的 API_KEY
#   2. 都没设 → 首次运行时交互式提示输入
# 默认走智谱 BigModel 的 Anthropic 兼容网关 + glm-5.2[1m] 模型。

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
# Part 2: 工具定义（Function Calling 标准格式）
# ============================================================
# 每次请求随 tools 参数一起发给大模型，相当于一份「工具说明书」。
# 大模型拿到说明书后就知道自己有哪些本地能力，但真正的执行发生在本地代码里。

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
]

SYSTEM_PROMPT = """你是一个有用的助手，可以通过工具与系统交互，帮助用户完成任务。

你有以下工具可以使用：
1. execute_bash: 执行 shell 命令
2. read_file: 读取文件内容
3. write_file: 写入文件内容

请根据用户需求选择合适的工具完成任务，执行完毕后总结结果并回复用户。"""


# ============================================================
# Part 3: 工具实现 + 路由表
# ============================================================
# 每个工具是一个普通 Python 函数：
#   - 错误信息也字符串化返回给大模型，让它自己看到错误后调整策略
#   - 设置超时，防止死循环或长时间阻塞
#   - shell=True 让命令拥有更强能力（风险换能力）

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
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file": read_file,
    "write_file": write_file,
}


# ============================================================
# Part 4: Agent 主循环（感知 / 行动 / 决策 = ReAct）
# ============================================================
# 每一轮：把整个 messages 重新发给大模型 → 大模型决策是否调用工具
#       → 调用就执行工具并把结果追加回 messages → 再发给大模型
#       → 直到 stop_reason != "tool_use"（任务完成）或达到 MAX_ITERATIONS。

MAX_ITERATIONS = 30  # 防止大模型陷入死循环


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


def run_agent(user_input: str, verbose: bool = True) -> str:
    """
    运行 Agent 处理一次用户任务。

    Args:
        user_input: 用户的任务目标
        verbose: 是否打印每一轮的决策与行动（教学演示建议开启）

    Returns:
        Agent 的最终文本回复
    """
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n{'=' * 60}")
            print(f"第 {loop_idx} 轮 ReAct 循环")
            print(f"{'=' * 60}")
            _print_messages(messages)

        # ---- 决策：大模型思考下一步 ----
        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"\n[LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text":
                    preview = block.text[:80] + ("..." if len(block.text) > 80 else "")
                    print(f"  - text      : {preview}")
                elif block.type == "tool_use":
                    print(f"  - tool_use  : {block.name}({block.input})")

        # ---- 判断是否结束 ----
        if response.stop_reason != "tool_use":
            if verbose:
                print(f"\n[循环结束] 大模型判断任务完成，退出循环")
            return "".join(b.text for b in response.content if b.type == "text")

        # ---- 行动：本地执行工具 + 感知：收集结果 ----
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                # 通过路由表把工具名分发到实际函数
                fn = AVAILABLE_FUNCTIONS.get(block.name)
                if fn is None:
                    result = f"[错误] 未知工具: {block.name}"
                else:
                    if verbose:
                        print(f"\n[执行工具] {block.name}({block.input})")
                    result = fn(**block.input)

                if verbose:
                    preview = str(result)[:200] + (
                        "..." if len(str(result)) > 200 else ""
                    )
                    print(f"[工具结果] {preview}")

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,  # Tool ID 精确匹配每次调用
                        "content": result,
                    }
                )

        # 把工具结果作为 user 消息追加进 messages，下一轮大模型就能看到
        messages.append({"role": "user", "content": tool_results})

    return "[错误] 超过最大循环次数（{}），可能陷入死循环".format(MAX_ITERATIONS)


# ============================================================
# 交互式入口：真实 Agent 演示
# ============================================================
# 改好上面的 API_KEY 后直接运行，
# 在终端输入任意任务（统计文件、查信息、写脚本……），观察每一轮 ReAct 循环。
# 输入 quit / exit / q 退出。

if __name__ == "__main__":
    # 未配置 API Key 时会交互式提示输入
    init_client()

    print("=" * 60)
    print("Demo1 Agent 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print("输入 quit / exit 退出")
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
            final = run_agent(user_input, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")
