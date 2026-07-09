#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo5 - 反馈回环演示：执行者 ↔ 质检员

绕开 Team.run_team（单向 DAG，无重试），手搓编排：
    A1 盲写一版广告语
       ↓
    Q1 按 4 条标准验收
       ↓ pass?  → ✅ 结束
       ↓ fail   → 把反馈 send 回 A1，A1 改写
       ↓
    循环直到 pass 或达到 MAX_RETRY

关键：靠的是 Agent 的 inbox 持久记忆 + 多次 chat() 累积 messages，
这是 demo4 的"临时工"模式做不到的——临时工没有跨轮记忆。
"""

import sys
import os
import re
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 强制 UTF-8 输出，避免 Windows GBK 终端 surrogate 报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from agent import init_client, LOCAL_TOOLS, LOCAL_FUNCTIONS, Team


TASK      = "为产品「智谱清言」写一句广告语"
CRITERIA  = [
    "必须完整包含产品名「智谱清言」（不能简写为「清言」或「智谱」）",
    "字数不超过 15 个汉字",
    "句末不能用感叹号「！」或「!」",
    "必须包含一个明显的动词",
]
MAX_RETRY = 3


def parse_verdict(text: str):
    """从质检员的自然语言回复里抠出 JSON 验收结论"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None, text
    try:
        v = json.loads(m.group(0))
        return v, text
    except Exception:
        return None, text


def main():
    init_client()

    print("=" * 60)
    print("反馈回环演示：执行者 A1 ↔ 质检员 Q1")
    print("=" * 60)

    team = Team(tools=LOCAL_TOOLS, local_fns=LOCAL_FUNCTIONS, verbose=True)
    a1 = team.recruit("A1", "广告文案写手")
    q1 = team.recruit("Q1", "广告语质检员，严格对照标准验收，不达标必须打回")

    # 第一版：故意不给 A1 全部标准，制造一个"很可能被打回"的初稿
    result = a1.chat(TASK + "\n（写一句简短有冲击力的广告语）")
    print(f"\n{'='*60}\n[A1 初稿] {result}\n{'='*60}\n")

    for attempt in range(1, MAX_RETRY + 1):
        # 质检员：消化 A1 的初稿 + 拿到验收标准
        team.send("A1", "Q1", f"待验收广告语：{result}")
        verdict_text = q1.chat(
            "请严格按以下 4 条标准验收上条消息里的广告语。\n"
            "标准：\n- " + "\n- ".join(CRITERIA) + "\n\n"
            "严格只输出 JSON：{\"pass\": true|false, \"feedback\": \"若不通过，列出具体哪条没过、怎么改\"}"
        )

        v, raw = parse_verdict(verdict_text)
        print(f"\n{'='*60}\n[第 {attempt} 轮质检原始回复]\n{raw}\n{'='*60}\n")

        if v and v.get("pass") is True:
            print(f"✅ 验收通过（第 {attempt} 轮）\n最终文案：{result}")
            team.dismiss()
            return

        feedback = (v.get("feedback") if v else None) or raw
        print(f"❌ 第 {attempt} 轮未通过。质检反馈：{feedback}")

        if attempt == MAX_RETRY:
            print(f"⚠️ 达到最大重试 {MAX_RETRY} 次，停止。最后一版文案：{result}")
            team.dismiss()
            return

        # 把反馈送回 A1 —— 关键：A1 的 inbox 累积，messages 也会带上这次反馈
        team.send("Q1", "A1", f"质检未通过。具体反馈：{feedback}")
        result = a1.chat("请按质检反馈重写一版广告语。")
        print(f"\n[A1 第 {attempt+1} 版] {result}\n")


if __name__ == "__main__":
    main()
