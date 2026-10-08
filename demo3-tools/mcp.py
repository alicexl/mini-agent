#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo3 MCP 客户端层 — 工具扩展轴的核心（demo3 新增）

一个最小的 MCP client：所有调用都通过 JSON-RPC 2.0 over HTTP。

本 demo 只实现 MCP 的 tools 能力，涉及三个主要 method：
    - initialize : 握手 + 协议版本协商
    - tools/list : 拿到 server 端的完整工具 schema 列表
    - tools/call : 按名字 + arguments 调用具体工具，返回 content 包装的结果

MCP 的本质：**工具的能力边界从「同一进程的函数调用」
            扩展到「跨进程 / 跨机器的 RPC 调用」**。
工具不需要被 Agent 进程 import，可以是任何语言写的、跑在任何地方的独立服务。

启动顺序：
    1. 先启动 MCP Server：  python mcp_server.py
    2. 再启动 Agent：       python agent.py
"""

import requests

MCP_URL = "http://127.0.0.1:8888/mcp"   # MCP Server 地址（mcp_server.py 默认端口）


class MCPClient:
    def __init__(self, url: str):
        self.url = url
        self._id = 0
        self.initialized = False

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def send(self, method: str, params: dict = None) -> dict:
        """发送 JSON-RPC 2.0 请求并返回 result 字段。失败抛 RuntimeError。"""
        payload = {
            "jsonrpc": "2.0",
            "id":      self._next_id(),
            "method":  method,
            "params":  params or {},
        }
        try:
            resp = requests.post(self.url, json=payload, timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"MCP 网络错误 ({method}): {e}") from e

        if resp.status_code != 200:
            raise RuntimeError(f"MCP HTTP {resp.status_code} ({method}): {resp.text[:200]}")

        data = resp.json()
        if "error" in data:
            err = data["error"]
            raise RuntimeError(f"MCP 调用失败 ({method}): {err}")

        return data.get("result", {})

    def initialize(self) -> dict:
        """握手：协议版本协商 + 拿 server 能力声明。"""
        result = self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities":    {},
            "clientInfo":      {"name": "demo3-tools-agent", "version": "1.0.0"},
        })
        self.initialized = True
        return result

    def list_tools(self) -> list:
        """发现工具：返回 server 端完整工具 schema 列表。"""
        result = self.send("tools/list", {})
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用工具：按 name + arguments 执行，提取 content[].text 拼接后返回。"""
        result = self.send("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content", [])
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
        return "\n".join(texts) if texts else "[MCP 工具无文本返回]"


def discover() -> tuple:
    """
    启动时握手 + 发现工具，返回 (mcp_client, mcp_tools)。

    Server 未启动时降级为仅本地工具模式（返回空工具列表），Agent 仍可运行——
    远程工具箱是增强，不是依赖。
    """
    mcp_client = MCPClient(MCP_URL)
    try:
        info = mcp_client.initialize()
        print(f"[MCP] 握手成功：{info.get('serverInfo', {})} 协议版本 {info.get('protocolVersion')}")
        mcp_tools = mcp_client.list_tools()
        print(f"[MCP] 发现 {len(mcp_tools)} 个远程工具：{', '.join(t['name'] for t in mcp_tools)}")
        return mcp_client, mcp_tools
    except Exception as e:
        print(f"[MCP] 连接失败，降级为仅本地工具模式。原因: {e}")
        print(f"[MCP] 请确认已在另一个终端运行：python mcp_server.py")
        return mcp_client, []
