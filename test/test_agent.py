"""
Agent 测试（使用 Mock）
"""

import os
import sys
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agent import Agent


class MockContentBlock:
    """模拟响应内容块"""
    def __init__(self, block_type, **kwargs):
        self.type = block_type
        for key, value in kwargs.items():
            setattr(self, key, value)


class MockResponse:
    """模拟 API 响应"""
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


class TestAgent:
    """测试 Agent 逻辑"""

    def test_agent_initialization(self):
        """测试 Agent 初始化"""
        mock_client = Mock()
        agent = Agent(mock_client)

        assert agent.llm == mock_client
        assert agent.messages == []

    def test_agent_clear(self):
        """测试清空消息历史"""
        mock_client = Mock()
        agent = Agent(mock_client)
        agent.messages = [{"role": "user", "content": "test"}]

        agent.clear()
        assert agent.messages == []

    def test_extract_text(self):
        """测试文本提取"""
        mock_client = Mock()
        agent = Agent(mock_client)

        content = [
            MockContentBlock("text", text="Hello "),
            MockContentBlock("text", text="World")
        ]

        result = agent._extract_text(content)
        assert result == "Hello \nWorld"

    def test_execute_tools_single(self):
        """测试执行单个工具"""
        mock_client = Mock()
        agent = Agent(mock_client)

        content = [
            MockContentBlock("tool_use", name="execute_bash", id="test_id", input={"command": "echo hello"})
        ]

        results = agent._execute_tools(content)

        assert len(results) == 1
        assert results[0]["type"] == "tool_result"
        assert results[0]["tool_use_id"] == "test_id"
        assert "hello" in results[0]["content"]

    def test_execute_tools_multiple(self):
        """测试执行多个工具"""
        mock_client = Mock()
        agent = Agent(mock_client)

        content = [
            MockContentBlock("tool_use", name="execute_bash", id="id1", input={"command": "echo a"}),
            MockContentBlock("tool_use", name="execute_bash", id="id2", input={"command": "echo b"})
        ]

        results = agent._execute_tools(content)

        assert len(results) == 2
        assert results[0]["tool_use_id"] == "id1"
        assert results[1]["tool_use_id"] == "id2"

    def test_run_end_turn_immediately(self):
        """测试直接返回结果（无需工具）"""
        mock_client = Mock()
        agent = Agent(mock_client)

        # 模拟响应：直接返回文本
        mock_response = MockResponse(
            stop_reason="end_turn",
            content=[MockContentBlock("text", text="这是回复")]
        )
        mock_client.chat.return_value = mock_response

        result = agent.run("你好")

        assert "这是回复" in result
        assert len(agent.messages) == 1  # 只有用户消息

    def test_run_tool_use_then_end(self):
        """测试工具调用后返回结果"""
        mock_client = Mock()
        agent = Agent(mock_client)

        # 第一次调用：需要工具
        first_response = MockResponse(
            stop_reason="tool_use",
            content=[MockContentBlock("tool_use", name="execute_bash", id="tool_1", input={"command": "echo test"})]
        )
        # 第二次调用：返回结果
        second_response = MockResponse(
            stop_reason="end_turn",
            content=[MockContentBlock("text", text="任务完成")]
        )

        mock_client.chat.side_effect = [first_response, second_response]

        result = agent.run("执行命令")

        assert "任务完成" in result
        assert len(agent.messages) == 3  # user + assistant(tool_use) + user(tool_result)


class TestToolDefinitions:
    """测试工具定义"""

    def test_tools_structure(self):
        """测试工具定义结构"""
        from src.tools.definitions import TOOLS

        assert len(TOOLS) == 3

        tool_names = [t["name"] for t in TOOLS]
        assert "execute_bash" in tool_names
        assert "read_file" in tool_names
        assert "write_file" in tool_names

    def test_tool_has_required_fields(self):
        """测试工具包含必需字段"""
        from src.tools.definitions import TOOLS

        for tool in TOOLS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "properties" in tool["input_schema"]
            assert "required" in tool["input_schema"]
