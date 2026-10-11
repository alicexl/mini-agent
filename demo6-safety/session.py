#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo2 会话层 — 会话持久化（会话级记忆）

memory/<会话ID>.jsonl：每个会话一个文件，一行一条消息、只追加（compact
触发后整文件重写，见 rewrite_session）。会话 ID 用时间戳（如 20261007_031512）
——自描述、按文件名自然排序。

重启后 /resume 扫描目录列出历史会话，选中即恢复——模型是无状态的，
所谓会话只是每次请求带上的 messages 数组，历史填回去就「记起」了。
（对照 Claude Code：~/.claude/projects/<项目路径转码>/<会话ID>.jsonl，
它是 uuid 会话 ID + 按项目分目录；教学版存在项目内 memory/ 目录即可。）
"""

import json
from datetime import datetime
from pathlib import Path

# 会话文件目录（与项目级记忆 MEMORY.md 同住 memory/）
SESSIONS_DIR = "memory"


def new_session_id() -> str:
    """会话 ID：时间戳，自描述且按文件名排序"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def session_file(session_id: str) -> Path:
    return Path(SESSIONS_DIR) / f"{session_id}.jsonl"


def _to_jsonable(msg: dict) -> dict:
    """
    message → 可 json 序列化的 dict。

    assistant 消息的 content 是 SDK block 对象（TextBlock / ToolUseBlock /
    ThinkingBlock），anthropic SDK 是 pydantic 底座，model_dump() 现成转 dict；
    我们自拼的 dict（user 输入、tool_result）原样通过。
    回读时 dict 形态的 content API 直接认，无需还原成 SDK 对象。
    """
    content = msg.get("content")
    if isinstance(content, list):
        content = [b.model_dump() if hasattr(b, "model_dump") else b for b in content]
    return {"role": msg.get("role"), "content": content}


def append_messages(session_id: str, messages: list) -> None:
    """增量追加（一行一条）。目录/文件惰性创建——第一轮对话结束才落盘。"""
    if not messages:
        return
    path = session_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(_to_jsonable(msg), ensure_ascii=False) + "\n")


def rewrite_session(session_id: str, messages: list) -> None:
    """全量重写——compact 改写了历史，纯追加语义在此断裂，整文件重写。"""
    path = session_file(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(_to_jsonable(msg), ensure_ascii=False) + "\n")


def load_history(session_id: str) -> list:
    """读取整个会话文件还原 messages 列表（dict 形态 API 直接认）。"""
    path = session_file(session_id)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _first_prompt(path: Path) -> str:
    """只读文件第一行（一定是首条 user 消息），取其内容当会话摘要。"""
    with open(path, encoding="utf-8") as f:
        head = f.readline()
    try:
        msg = json.loads(head)
    except json.JSONDecodeError:
        return "(空会话)"
    content = msg.get("content", "")
    return content if isinstance(content, str) else "(空会话)"


def list_sessions() -> list:
    """
    扫描会话文件，按修改时间从新到旧返回 (session_id, 修改时间, 首条输入)。
    无索引文件——本地扫目录开销很小，老会话未必还会打开（Claude Code 同款做法）。
    """
    root = Path(SESSIONS_DIR)
    if not root.exists():
        return []
    files = sorted(
        root.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [
        (p.stem, datetime.fromtimestamp(p.stat().st_mtime), _first_prompt(p))
        for p in files
    ]
