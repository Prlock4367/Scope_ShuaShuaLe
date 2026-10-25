# -*- coding: utf-8 -*-
"""scope_mcp —— 示波器 MCP server。

**MCP 里零仪器逻辑**，全部下沉 `scope/` 采集库。

    from scope_mcp.server import build_server
    mcp = build_server()
    mcp.run()
"""
from . import conn, format, guard, state

__all__ = ["build_server", "conn", "format", "guard", "state"]

_VERSION = "0.1.0"


def build_server(*args, **kwargs):
    """延迟导入 —— 避免 import 本包时就拉起 mcp SDK。"""
    from .server import build_server as _b
    return _b(*args, **kwargs)


def main():
    from .server import main as _m
    return _m()
