#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Mini Agent - 极简 AI Agent
"""

import sys
from src.llm_client import LLMClient
from src.agent import Agent


def main():
    """主函数"""
    # 初始化 LLM 客户端
    try:
        llm_client = LLMClient()
    except ValueError as e:
        print(f"错误: {e}")
        print("请设置环境变量: export ANTHROPIC_API_KEY=your_api_key")
        sys.exit(1)

    # 初始化 Agent
    agent = Agent(llm_client)

    # 检查是否有命令行参数
    if len(sys.argv) > 1:
        # 单次执行模式
        user_input = " ".join(sys.argv[1:])
        print(f"用户: {user_input}\n")
        response = agent.run(user_input)
        print(f"\n助手: {response}")
    else:
        # 交互模式
        print("Mini Agent 已启动，输入 'quit' 或 'exit' 退出\n")

        while True:
            try:
                user_input = input("用户: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("再见！")
                    break

                if user_input.lower() == 'clear':
                    agent.clear()
                    print("对话历史已清空\n")
                    continue

                response = agent.run(user_input)
                print(f"\n助手: {response}\n")

            except KeyboardInterrupt:
                print("\n\n再见！")
                break
            except Exception as e:
                print(f"\n错误: {e}\n")


if __name__ == "__main__":
    main()
