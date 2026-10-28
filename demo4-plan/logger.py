#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Logger 层 — 文件日志（logs/agent.log）

教学点：控制台输出是给人看的（render.py），文件日志是给事后排查留的——
谁在什么时刻做了什么、花了多少 tokens、报了什么错。标准库 logging +
FileHandler；FileHandler 显式 encoding="utf-8" 避开 Windows GBK 乱码坑。
所有 demo 的本文件为同内容副本（demo2 是 canonical）。
"""

import logging
import os


LOG_DIR = "logs"                        # 日志目录（相对 demo 运行目录）
LOG_FILE = os.path.join(LOG_DIR, "agent.log")

_logger = logging.getLogger("agent")
_ready = False                          # setup_logging 之前静默丢弃（测试/被 import 时不落盘）


def setup_logging(log_dir: str = LOG_DIR) -> None:
    """在 __main__ 启动时调用一次：建目录 + 挂 FileHandler。

    log_dir 参数化是为了测试能指向 tmp_path；正常运行用默认 logs/。
    幂等：重复调用不重复挂 handler。
    """
    global _ready
    if _ready:
        return
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "agent.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _ready = True


def _log(level: int, msg: str) -> None:
    if _ready:
        _logger.log(level, msg)


def log_start(model: str, base_url: str) -> None:
    _log(logging.INFO, f"agent 启动 model={model} base_url={base_url}")


def log_user(text: str) -> None:
    _log(logging.INFO, f"[user] {text[:200]}")


def log_tool(name: str, args) -> None:
    _log(logging.INFO, f"[tool] {name} {str(args)[:200]}")


def log_usage(round_idx: int, usage) -> None:
    if usage is None:
        return
    _log(logging.INFO, f"[usage] 第 {round_idx} 轮 输入 {getattr(usage, 'input_tokens', 0)} "
                       f"输出 {getattr(usage, 'output_tokens', 0)}")


def log_compact(before: int, after: int) -> None:
    _log(logging.INFO, f"[compact] {before} 条 → {after} 条")


def log_error(msg: str) -> None:
    _log(logging.ERROR, msg)


def log_exit() -> None:
    _log(logging.INFO, "agent 退出")
