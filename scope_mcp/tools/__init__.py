# -*- coding: utf-8 -*-
"""MCP 工具实现，按组切分。

    status.py     A 组 · 只读发现（4 个）
    capture.py    B 组 · 只读取数（3 个）
    analyze.py    C 组 · 判读（3 个）
    configure.py  D 组 · 控制（2 个，需显式开启写权限）

每个模块暴露 `register(mcp)`，由 server.py 统一调用。
"""
