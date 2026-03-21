"""
LLM 客户端 - 封装 Anthropic API 调用
"""

import os
from anthropic import Anthropic


class LLMClient:
    """LLM 客户端，封装 API 调用"""

    def __init__(self, api_key: str = None, model: str = "claude-sonnet-4-6"):
        """
        初始化 LLM 客户端

        Args:
            api_key: Anthropic API Key，如果为空则从环境变量读取
            model: 模型名称
        """
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("请设置 ANTHROPIC_API_KEY 环境变量或传入 api_key 参数")

        self.client = Anthropic(api_key=self.api_key)
        self.model = model

    def chat(self, messages: list, tools: list = None, system: str = None) -> object:
        """
        发送消息，支持 tool_use

        Args:
            messages: 消息列表
            tools: 工具定义列表
            system: 系统提示

        Returns:
            API 响应对象
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4096,
        }

        if tools:
            kwargs["tools"] = tools

        if system:
            kwargs["system"] = system

        return self.client.messages.create(**kwargs)
