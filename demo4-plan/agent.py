#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4-plan - 规划轴的 Agent

在 demo1-react（base）基础上叠加「规划轴」：
    + plan：把"列步骤"显式化（自动决策版）
        · LLM 自主判断任务复杂度——简单任务直接 ReAct，复杂任务先 plan 列步骤
        · 对应 Claude Code 的 TodoWrite 工具
    + Skill：预消化的工作流
        · skills/*.md 用 YAML frontmatter 声明 triggers 关键词
        · 用户输入命中某 skill 的 triggers 时，把该 skill 的 body 注入 system prompt
        · 对应 Claude Code 的 Skill 工具（description 匹配后注入）

公式：demo4 = base × 规划

「规划」的两类增量：
    (A) 单次任务的内部规划：plan 让 LLM 把多步拆解显式化、可观测、可追踪
    (B) 跨任务的复用规划：Skill 把"常见任务的最佳实践步骤"预先固化，相似任务直接套用

单文件按 5 个 Part 组织：
    Part 1: LLM 客户端初始化（同 demo1）
    Part 2: 本地工具定义（demo1 三件套 + 新增 plan）
    Part 3: 本地工具实现 + 路由表
    Part 4: Skill 加载器（扫描 skills/ → frontmatter 解析 → 关键词匹配）
    Part 5: Agent 主循环（ReAct + Skill body 注入 system prompt）

启动：
    python agent.py
"""

import os
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
# Part 2: 本地工具定义（demo1 三件套 + 新增 plan）
# ============================================================
# 每次请求随 tools 参数一起发给大模型，相当于一份「工具说明书」。
# 大模型拿到说明书后就知道自己有哪些本地能力，但真正的执行发生在本地代码里。
#
# demo1 的 3 件套（execute_bash / read_file / write_file）保留不变。
# demo4 在此基础上**新增 1 个本地工具 plan** —— 把"规划"显式化。
#
# 为什么需要 plan？
#   ReAct 循环是「走一步看一步」——LLM 每轮只决策下一步。
#   对 3 步以上的复杂任务，LLM 容易在中途跑偏、忘了目标、重复尝试。
#   plan 让 LLM 先把整个任务的步骤列出来（规划阶段），
#   再逐步执行（执行阶段），每完成一步更新状态——这是 Claude Code 的核心机制之一。
#
# 何时用 plan？由 LLM 自动判断：
#   · 简单任务（1-2 步、单一工具）→ 不用，直接 ReAct
#   · 复杂任务（3+ 步、多工具协作、有依赖）→ 先 plan 列步骤再执行

LOCAL_TOOLS = [
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
        # === demo4 新增 ===
        "name": "plan",
        "description": (
            "更新当前任务的待办步骤清单（规划用）。"
            "**使用时机**：只在**复杂的多步任务**（3 步以上、多工具协作、步骤间有依赖）开头规划阶段调用，"
            "把整个任务拆成显式 step 列表；后续每完成一步就再调一次本工具更新对应 step 的状态。\n\n"
            "**不要滥用**：简单的一两步任务（如「统计 .py 文件数」「读一下某文件」）"
            "请直接用 execute_bash / read_file 完成，**不要**为了用而用 plan。\n\n"
            "**状态规则**：同一时刻最多只能有 1 个 step 处于 in_progress；"
            "开始下一步前，把上一步从 in_progress 改为 completed，把下一步从 pending 改为 in_progress。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "完整的待办清单（每次调用都传全量，覆盖式更新）",
                    "items": {
                        "type": "object",
                        "properties": {
                            "subject": {
                                "type": "string",
                                "description": "这一步要做什么（动词开头，如「统计 .py 文件数」「读取 agent.py」）",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "这一步的当前状态",
                            },
                        },
                        "required": ["subject", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
        {
            # === demo4 新增 ===
            "name": "use_skill",
            "description": (
                "激活一个 skill 获取其详细工作流正文。"
                "当用户任务匹配 system prompt 中列出的某个可用 skill 时，先调用此工具获取该 skill 的步骤，"
                "然后严格按照返回的工作流执行。"
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要激活的 skill 名称（见 system prompt 中的可用 Skills 列表）",
                    }
                },
                "required": ["name"],
            },
        },
    ]


# ============================================================
# Part 3: 本地工具实现 + 路由表
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
            encoding="utf-8",      # GBK Windows 下 text=True 会崩，显式 UTF-8（CLAUDE.md 工程规范）
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


# todos.md 落盘路径——让用户能看到 Agent 当前的「待办清单」
TODOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "todos.md")

# 状态标记符号（终端可视化）
_STATUS_MARK = {
    "pending":     "[ ]",
    "in_progress": "[→]",
    "completed":   "[✓]",
}


def plan(todos: list) -> str:
    """
    更新待办清单（demo4 新增）。

    与其它工具的核心区别：
        - execute_bash / read_file / write_file 都是「执行一步」
        - plan 是「规划整段」——把多步任务一次性铺开，让 LLM 自己和用户都能看到全局

    落盘到 todos.md 只是给人看的副产物；真正的作用是把"规划阶段"在 ReAct 轨迹里显式化。
    """
    if not isinstance(todos, list):
        return "[错误] todos 必须是数组"

    lines = ["# 当前任务待办清单\n"]
    valid_statuses = {"pending", "in_progress", "completed"}
    n_by_status = {"pending": 0, "in_progress": 0, "completed": 0}

    for i, item in enumerate(todos, 1):
        if not isinstance(item, dict):
            lines.append(f"{i}. [?] 格式错误：{item}")
            continue
        subject = str(item.get("subject", "")).strip()
        status = str(item.get("status", "pending")).strip()
        if status not in valid_statuses:
            status = "pending"
        n_by_status[status] += 1
        mark = _STATUS_MARK[status]
        lines.append(f"{i}. {mark} {subject}")

    text = "\n".join(lines)

    # 同步落盘（覆盖式）——用户可随时打开 todos.md 查看
    try:
        with open(TODOS_FILE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    except Exception as e:
        text += f"\n[警告] 写 todos.md 失败: {e}"

    # 终端 pretty-print（让 verbose 模式轨迹清晰）
    print("\n" + "─" * 50)
    print(text)
    print("─" * 50)

    total = len(todos)
    done = n_by_status["completed"]
    active = n_by_status["in_progress"]
    pending = n_by_status["pending"]
    return (
        f"[已更新] 共 {total} 步：✓{done} 完成 / →{active} 进行中 / [ ]{pending} 待办。"
        f"清单已同步写入 {TODOS_FILE}"
    )


# ---- use_skill（demo4 新增） ----
# 定义放在 LOCAL_FUNCTIONS 之前，因为路由表引用它。
# _SKILLS 在 main() 启动时赋值，运行时调用没问题。


def use_skill(name: str) -> str:
    """激活 skill 获取工作流正文（tool 负责"拿技能"）"""
    skill = _SKILLS.get(name)
    if not skill:
        return f"[错误] 未知 skill: {name}。可用: {', '.join(_SKILLS.keys()) or '(无)'}"
    print(f"\n[Skill] LLM 激活 skill: {name}")
    return skill["body"]


# 路由表：工具名 → 实际函数（调度核心）
# 当大模型说「我要调用 execute_bash」时，Agent 通过这张表把名字映射到具体函数并执行。
LOCAL_FUNCTIONS = {
    "use_skill":    use_skill,
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "plan":         plan,
}


# ============================================================
# Part 4: Skill 加载器（demo4 核心新增之二）
# ============================================================
# Skill = 预消化的工作流。每个 skill 是 skills/ 目录下的一个 .md 文件：
#   ---
#   name: review
#   description: 代码审查工作流...
#   triggers: ["代码审查", "code review", ...]
#   ---
#   # 工作流正文（LLM 收到匹配任务时按此执行）
#
# 三层协作（渐加载分离）：
#   · system prompt — "我有哪些技能"（元信息清单：name + description + triggers）
#   · use_skill 工具 — "拿技能"（LLM 主动调用获取 body）
#   · skill 文件 — "技能具体怎么做"（tool_result 返回工作流正文）
#
# 对应 Claude Code 的 Skill 工具——system prompt 列可用 skill，LLM 自主调用获取 body。

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

# 模块级 skills 字典——main() 启动时赋值，use_skill 工具从中读取 body
_SKILLS: dict = {}

# YAML frontmatter 正则——非贪婪匹配首尾 ---
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple:
    """
    简易 YAML frontmatter 解析（只支持 name/description/triggers 三字段）。
    不引入 PyYAML 依赖——教学代码保持零额外依赖。
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    fm_text, body = match.group(1), match.group(2)
    meta = {}

    # 解析 name / description（单行字符串）
    for key in ("name", "description"):
        m = re.search(rf"^{key}:\s*(.+?)\s*$", fm_text, re.MULTILINE)
        if m:
            meta[key] = m.group(1).strip().strip('"').strip("'")

    # 解析 triggers（JSON 数组成 [a, b, c] 形式）
    m = re.search(r"^triggers:\s*\[(.*?)\]", fm_text, re.MULTILINE | re.DOTALL)
    if m:
        items = [
            t.strip().strip('"').strip("'").strip()
            for t in m.group(1).split(",")
            if t.strip()
        ]
        meta["triggers"] = items
    else:
        meta["triggers"] = []

    return meta, body.strip()


def load_skills() -> dict:
    """
    扫描 skills/*.md，解析 frontmatter，返回 {name: {description, triggers, body}}。
    目录不存在或空时返回空字典（Agent 仍可运行，只是没有 skill 可激活）。
    """
    skills = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills

    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(SKILLS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            print(f"[Skill] 读取 {fname} 失败: {e}")
            continue

        meta, body = _parse_frontmatter(raw)
        name = meta.get("name") or fname[:-3]  # 缺 name 用文件名
        skills[name] = {
            "description": meta.get("description", ""),
            "triggers":    meta.get("triggers", []),
            "body":        body,
            "file":        fname,
        }

    return skills


def build_skill_metadata_section(skills: dict) -> str:
    """
    生成 system prompt 里的「## 可用 Skills」段。
    system prompt 放"我有哪些技能"——LLM 据此决定是否调用 use_skill 获取 body。
    """
    if not skills:
        return ""
    lines = ["\n## 可用 Skills\n"]
    for name, info in skills.items():
        lines.append(f"- **{name}**: {info['description']}")
        if info["triggers"]:
            lines.append(f"  - 触发词: {', '.join(info['triggers'])}")
    lines.append("\n当用户任务匹配某个 skill 时，先调用 `use_skill` 获取该 skill 的详细工作流，然后严格按要求执行。")
    return "\n".join(lines) + "\n"


# ============================================================
# Part 5: Agent 主循环（ReAct + Skill body 注入）
# ============================================================
# 与 demo1 的核心区别：
#   - 工具列表从 3 个扩到 5 个（新增 plan + use_skill），走 API tools 参数
#   - system prompt 两层叠加：基础说明 + 可用 Skills 元信息
#   - LLM 自主调用 use_skill 获取 skill body（body 走 tool_result 进 messages）
#   - 工具（tools 参数）与 skill（system prompt 元信息 + use_skill 工具）渐加载分离

MAX_ITERATIONS = 30


def _preview(text, limit: int = 60) -> str:
    text = str(text).replace("\n", " ").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _print_messages(messages: list) -> None:
    """调试打印——只是给人看的预览。"""
    print(f"[messages] 当前 {len(messages)} 条消息")
    for i, msg in enumerate(messages):
        content = msg.get("content", "")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        parts.append(f"[调用工具 {block.get('name')}]")
                    elif block.get("type") == "tool_result":
                        parts.append(str(block.get("content", ""))[:100])
                else:
                    t = getattr(block, "type", None)
                    if t == "text":
                        parts.append(getattr(block, "text", ""))
                    elif t == "tool_use":
                        parts.append(f"[调用工具 {getattr(block, 'name', '')}]")
            content = "\n".join(parts)
        print(f"  [{i}] {msg.get('role', '?'):<9}: {_preview(content)}")
    print()


def build_system_prompt(skills: dict) -> str:
    """
    构造 system prompt（两层叠加）：
        1. 基础说明（角色）
        2. 可用 Skills 元信息（"我有哪些技能"）

    工具清单走 API tools 参数；skill body 走 use_skill 工具（tool_result 进 messages）。
    """
    parts = [
        "你是一个有用的助手，可以通过工具与本地系统交互完成任务。",
        f"当前工作目录（相对路径基准）：{os.getcwd()}。用户提到的文件若在此目录下，直接用文件名，不要再额外加目录前缀。",
    ]

    # 可用 Skills 元信息
    skill_meta = build_skill_metadata_section(skills)
    if skill_meta:
        parts.append(skill_meta)

    return "\n".join(parts)


def run_agent(
    user_input: str,
    verbose: bool = True,
) -> str:
    """
    ReAct 主循环（同 demo1，工具集扩为 5 个 + system prompt 含 Skills 元信息）。
    """
    system_prompt = build_system_prompt(_SKILLS)
    messages = [{"role": "user", "content": user_input}]

    for loop_idx in range(1, MAX_ITERATIONS + 1):
        if verbose:
            print(f"\n----- ReAct 第 {loop_idx} 轮 -----")
            _print_messages(messages)

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=LOCAL_TOOLS,
            messages=messages,
        )

        if verbose:
            print(f"[LLM 决策] stop_reason = {response.stop_reason}")
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    print(f"  - text     : {_preview(block.text, 100)}")
                elif block.type == "tool_use":
                    print(f"  - tool_use : {block.name}({_preview(str(block.input), 80)})")

        if response.stop_reason != "tool_use":
            if verbose:
                print(f"[任务结束] 大模型判断完成")
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            name = block.name
            args = block.input or {}
            fn = LOCAL_FUNCTIONS.get(name)
            if fn is None:
                result = f"[错误] 未知工具: {name}"
            else:
                if verbose and name != "plan":
                    # plan 内部已 pretty-print，这里不重复打印
                    print(f"\n[执行工具] {name}({_preview(args, 80)})")
                try:
                    result = str(fn(**args))
                except Exception as e:
                    result = f"[错误] 工具 {name} 执行失败: {e}"

            if verbose:
                print(f"[工具结果] {_preview(result, 200)}")
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})

    return f"[错误] 超过最大循环次数（{MAX_ITERATIONS}）"


# ============================================================
# 交互式入口
# ============================================================

def main():
    init_client()

    print("=" * 60)
    print("Demo4-plan Agent 已启动（规划轴）")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"Skills: {SKILLS_DIR}")
    print("=" * 60)

    # ---- 启动时加载所有 skills ----
    global _SKILLS
    _SKILLS = load_skills()
    if _SKILLS:
        print(f"[Skills] 加载 {len(_SKILLS)} 个：")
        for name, info in _SKILLS.items():
            print(f"  - {name}: {info['description'][:60]}")
            print(f"    触发词: {', '.join(info['triggers'])}")
    else:
        print(f"[Skills] 未在 {SKILLS_DIR} 找到任何 .md 文件（Agent 仍可运行）")

    print(f"\n[Tools] 共 {len(LOCAL_TOOLS)} 个本地工具："
          f"{', '.join(t['name'] for t in LOCAL_TOOLS)}")
    print("[Tools] 其中 `plan` 用于复杂多步任务的规划，`use_skill` 用于获取 skill 工作流正文")

    print("\n命令:   /skills 查看已加载 skills / quit 退出")
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

        if user_input.lower() in {"/skills", "/s"}:
            if not _SKILLS:
                print(f"\n[Skills] 无（目录 {SKILLS_DIR} 为空或不存在）")
            else:
                print(f"\n--- 已加载 {len(_SKILLS)} 个 Skills ---")
                for name, info in _SKILLS.items():
                    print(f"  [{name}] {info['description']}")
                    print(f"    触发词: {', '.join(info['triggers'])}")
                    print(f"    来源:   {info['file']}")
            continue

        try:
            final = run_agent(user_input=user_input, verbose=True)
            print(f"\n助手: {final}")
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
