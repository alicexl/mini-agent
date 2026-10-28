#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""demo2-memory/logger.py 文件日志测试（canonical 实现，其余 demo 为同内容副本）"""

import pytest

from conftest import import_demo_module

logger = import_demo_module("demo2-memory", "logger")


@pytest.fixture(autouse=True)
def _reset_logger(monkeypatch):
    """每个测试前复位：清 handler + 复位 _ready，保证测试互相隔离"""
    logger._logger.handlers.clear()
    monkeypatch.setattr(logger, "_ready", False)


def test_setup_creates_log_file_with_content(tmp_path):
    logger.setup_logging(str(tmp_path))
    logger.log_start("glm-5.2", "http://x")
    logger.log_user("你好，任务")
    logger.log_error("boom")

    content = (tmp_path / "agent.log").read_text(encoding="utf-8")
    assert "agent 启动" in content
    assert "你好，任务" in content
    assert "boom" in content


def test_setup_is_idempotent(tmp_path):
    logger.setup_logging(str(tmp_path))
    logger.setup_logging(str(tmp_path))
    assert len(logger._logger.handlers) == 1


def test_silent_before_setup():
    # 未 setup 前调用不崩溃也不落盘（被 import 时静默丢弃）
    logger.log_user("silent")
    assert not logger._logger.handlers
