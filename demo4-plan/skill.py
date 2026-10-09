#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo4 Skill 层 — 历史经验的复用（Skill 加载器 + use_skill 工具，规划轴三件之一）

    skills/*.md 可复用工作流模板（YAML frontmatter：name/description/triggers）
    元信息常驻 system prompt，正文由 LLM 按需经 use_skill 拉取
    （渐进式披露：不常驻 prompt，skill 再多也只增加少量元信息开销）

规划轴三件分三层：plan（plan.py，当前任务的规划）/ Skill（本文件，历史经验的复用）
/ ask_user_question（ask.py，歧义时主动问用户）。

调用方职责：main() 启动时调用 load_skills() 装载；
build_system_prompt 追加 build_skill_metadata_section() 的元信息段。
"""

import os
import re


# ============================================================
# Skill 机制：加载器 + use_skill 工具
# ============================================================

SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

# 模块级 skills 字典——main() 启动时 load_skills() 赋值，use_skill 运行时读取
_SKILLS: dict = {}

# YAML frontmatter 正则——非贪婪匹配首尾 ---
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple:
    """
    简易 YAML frontmatter 解析（只支持 name/description/triggers 三字段）。
    不引入 PyYAML 依赖——教学代码保持零额外依赖。
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    fm_text, body = match.group(1), match.group(2)
    meta = {}

    # 解析 name / description（单行字符串）
    for key in ("name", "description"):
        m = re.search(rf"^{key}:\s*(.+?)\s*$", fm_text, re.MULTILINE)
        if m:
            meta[key] = m.group(1).strip().strip('"').strip("'")

    # 解析 triggers（JSON 数组成 [a, b, c] 形式）
    m = re.search(r"^triggers:\s*\[(.*?)\]", fm_text, re.MULTILINE | re.DOTALL)
    if m:
        items = [
            t.strip().strip('"').strip("'").strip()
            for t in m.group(1).split(",")
            if t.strip()
        ]
        meta["triggers"] = items
    else:
        meta["triggers"] = []

    return meta, body.strip()


def load_skills() -> dict:
    """
    扫描 skills/*.md，解析 frontmatter，返回 {name: {description, triggers, body}}。
    目录不存在或空时返回空字典（Agent 仍可运行，只是没有 skill 可激活）。
    """
    skills = {}
    if not os.path.isdir(SKILLS_DIR):
        return skills

    for fname in sorted(os.listdir(SKILLS_DIR)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(SKILLS_DIR, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except Exception as e:
            print(f"[Skill] 读取 {fname} 失败: {e}")
            continue

        meta, body = _parse_frontmatter(raw)
        name = meta.get("name") or fname[:-3]  # 缺 name 用文件名
        skills[name] = {
            "description": meta.get("description", ""),
            "triggers":    meta.get("triggers", []),
            "body":        body,
            "file":        fname,
        }

    return skills


def build_skill_metadata_section() -> str:
    """
    生成 system prompt 里的「## 可用 Skills」段——只放元信息，不放 body。
    system prompt 告诉 LLM「有哪些技能」，LLM 自己决定要不要调 use_skill 拿正文。
    """
    if not _SKILLS:
        return ""
    lines = ["\n## 可用 Skills\n"]
    for name, info in _SKILLS.items():
        lines.append(f"- **{name}**: {info['description']}")
        if info["triggers"]:
            lines.append(f"  - 触发词: {', '.join(info['triggers'])}")
    lines.append("\n当用户任务匹配某个 skill 时，先调用 `use_skill` 获取该 skill 的详细工作流，然后严格按要求执行。")
    return "\n".join(lines) + "\n"


USE_SKILL_TOOL = {
    "name": "use_skill",
    "description": (
        "激活一个 skill 获取其详细工作流正文。"
        "当用户任务匹配 system prompt 中列出的某个可用 skill 时，先调用此工具获取该 skill 的步骤，"
        "然后严格按照返回的工作流执行。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "要激活的 skill 名称（见 system prompt 中的可用 Skills 列表）",
            }
        },
        "required": ["name"],
    },
}


def use_skill(name: str) -> str:
    """激活 skill 获取工作流正文。"""
    skill = _SKILLS.get(name)
    if not skill:
        return f"[错误] 未知 skill: {name}。可用: {', '.join(_SKILLS.keys()) or '(无)'}"
    print(f"\n[Skill] LLM 激活 skill: {name}")
    return skill["body"]


# Skill 层对外导出：工具 schema + 路由表（agent.py 与本地四件套合并）
SKILL_TOOLS = [USE_SKILL_TOOL]
SKILL_FUNCTIONS = {"use_skill": use_skill}
