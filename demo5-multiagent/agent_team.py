#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 - 多 Agent 轴（二）：Team 持久项目组

公式：demo5 = base × 多 Agent
    本文件演示第二条机制 —— Team（30% 权重）

Subagent 的痛点：
    · 干完结果就丢，不能累积记忆
    · 不能互相通信（A 的结果传不到 B）
    · 不能质检打回（结果对不对只能主 Agent 看一眼）

Team 模式的升级：
    · Agent 从"一次性函数"升级为"持久化对象"——有 name / role / messages / inbox
    · Agent 的 messages 跨多次 chat 累积（"我之前做了什么、别人给我发了什么"）
    · inbox 让 Agent 之间能互相塞消息
    · Researcher→Writer→Reviewer 流水线 + 状态机 + 质检总闸门

对应 AutoGen / CrewAI 范式。

单文件按 6 部分组织：
    Part 1: LLM 客户端初始化（沿用 demo1-react）
    Part 2: 工具定义（demo1 三件套，无 subagent）
    Part 3: 工具实现 + 路由表
    Part 4: Agent 类（核心新增——inbox + 长期 messages + chat）
    Part 5: Team 类 + 状态机（核心新增——Researcher→Writer→Reviewer 流水线）
    Part 6: 交互式入口

启动：
    python agent_team.py
