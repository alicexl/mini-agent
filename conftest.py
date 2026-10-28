#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
测试基建 — 按路径加载 demo 目录下的非包模块。

各 demo 是同内容副本、且模块同名（memory.py / stats.py …），测试只以
demo2-memory 为 canonical 导入，避免同名模块在 sys.modules 里互相覆盖。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def import_demo_module(demo: str, name: str):
    """
    加载 <demo>/<name>.py 并返回模块对象。

    用 "demo.name" 做唯一注册名（demo 目录名带连字符，与包名规则无关，
    不冲突即可）；同时把 demo 目录临时插到 sys.path 前面，让模块内部的
    `import stats` 这类兄弟导入解析到同一个 demo 的副本。
    """
    demo_dir = ROOT / demo
    sys.path.insert(0, str(demo_dir))
    try:
        spec = importlib.util.spec_from_file_location(f"{demo}.{name}", demo_dir / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)
