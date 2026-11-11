#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Demo7 纯函数测试 — 条件边路由 + 审批门判定 + .env 解析（不碰 LLM/网络）"""

from pathlib import Path

from conftest import import_demo_module
from langchain_core.messages import AIMessage, ToolMessage

agent = import_demo_module("demo7-langgraph", "agent")


def _ai_msg(*calls):
    """构造带 tool_calls 的 AIMessage"""
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}
        for i, (name, args) in enumerate(calls)
    ])


# ---------- 循环轴：判停条件边 ----------

def test_route_after_llm_with_tool_calls():
    state = {"messages": [_ai_msg(("execute_bash", {"command": "ls"}))]}
    assert agent.route_after_llm(state) == "gate"


def test_route_after_llm_without_tool_calls():
    state = {"messages": [AIMessage(content="任务完成")]}
    assert agent.route_after_llm(state) == "__end__"


# ---------- 约束轴：审批门 ----------

def test_needs_approval_filters_dangerous():
    calls = [
        {"name": "read_file", "args": {"path": "a.py"}, "id": "call_0", "type": "tool_call"},
        {"name": "execute_bash", "args": {"command": "ls"}, "id": "call_1", "type": "tool_call"},
    ]
    assert [c["id"] for c in agent.needs_approval(calls)] == ["call_1"]


def test_route_after_gate():
    assert agent.route_after_gate({"gate_decision": "allow"}) == "tools"
    assert agent.route_after_gate({"gate_decision": "deny"}) == "llm"


def test_apply_gate_decision_allow():
    msg = _ai_msg(("execute_bash", {"command": "ls"}))
    result = agent.apply_gate_decision(msg, "allow")
    assert result == {"gate_decision": "allow"}
    assert "messages" not in result


def test_apply_gate_decision_deny_pairs_every_call():
    """deny 时每个 tool_use 都必须配上拒绝 ToolMessage（协议配对要求）"""
    msg = _ai_msg(("read_file", {"path": "a.py"}), ("execute_bash", {"command": "ls"}))
    result = agent.apply_gate_decision(msg, "deny")
    assert result["gate_decision"] == "deny"
    denials = result["messages"]
    assert len(denials) == 2
    assert all(isinstance(d, ToolMessage) for d in denials)
    # tool_call_id 一一对应（call_0 read_file 也被拒——教学版整批拒绝，见 README）
    assert [d.tool_call_id for d in denials] == ["call_0", "call_1"]
    assert "用户拒绝" in denials[0].content


# ---------- .env 解析 ----------

def test_load_env_file_parses(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# 注释\nKEY_A=value_a\n\nKEY_B = 带空格 \nBAD_LINE_NO_EQ\n", encoding="utf-8")
    values = agent.load_env_file(f)
    assert values == {"KEY_A": "value_a", "KEY_B": "带空格"}


def test_load_env_file_missing(tmp_path):
    assert agent.load_env_file(tmp_path / "nope.env") == {}