"""

import os
import json
import subprocess

from anthropic import Anthropic


# ============================================================
# Part 1: 配置 + LLM 客户端初始化
# ============================================================
# 与 demo1-react 完全一致——API Key 三级回退、智谱 BigModel 兼容网关。

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
    print("如需持久化：请改 agent_team.py 顶部的 API_KEY 变量")
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
# Part 2: 工具定义（demo1 三件套，无 subagent）
# ============================================================
# Team 模式下协调工作交给外部编排器（Team 类），不靠 LLM 调 subagent 工具。

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
]


# ============================================================
# Part 3: 工具实现 + 路由表
# ============================================================
# 三件套实现照搬 demo1-react。

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


# 路由表：工具名 → 实际函数
LOCAL_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
}


# ============================================================
# Part 4: Agent 类（核心新增）
# ============================================================
# 与 agent_sub.py 的 _run_subagent 函数相比，Agent 是一个**持久化对象**：
#   · self.name / self.role          → 固定身份（Subagent 是临时拼角色）
#   · self.messages                  → 长期记忆（Subagent 是一次用完即丢）
#   · self.inbox                     → 收件箱（Subagent 没有通信机制）
#
# 一个 Agent 可以被 chat() 多次——每次 chat 消化 inbox → 把新消息和任务
# 追加到 messages → 走一个完整的 ReAct 循环 → 返回响应。messages 跨多次
# chat 累积，构成"我之前做了什么、别人给我发了什么"的长期记忆。

STEP_MAX_ITERATIONS = 10


def _preview(text, limit: int = 60) -> str:
    """截取字符串预览，超长加省略号"""
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

        self.inbox: list = []                                 # 收件箱：[(sender, message), ...]
        self.messages: list = []                              # 长期记忆（跨多次 chat 累积）
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
            print(f"{self.indent}[Agent · {self.name}] 收到来自 {sender} 的消息："
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

        # 3) 走 ReAct 循环（与 agent_sub.py 的 _react_loop 同构，只是挂到实例上）
        return self._react_loop()

    # ------------------------------------------------------------
    # ReAct 循环——和 agent_sub.py 主循环同构，只是挂到 Agent 实例上
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
# Part 5: Team 类 + 状态机（核心新增）
# ============================================================
# Team 是 Agent 的协调器——demo5 的"项目组"。
# 它提供 4 个核心动作：
#   · recruit(name, role)        招募成员
#   · send(a, b, msg)            一对一通信（依赖注入 / 质检反馈打回）
#   · broadcast(sender, msg)     群发（成员完成任务后通报全员）
#   · dismiss()                  解散团队
#
# 与 agent_sub.py 的 Subagent 相比：
#   · Subagent 是函数，一次性；Agent 是对象，可被 chat() 多次唤起
#   · Subagent 无 inbox；Agent 有 inbox，可被其他 Agent 塞消息
#   · Subagent messages 用完即丢；Agent messages 跨多次 chat 累积
#
# 固定三角色流水线（不靠 LLM 动态规划）：
#   Researcher → Writer → Reviewer
#   · Researcher：调研主题，输出要点列表
#   · Writer：基于要点写结构化研究报告（markdown）
#   · Reviewer：验收报告，不通过则打回重做（最多 3 次）
#
# 状态机（任务级，不是 Agent 级）：
#   researching → writing → reviewing → ┬→ passed (终态，报告输出)
#                                       └→ redoing → writing → reviewing → ...
#                                           (3 次不过 = failed)

# 流水线阶段
STAGE_RESEARCHING = "researching"
STAGE_WRITING     = "writing"
STAGE_REVIEWING   = "reviewing"
STAGE_REDOING     = "redoing"
STAGE_PASSED      = "passed"
STAGE_FAILED      = "failed"

MAX_REVIEW_ATTEMPTS = 3       # Writer 最多被质检打回 3 次


class Team:
    """一组互相协作的 Agent + 一个外部编排器。"""

    def __init__(self, tools: list, local_fns: dict, verbose: bool = True):
        self.tools     = tools
        self.local_fns = local_fns
        self.verbose   = verbose
        self.agents: dict = {}
        self.stage = STAGE_RESEARCHING      # 当前流水线阶段

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
        """群发：sender → 所有其他成员"""
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
    # 流水线入口（事件驱动状态机）
    # ------------------------------------------------------------
    def run_pipeline(self, topic: str) -> dict:
        """
        固定 Researcher→Writer→Reviewer 流水线。

        事件驱动：
          1. recruit 三角色
          2. Researcher.chat(调研主题) → 结果 send 给 Writer
          3. Writer.chat(基于要点写报告) → 结果 send 给 Reviewer
          4. Reviewer.chat(质检报告) → passed / 打回 Writer 重做
          5. passed → 把报告写到 <topic>.md → dismiss
        """
        if self.verbose:
            print(f"\n[run_pipeline] 研究报告主题：{topic}")

        # Step 1: recruit 三角色
        if self.verbose:
            print(f"\n[run_pipeline] 开始招募团队成员...")
        self.recruit("Researcher", "研究员——调研主题，输出要点列表")
        self.recruit("Writer",     "撰稿人——基于研究员的要点写结构化研究报告")
        self.recruit("Reviewer",   "质检员——验收报告，不通过则打回重做")

        # Step 2: Researcher 调研
        self.stage = STAGE_RESEARCHING
        if self.verbose:
            print(f"\n[stage: {self.stage}] Researcher 开始调研")
        research_task = (
            f"请调研以下主题，输出 5-8 个要点（用编号列表）：\n\n"
            f"主题：{topic}\n\n"
            f"可以用 execute_bash / read_file 查本地资料，也可以直接基于你的知识调研。"
            f"输出格式：编号列表，每条 1-2 句话。"
        )
        research_result = self.agents["Researcher"].chat(research_task)
        # 把调研结果发给 Writer
        self.send("Researcher", "Writer",
                  f"调研要点如下，请基于这些要点写报告：\n\n{research_result}")

        # Step 3 + 4: Writer 写 → Reviewer 质检（循环）
        review_attempts = 0
        writer_report = ""
        while True:
            # Step 3: Writer 写报告
            self.stage = STAGE_WRITING if review_attempts == 0 else STAGE_REDOING
            if self.verbose:
                print(f"\n[stage: {self.stage}] Writer 开始写报告"
                      f"{'（第 ' + str(review_attempts + 1) + ' 次撰写）' if review_attempts > 0 else ''}")
            write_task = (
                f"基于研究员给的要点，写一份结构化的研究报告（markdown 格式）。\n"
                f"主题：{topic}\n\n"
                f"要求：\n"
                f"- 标题、概述、正文（分章节）、结论\n"
                f"- 语言简洁清晰，避免空话\n"
                f"- 直接输出 markdown 正文，不要包裹在代码块里"
            )
            writer_report = self.agents["Writer"].chat(write_task)
            # 把报告发给 Reviewer
            self.send("Writer", "Reviewer",
                      f"待验收的报告（第 {review_attempts + 1} 次提交）：\n\n{writer_report}")

            # Step 4: Reviewer 质检
            self.stage = STAGE_REVIEWING
            if self.verbose:
                print(f"\n[stage: {self.stage}] Reviewer 开始质检（第 {review_attempts + 1} 次）")
            review_task = (
                "请严格验收上条报告。检查：内容是否覆盖主题、结构是否清晰、"
                "是否有明显错误或空话。可以用 read_file / execute_bash 复查。\n\n"
                "严格只输出 JSON（不要 markdown 代码块、不要任何解释）：\n"
                '{"pass": true|false, "feedback": "若不通过，说明具体怎么改"}'
            )
            verdict_text = self.agents["Reviewer"].chat(review_task)
            # 解析 JSON——prompt 已要求纯 JSON，解析失败默认不通过
            try:
                v = json.loads(verdict_text.strip())
                passed = bool(v.get("pass") is True)
                feedback = v.get("feedback") or ""
            except json.JSONDecodeError:
                passed = False
                feedback = f"[质检员输出不是合法 JSON，默认不通过] 原文：{verdict_text}"

            review_attempts += 1

            if passed:
                self.stage = STAGE_PASSED
                if self.verbose:
                    print(f"\n[stage: {self.stage}] Reviewer 通过（第 {review_attempts} 次质检）")
                self.broadcast("Reviewer",
                               f"报告通过质检（第 {review_attempts} 次质检）")
                break

            if review_attempts >= MAX_REVIEW_ATTEMPTS:
                self.stage = STAGE_FAILED
                if self.verbose:
                    print(f"\n[stage: {self.stage}] Reviewer {MAX_REVIEW_ATTEMPTS} 次未通过，"
                          f"流水线失败")
                self.broadcast("Reviewer",
                               f"报告 {MAX_REVIEW_ATTEMPTS} 次质检未通过，流水线终止")
                break

            # 打回 Writer 重做
            self.stage = STAGE_REDOING
            if self.verbose:
                print(f"\n[stage: {self.stage}] Reviewer 第 {review_attempts} 次未通过，"
                      f"打回 Writer 重做")
                print(f"         反馈：{_preview(feedback, 150)}")
            self.send("Reviewer", "Writer",
                      f"质检未通过（第 {review_attempts} 次）。反馈：{feedback}")

        # Step 5: 把报告落盘 + dismiss
        # 文件名：把 topic 里的特殊字符替换掉
        safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in topic)
        if len(safe_name) > 40:
            safe_name = safe_name[:40]
        report_path = f"{safe_name}.md"
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(f"# 研究报告：{topic}\n\n")
                f.write(f"> 由 demo5-multiagent/agent_team.py 的 Researcher→Writer→Reviewer 流水线生成\n")
                f.write(f"> 质检次数：{review_attempts} / 状态：{self.stage}\n\n")
                f.write(writer_report)
            if self.verbose:
                print(f"\n[run_pipeline] 报告已写入 {report_path}")
        except Exception as e:
            print(f"[run_pipeline] 写入报告失败: {e}")

        results = {
            "topic":     topic,
            "stage":     self.stage,
            "attempts":  review_attempts,
            "report":    writer_report,
            "path":      report_path,
        }
        self.dismiss()
        return results


# ============================================================
# Part 6: 交互式入口
# ============================================================

def main():
    init_client()

    print("=" * 60)
    print("Demo5 (Team) 已启动")
    print(f"模型:   {MODEL}")
    print(f"网关:   {BASE_URL}")
    print(f"工具:   {', '.join(t['name'] for t in LOCAL_TOOLS)}")
    print("=" * 60)
    print("本节演示 Team 模式——Researcher→Writer→Reviewer 流水线 + 质检总闸门。")
    print("输入任意主题，会生成一份研究报告并落盘。")
    print("quit / exit 退出")
    print("=" * 60)

    while True:
        try:
            topic = input("\n研究报告主题: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not topic:
            continue
        if topic.lower() in {"quit", "exit", "q"}:
            print("再见！")
            break

        try:
            team = Team(tools=LOCAL_TOOLS, local_fns=LOCAL_FUNCTIONS, verbose=True)
            result = team.run_pipeline(topic)
            print(f"\n{'=' * 60}")
            print(f"[流水线完成] 状态：{result['stage']} / 质检次数：{result['attempts']}")
            print(f"[报告路径] {result['path']}")
            print(f"[报告预览]")
            print("-" * 60)
            preview = result["report"]
            if len(preview) > 1000:
                preview = preview[:1000] + "\n\n... [已截断，完整内容见文件]"
            print(preview)
        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    main()
