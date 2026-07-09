#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Demo3 - MCP Server（Model Context Protocol）

把工具从「Agent 本地代码」搬到「独立的 HTTP 服务」，Agent 通过 JSON-RPC 2.0
协议远程发现并调用它们。这就是 MCP 的本质：**工具的能力边界从「同一进程的
函数调用」扩展到「跨进程 / 跨机器的 RPC 调用」**。

单文件按 4 个 Part 组织：
    Part 1: 工具定义（schema + 实现，与 demo1 的本地工具同构）
    Part 2: MCP 协议常量（method 名 / 协议版本）
    Part 3: JSON-RPC Handler（三大 method：initialize / tools/list / tools/call）
    Part 4: HTTP Server 入口（POST /mcp 接收请求）

启动：
    python mcp_server.py            # 默认监听 127.0.0.1:8888
    python mcp_server.py --port 9000
"""

import argparse
import json
import random
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================
# Part 1: 工具定义（schema + 实现）
# ============================================================
# 每个工具仍然是「schema（说明书）+ fn（实现）」两件套，和 demo1 的本地工具
# 完全同构。唯一的区别是：它们不再被 Agent 进程直接 import，而是被一个独立
# 的 HTTP Server 持有，通过 JSON-RPC 暴露出去。

TOOLS = [
    {
        "name": "add",
        "description": "计算两个数字的和",
        "input_schema": {                      # 与 agent.py 本地工具统一用 input_schema，省去格式转换
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个加数"},
                "b": {"type": "number", "description": "第二个加数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "multiply",
        "description": "计算两个数字的乘积",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个乘数"},
                "b": {"type": "number", "description": "第二个乘数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "weather",
        "description": "查询指定城市的天气（演示用，返回写死/随机数据）",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名，如 北京 / 上海"},
            },
            "required": ["city"],
        },
    },
]


def fn_add(a, b):
    """加法实现"""
    return a + b


def fn_multiply(a, b):
    """乘法实现"""
    return a * b


# 天气演示数据：从预设表里取，找不到就随机一个
_WEATHER_DB = {
    "北京": ("晴", 25),
    "上海": ("多云", 22),
    "广州": ("雷阵雨", 30),
    "深圳": ("多云", 29),
    "杭州": ("晴", 24),
}


def fn_weather(city):
    """天气查询实现（演示用，非真实接口）"""
    if city in _WEATHER_DB:
        condition, temp = _WEATHER_DB[city]
    else:
        condition = random.choice(["晴", "多云", "阴", "小雨", "大雨", "雷阵雨"])
        temp = random.randint(10, 35)
    return f"{city} 今天天气：{condition}，气温 {temp}°C"


# 路由表：工具名 → 实现函数（与 Agent 端的 AVAILABLE_FUNCTIONS 同构）
TOOL_FUNCTIONS = {
    "add":      fn_add,
    "multiply": fn_multiply,
    "weather":  fn_weather,
}


# ============================================================
# Part 2: MCP 协议常量
# ============================================================
# MCP 协议规定了三个核心 method（JSON-RPC 2.0 的 method 字段）：
#   1. initialize  — 握手：客户端询问服务端能力（capabilities）
#   2. tools/list  — 工具发现：拿到所有工具的 schema 列表
#   3. tools/call  — 工具调用：按名字 + 参数执行，返回结果

PROTOCOL_VERSION = "2024-11-05"   # MCP 协议版本号（写死一个常见值即可）

METHOD_INITIALIZE = "initialize"
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"


# ============================================================
# Part 3: JSON-RPC Handler（三大 method 分发）
# ============================================================
# JSON-RPC 2.0 的请求体形如：
#   {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {...}}
# 响应体形如：
#   {"jsonrpc": "2.0", "id": 1, "result": {...}}      ← 成功
#   {"jsonrpc": "2.0", "id": 1, "error": {...}}       ← 失败

def handle_request(payload: dict) -> dict:
    """
    MCP 协议入口：解析 JSON-RPC 请求，按 method 分发。

    Args:
        payload: 已解析的 JSON-RPC 2.0 请求 dict

    Returns:
        JSON-RPC 2.0 响应 dict（直接 json.dumps 后写回 HTTP body）
    """
    # JSON-RPC 2.0 规定：请求必须带 method 和 id
    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params", {}) or {}

    try:
        if method == METHOD_INITIALIZE:
            result = _handle_initialize(params)
        elif method == METHOD_TOOLS_LIST:
            result = _handle_tools_list(params)
        elif method == METHOD_TOOLS_CALL:
            result = _handle_tools_call(params)
        else:
            # 未知 method → JSON-RPC 标准错误码 -32601 (Method not found)
            return _error(req_id, -32601, f"未知方法: {method}")

        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    except Exception as e:
        # 任何异常 → 标准错误码 -32603 (Internal error)
        return _error(req_id, -32603, f"服务端内部错误: {e}")


def _handle_initialize(params: dict) -> dict:
    """
    initialize：握手 + 协议版本协商 + 能力声明。

    真实场景这里还会做鉴权（API Key / OAuth），演示版跳过。
    """
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {
            "tools": {},            # 声明本服务支持 tools/list 和 tools/call
        },
        "serverInfo": {
            "name": "demo3-mcp-server",
            "version": "1.0.0",
        },
    }


def _handle_tools_list(params: dict) -> dict:
    """
    tools/list：返回完整工具 schema 列表。

    schema 格式与 agent.py 本地工具一致（都用 input_schema），
    Agent 合并后可直接塞进 LLM 请求的 tools 参数。
    """
    return {"tools": TOOLS}


def _handle_tools_call(params: dict) -> dict:
    """
    tools/call：执行指定工具。

    params 形如：{"name": "add", "arguments": {"a": 1, "b": 2}}
    返回形如：{"content": [{"type": "text", "text": "3"}]}
    """
    name = params.get("name")
    arguments = params.get("arguments", {}) or {}

    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"未知工具: {name}")

    fn = TOOL_FUNCTIONS[name]
    # 把 arguments dict 展开成关键字参数 —— 这一步是「跨进程 RPC」与「本地
    # 函数调用」的衔接点
    value = fn(**arguments)

    # MCP 协议规定 tools/call 的返回值用 content 数组包装（便于多模态扩展）
    return {"content": [{"type": "text", "text": str(value)}]}


def _error(req_id, code: int, message: str) -> dict:
    """构造 JSON-RPC 标准错误响应"""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    }


# ============================================================
# Part 4: HTTP Server 入口
# ============================================================
# MCP 的「Transport 层」可以有很多种（stdio / SSE / HTTP）。Demo3 选最简单的：
# 一个本地 HTTP POST 端点 /mcp，所有 JSON-RPC 都走这一个端点。

class MCPHTTPHandler(BaseHTTPRequestHandler):
    """接收 POST /mcp，把 body 解析成 JSON-RPC 请求交给 handle_request。"""

    # 关闭默认的 access log（教学演示时刷屏太吵）
    def log_message(self, format, *args):
        pass

    def do_POST(self):
        # 读 body
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            response = _error(None, -32700, f"JSON 解析失败: {e}")
        else:
            # 实际分发
            response = handle_request(payload)
            # 让观察更友好（教学用）
            method = payload.get("method", "?")
            print(f"[MCP] <- {method}  params={payload.get('params')}", flush=True)
            print(f"[MCP] -> {response.get('result') or response.get('error')}", flush=True)

        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    parser = argparse.ArgumentParser(description="Demo3 MCP Server")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=8888, help="监听端口")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), MCPHTTPHandler)

    print("=" * 60)
    print("Demo3 MCP Server 已启动")
    print(f"监听:   http://{args.host}:{args.port}/mcp")
    print(f"协议:   JSON-RPC 2.0 over HTTP")
    print(f"工具:   {', '.join(t['name'] for t in TOOLS)}")
    print("=" * 60)
    print("等待 Agent 调用...\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MCP] 收到 Ctrl+C，关闭服务")
        server.shutdown()


if __name__ == "__main__":
    main()
