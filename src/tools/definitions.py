"""
工具定义 - 工具的 schema 定义
"""

TOOLS = [
    {
        "name": "execute_bash",
        "description": "执行任意 shell 命令，可以用于文件操作、系统命令等",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 shell 命令"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "read_file",
        "description": "读取文件内容，返回文件的文本内容",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径"
                }
            },
            "required": ["path"]
        }
    },
    {
        "name": "write_file",
        "description": "写入文件内容，如果文件不存在会创建，如果存在会覆盖",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要写入的文件路径"
                },
                "content": {
                    "type": "string",
                    "description": "要写入的文件内容"
                }
            },
            "required": ["path", "content"]
        }
    }
]
