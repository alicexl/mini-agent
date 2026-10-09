#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4 规划层 — plan 工具（规划轴三件之一）

    plan      当前任务的规划——LLM 自判复杂度，复杂任务开头列一次步骤，
              Agent 打印清单（一次性：调用后从 tools 移除，LLM 列完就放手）

规划轴三件分三层：plan（本文件，当前任务的规划）/ Skill（skill.py，历史经验的复用）
/ ask_user_question（ask.py，歧义时主动问用户）。
"""


# ============================================================
# plan 工具：定义 + 实现
# ============================================================

PLAN_TOOL = {
    "name": "plan",
    "description": (
        "任务规划——仅在复杂的多步任务开头调用一次，列出步骤。"
        "**使用时机**：3 步以上、多工具协作、步骤间有依赖的任务。"
        "简单的一两步任务直接 execute_bash / read_file，不要用 plan。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "steps": {
                "type": "array",
                "description": "任务步骤列表（动词开头，按执行顺序排列）",
                "items": {"type": "string"},
            },
        },
        "required": ["steps"],
    },
}

_PLAN: list = []  # 模块级计划列表——plan 工具设置，Agent 终端打印


def plan(steps: list) -> str:
    """
    任务规划——仅在复杂多步任务开头调用一次。
    LLM 列步骤，Agent 打印清单。没有状态机、没有进度追踪——教学最简形态。
    """
    global _PLAN
    if not isinstance(steps, list):
        return "[错误] steps 必须是数组"
    _PLAN = [str(s) for s in steps]
    print("\n" + "─" * 50)
    for i, s in enumerate(_PLAN, 1):
        print(f"  {i}. {s}")
    print("─" * 50)
    return f"已规划 {len(_PLAN)} 步"


# 规划层对外导出：工具 schema + 路由表（agent.py 与本地四件套合并）
PLAN_TOOLS = [PLAN_TOOL]
PLAN_FUNCTIONS = {"plan": plan}
