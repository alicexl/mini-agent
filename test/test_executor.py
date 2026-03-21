"""
工具执行器测试
"""

import os
import tempfile
import pytest
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.executor import execute_tool, execute_bash, read_file, write_file


class TestExecuteBash:
    """测试 execute_bash 工具"""

    def test_echo_command(self):
        """测试 echo 命令"""
        result = execute_bash("echo hello")
        assert "hello" in result

    def test_pwd_command(self):
        """测试 pwd 命令"""
        result = execute_bash("pwd")
        assert "/" in result or "\\" in result  # Windows 或 Unix

    def test_invalid_command(self):
        """测试无效命令"""
        result = execute_bash("nonexistent_command_12345")
        assert "错误" in result or "exit code" in result or "stderr" in result

    def test_command_with_pipe(self):
        """测试管道命令"""
        result = execute_bash("echo hello | tr 'a-z' 'A-Z'")
        assert "HELLO" in result


class TestReadFile:
    """测试 read_file 工具"""

    def test_read_existing_file(self):
        """测试读取存在的文件"""
        # 创建临时文件
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("test content")
            temp_path = f.name

        try:
            result = read_file(temp_path)
            assert "test content" in result
        finally:
            os.unlink(temp_path)

    def test_read_nonexistent_file(self):
        """测试读取不存在的文件"""
        result = read_file("/nonexistent/path/file.txt")
        assert "错误" in result or "不存在" in result

    def test_read_empty_file(self):
        """测试读取空文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            temp_path = f.name

        try:
            result = read_file(temp_path)
            assert result == "" or "成功" in result or len(result) == 0
        finally:
            os.unlink(temp_path)


class TestWriteFile:
    """测试 write_file 工具"""

    def test_write_new_file(self):
        """测试写入新文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.txt")
            result = write_file(file_path, "hello world")

            assert "成功" in result
            assert os.path.exists(file_path)

            with open(file_path, 'r') as f:
                content = f.read()
            assert content == "hello world"

    def test_overwrite_file(self):
        """测试覆盖已存在的文件"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("old content")
            temp_path = f.name

        try:
            result = write_file(temp_path, "new content")
            assert "成功" in result

            with open(temp_path, 'r') as f:
                content = f.read()
            assert content == "new content"
        finally:
            os.unlink(temp_path)

    def test_write_to_nested_directory(self):
        """测试写入到嵌套目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "subdir", "nested", "test.txt")
            result = write_file(file_path, "nested content")

            assert "成功" in result
            assert os.path.exists(file_path)


class TestExecuteTool:
    """测试 execute_tool 分发函数"""

    def test_dispatch_execute_bash(self):
        """测试分发到 execute_bash"""
        result = execute_tool("execute_bash", {"command": "echo test"})
        assert "test" in result

    def test_dispatch_read_file(self):
        """测试分发到 read_file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write("dispatch test")
            temp_path = f.name

        try:
            result = execute_tool("read_file", {"path": temp_path})
            assert "dispatch test" in result
        finally:
            os.unlink(temp_path)

    def test_dispatch_write_file(self):
        """测试分发到 write_file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "dispatch.txt")
            result = execute_tool("write_file", {"path": file_path, "content": "dispatched"})
            assert "成功" in result

    def test_unknown_tool(self):
        """测试未知工具"""
        result = execute_tool("unknown_tool", {})
        assert "未知工具" in result or "错误" in result
