#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo1 工具层 — 工具定义 + 工具实现 + 路由表

从 agent_single.py 拆出（原 Part 2「工具」整块，逻辑零改动）。
拆分动机：工具是独立资产——主循环只认识两样东西：
    TOOLS               发给大模型的「工具说明书」
    AVAILABLE_FUNCTIONS 本地调度的路由表（工具名 → 函数）
工具内部怎么实现（超时、编码、错误处理），主循环一概不关心。
"""

import os
import subprocess


# ============================================================
# 工具定义（Function Calling 标准格式）
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
    {
        "name": "edit",
        "description": (
            "精确替换文件中的一段文本（string replacement）。"
            "比 write_file 整文件覆写更精细，适合改一行 / 改一个值。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":        {"type": "string",  "description": "要编辑的文件路径"},
                "old":         {"type": "string",  "description": "要替换的原文本（必须精确匹配，含空格/缩进）"},
                "new":         {"type": "string",  "description": "替换为的新文本"},
                "replace_all": {"type": "boolean", "description": "是否替换全部匹配处（默认 false，只替换第一处）"},
            },
            "required": ["path", "old", "new"],
        },
    },
]


# ============================================================
# 工具实现 + 路由表
# ============================================================
# 每个工具是一个普通 Python 函数：
#   - 错误信息也字符串化返回给大模型，让它自己看到错误后调整策略
#   - 设置超时，防止死循环或长时间阻塞

def execute_bash(command: str) -> str:
    """执行 shell 命令"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            encoding="utf-8",      # GBK Windows 下 text=True 会崩，显式 UTF-8
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
        max_length = 20000
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


def edit(path: str, old: str, new: str, replace_all: bool = False) -> str:
    """精确替换文件中的文本"""
    try:
        if not os.path.exists(path):
            return f"[错误] 文件不存在: {path}"
        if not old:
            return "[错误] old 不能为空"
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        occurrences = content.count(old)
        if occurrences == 0:
            return f"[错误] 未找到匹配文本，请用 read_file 确认精确内容"
        new_content = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
        which = f"全部 {occurrences} 处" if replace_all else f"第 1 处（共 {occurrences} 处）"
        return f"[成功] {path} 替换 {which}"
    except Exception as e:
        return f"[错误] 编辑文件失败: {e}"


# 路由表：工具名 → 实际函数（调度核心）
# 当大模型说「我要调用 execute_bash」时，Agent 通过这张表把名字映射到具体函数并执行。
AVAILABLE_FUNCTIONS = {
    "execute_bash": execute_bash,
    "read_file":    read_file,
    "write_file":   write_file,
    "edit":         edit,
}
