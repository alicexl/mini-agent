#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
demo2-memory/memory.py 压缩纯函数测试。

demo2 是 canonical 实现，其余 demo 的 memory.py 为同内容副本，
因此只测这一份。全部纯函数——不 import anthropic、不打 API。
"""

from types import SimpleNamespace

import tiktoken

from conftest import import_demo_module

memory = import_demo_module("demo2-memory", "memory")
_ENC = tiktoken.get_encoding("cl100k_base")


# ---------- _extract_text ----------

def test_extract_text_str_passthrough():
    assert memory._extract_text("hello") == "hello"


def test_extract_text_non_str_non_list():
    assert memory._extract_text(123) == "123"


def test_extract_text_dict_blocks():
    content = [
        {"type": "text", "text": "a"},
        {"type": "tool_use", "name": "read_file", "input": {"path": "x.txt"}},
        {"type": "tool_result", "content": "result"},
    ]
    text = memory._extract_text(content)
    assert "a" in text
    assert "[调用工具 read_file]" in text
    assert "result" in text


def test_extract_text_sdk_block_objects():
    content = [
        SimpleNamespace(type="text", text="t1"),
        SimpleNamespace(type="tool_use", name="bash", input={"command": "ls"}),
    ]
    text = memory._extract_text(content)
    assert "t1" in text
    assert "bash" in text


def test_extract_text_truncates_tool_result_to_200_chars():
    text = memory._extract_text([{"type": "tool_result", "content": "x" * 500}])
    assert text == "x" * 200


# ---------- _is_tool_result_message ----------

def test_is_tool_result_message_true():
    msg = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "x"}]}
    assert memory._is_tool_result_message(msg)


def test_is_tool_result_message_false_cases():
    assert not memory._is_tool_result_message({"role": "user", "content": "plain text"})
    assert not memory._is_tool_result_message({"role": "user", "content": [{"type": "text", "text": "t"}]})
    assert not memory._is_tool_result_message(
        {"role": "assistant", "content": [{"type": "tool_use", "name": "x", "input": {}}]}
    )


# ---------- _find_recent_start ----------

def test_find_recent_start_no_backoff(monkeypatch):
    monkeypatch.setattr(memory, "COMPACT_KEEP_RECENT", 2)
    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
    ]
    assert memory._find_recent_start(msgs) == 2


def test_find_recent_start_backoff_on_tool_result(monkeypatch):
    """切点落在 tool_result 上时向前回退——不能切断 tool_use ↔ tool_result 配对"""
    monkeypatch.setattr(memory, "COMPACT_KEEP_RECENT", 2)
    msgs = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": [{"type": "tool_use", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "r1"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "r2"}]},
        {"role": "assistant", "content": "a2"},
    ]
    assert memory._find_recent_start(msgs) == 1


# ---------- estimate_tokens ----------

def test_estimate_tokens_matches_tiktoken_for_simple_messages():
    msgs = [{"role": "user", "content": "hello world"}]
    expected = 4 + len(_ENC.encode("hello world"))
    assert memory.estimate_tokens(msgs) == expected


def test_estimate_tokens_monotonic():
    short = [{"role": "user", "content": "hi"}]
    long = [{"role": "user", "content": "很长的中文内容" * 20}]
    assert memory.estimate_tokens(long) > memory.estimate_tokens(short)


def test_estimate_tokens_cjk_not_free():
    # 中文不是 0 token——每字至少 1 token 的量级
    assert memory.estimate_tokens([{"role": "user", "content": "你好"}]) > 4
