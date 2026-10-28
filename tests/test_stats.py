#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""demo2-memory/stats.py 纯函数测试（canonical 实现，其余 demo 为同内容副本）"""

from types import SimpleNamespace

from conftest import import_demo_module

stats = import_demo_module("demo2-memory", "stats")


def _usage(input_tokens=100, output_tokens=50, cache_create=0, cache_read=0):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_create,
        cache_read_input_tokens=cache_read,
    )


def test_record_usage_accumulates():
    s = stats.Stats()
    s.record_usage(_usage(100, 50))
    s.record_usage(_usage(200, 30))
    assert s.api_calls == 2
    assert s.input_tokens == 300
    assert s.output_tokens == 80


def test_record_usage_none_is_noop():
    s = stats.Stats()
    s.record_usage(None)
    assert s.api_calls == 0


def test_record_usage_missing_cache_fields_ok():
    # 兼容网关不返回缓存字段的情况
    s = stats.Stats()
    s.record_usage(SimpleNamespace(input_tokens=10, output_tokens=5))
    assert s.cache_create == 0 and s.cache_read == 0


def test_cost_yuan_splits_cache_pricing():
    s = stats.Stats()
    # 1000 输入（缓存写 100 / 读 200）+ 100 输出：未命中部分按输入价、其余按各自价
    s.record_usage(_usage(input_tokens=1000, output_tokens=100, cache_create=100, cache_read=200))
    expected = (700 * stats.PRICE_INPUT + 100 * stats.PRICE_CACHE_WRITE
                + 200 * stats.PRICE_CACHE_READ + 100 * stats.PRICE_OUTPUT) / 1_000_000
    assert s.cost_yuan() == expected


def test_record_compaction_counts():
    s = stats.Stats()
    s.record_compaction()
    s.record_compaction()
    assert s.compactions == 2


def test_format_lines_covers_all_fields():
    s = stats.Stats()
    s.record_usage(_usage(10, 5))
    s.record_compaction()
    lines = "\n".join(s.format_lines())
    for keyword in ("API 调用", "输入", "输出", "压缩:     1", "估算成本"):
        assert keyword in lines
