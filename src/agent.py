"""
Agent - 主循环逻辑
"""

from .llm_client import LLMClient
from .tools.definitions import TOOLS
from .tools.executor import execute_tool


# 系统提示
SYSTEM_PROMPT = """你是一个有用的 AI 助手，可以通过工具来帮助用户完成任务。

你有以下工具可以使用：
1. execute_bash: 执行 shell 命令
2. read_file: 读取文件内容
3. write_file: 写入文件内容

请根据用户的需求，选择合适的工具来完成任务。执行完工具后，请总结结果并回复用户。"""


class Agent:
    """Agent 主类，处理与 LLM 的交互循环"""

    def __init__(self, llm_client: LLMClient):
        """
        初始化 Agent

        Args:
            llm_client: LLM 客户端实例
        """
        self.llm = llm_client
        self.messages = []

    def run(self, user_input: str) -> str:
        """
        运行 Agent，处理用户输入

        Args:
            user_input: 用户输入

        Returns:
            最终回复
        """
        # 添加用户消息
        self.messages.append({
            "role": "user",
            "content": user_input
        })

        # 主循环
        while True:
            # 调用 LLM
            response = self.llm.chat(
                messages=self.messages,
                tools=TOOLS,
                system=SYSTEM_PROMPT
            )

            # 检查停止原因
            if response.stop_reason == "tool_use":
                # 需要执行工具
                print(f"\n[调用工具...]")

                # 执行工具
                tool_results = self._execute_tools(response.content)

                # 添加 assistant 消息
                self.messages.append({
                    "role": "assistant",
                    "content": response.content
                })

                # 添加 tool_result 消息
                self.messages.append({
                    "role": "user",
                    "content": tool_results
                })

            elif response.stop_reason == "end_turn":
                # 对话结束，返回结果
                return self._extract_text(response.content)

            else:
                # 其他情况（如 max_tokens）
                return self._extract_text(response.content) + f"\n[停止原因: {response.stop_reason}]"

    def _execute_tools(self, content: list) -> list:
        """
        执行工具调用

        Args:
            content: 响应内容列表

        Returns:
            工具结果列表
        """
        results = []

        for block in content:
            if block.type == "tool_use":
                tool_name = block.name
                tool_input = block.input

                print(f"  - {tool_name}({tool_input})")

                # 执行工具
                result = execute_tool(tool_name, tool_input)

                # 构造 tool_result
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result
                })

        return results

    def _extract_text(self, content: list) -> str:
        """
        从响应内容中提取文本

        Args:
            content: 响应内容列表

        Returns:
            文本内容
        """
        texts = []
        for block in content:
            if block.type == "text":
                texts.append(block.text)
        return "\n".join(texts)

    def clear(self):
        """清空消息历史"""
        self.messages = []
