#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Stats 层 — 会话级 API 用量与成本统计（/stats 面板的数据源）

教学点：每次 messages.create 都花钱。record_usage 在每个调用点埋一行，
STATS 单例累计整个会话；成本按 glm-5.2 官方价目估算（常量可改）。
所有 demo 的本文件为同内容副本（demo2 是 canonical）。
"""


# glm-5.2 官方价目（元 / 百万 tokens，来源：智谱 BigModel 开放平台，2026-10 查得）。
# 按你的网关实际价目改这几个常量即可。
PRICE_INPUT       = 8    # 输入（未命中缓存的部分）
PRICE_OUTPUT      = 28   # 输出
PRICE_CACHE_WRITE = 10   # 缓存写入
PRICE_CACHE_READ  = 2    # 缓存读取（命中）


class Stats:
    """会话级累计统计（模块级单例 STATS）"""

    def __init__(self) -> None:
        self.api_calls     = 0
        self.input_tokens  = 0
        self.output_tokens = 0
        self.cache_create  = 0   # 缓存写入 tokens（cache_creation_input_tokens）
        self.cache_read    = 0   # 缓存命中读取 tokens（cache_read_input_tokens）
        self.compactions   = 0

    def record_usage(self, usage) -> None:
        """每个 messages.create 调用点埋一行：把该次响应的 usage 计入累计。

        兼容网关不返回 usage / 不返回缓存字段的情况（getattr 取 0）。
        """
        if usage is None:
            return
        self.api_calls += 1
        self.input_tokens  += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_create  += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read    += getattr(usage, "cache_read_input_tokens", 0) or 0

    def record_compaction(self) -> None:
        self.compactions += 1

    def cost_yuan(self) -> float:
        """估算成本（元）。缓存命中的输入按缓存读价计，未命中的按输入价计。"""
        uncached_input = self.input_tokens - self.cache_create - self.cache_read
        return (uncached_input * PRICE_INPUT
                + self.cache_create * PRICE_CACHE_WRITE
                + self.cache_read * PRICE_CACHE_READ
                + self.output_tokens * PRICE_OUTPUT) / 1_000_000

    def format_lines(self) -> list:
        """/stats 面板的展示行"""
        return [
            f"API 调用: {self.api_calls} 次",
            f"输入:     {self.input_tokens:,} tokens（缓存写 {self.cache_create:,} / 读 {self.cache_read:,}）",
            f"输出:     {self.output_tokens:,} tokens",
            f"压缩:     {self.compactions} 次",
            f"估算成本: ¥{self.cost_yuan():.4f}（按 glm-5.2 价目，见 stats.py 常量）",
        ]


STATS = Stats()
