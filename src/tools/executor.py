"""
工具实现 - 具体的工具执行逻辑
"""

import subprocess
import os


def execute_tool(name: str, params: dict) -> str:
    """
    执行工具并返回结果

    Args:
        name: 工具名称
        params: 工具参数

    Returns:
        工具执行结果（字符串形式）
    """
    if name == "execute_bash":
        return execute_bash(params["command"])
    elif name == "read_file":
        return read_file(params["path"])
    elif name == "write_file":
        return write_file(params["path"], params["content"])
    else:
        return f"错误：未知工具 '{name}'"


def execute_bash(command: str) -> str:
    """
    执行 shell 命令

    Args:
        command: 要执行的命令

    Returns:
        命令输出或错误信息
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60
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
        return "[错误] 命令执行超时（60秒）"
    except Exception as e:
        return f"[错误] 命令执行失败: {str(e)}"


def read_file(path: str) -> str:
    """
    读取文件内容

    Args:
        path: 文件路径

    Returns:
        文件内容或错误信息
    """
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"

        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 限制返回内容长度
        max_length = 10000
        if len(content) > max_length:
            content = content[:max_length] + f"\n\n... [内容已截断，共 {len(content)} 字符]"

        return content

    except UnicodeDecodeError:
        return "[错误] 文件不是有效的文本文件或编码不支持"
    except Exception as e:
        return f"[错误] 读取文件失败: {str(e)}"


def write_file(path: str, content: str) -> str:
    """
    写入文件内容

    Args:
        path: 文件路径
        content: 文件内容

    Returns:
        操作结果
    """
    try:
        # 确保目录存在
        dir_path = os.path.dirname(path)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        return f"[成功] 文件已写入: {path} ({len(content)} 字符)"

    except Exception as e:
        return f"[错误] 写入文件失败: {str(e)}"
