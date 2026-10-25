# -*- coding: utf-8 -*-
"""试机脚本 —— 像 MCP 客户端那样调用工具，看真实返回。

用法:
    python ./try.py status         # 连通性
    python ./try.py state          # 完整状态
    python ./try.py measure        # 读数
    python ./try.py capture        # 抓波形
    python ./try.py analyze <id>   # 分析
    python ./try.py raw <工具名> {json}   # 直接调，自定参数

设计意图：不挂 MCP 客户端也能试。返回的就是客户端会收到的东西。
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(HERE))


def show(obj, title=""):
    if title:
        print("\n── %s %s" % (title, "─" * max(0, 56 - len(title))))
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


async def call(tool, **kwargs):
    """走 SDK 的 `MCPServer.call_tool(name, arguments)` —— 与 MCP 客户端同一条路径。

    不直接调 fn：v2 把同步 handler 放到 worker 线程跑（anyio.to_thread），
    直接调会拿到未 await 的包装对象。走 call_tool 才测得到真实行为。
    """
    from scope_mcp.server import build_server
    mcp = build_server()
    res = await mcp.call_tool(tool, kwargs)
    return _unpack(res)


def _unpack(res):
    """CallToolResult → 客户端的 content 里实际会拿到的对象。"""
    if getattr(res, "structured_content", None) is not None:
        return res.structured_content
    if res.content:
        text = getattr(res.content[0], "text", None)
        if text is not None:
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return text
    return {"is_error": getattr(res, "is_error", False), "raw": str(res)}


def main():
    argv = sys.argv[1:] or ["status"]
    what = argv[0]

    if what == "status":
        show(asyncio.run(call("scope_status")), "scope_status")
    elif what == "state":
        show(asyncio.run(call("scope_state")), "scope_state")
    elif what == "measure":
        items = argv[1].split(",") if len(argv) > 1 else None
        src = argv[2] if len(argv) > 2 else "CHANnel1"
        show(asyncio.run(call("scope_measure", source=src, items=items)), "scope_measure")
    elif what == "capture":
        src = argv[1] if len(argv) > 1 else "CHANnel1"
        show(asyncio.run(call("scope_capture", source=src)), "scope_capture")
    elif what == "analyze":
        if len(argv) < 2:
            print("用法: try.py analyze <capture_id> [kind]"); return 1
        kind = argv[2] if len(argv) > 2 else "all"
        show(asyncio.run(call("scope_analyze", capture_id=argv[1], kind=kind)),
             "scope_analyze")
    elif what == "cursor":
        # try.py cursor              读游标
        # try.py cursor read         读游标
        # try.py cursor set MANual CHANnel1 1e-4 5e-4   写游标（需 SCOPE_MCP_ALLOW_CONTROL=1）
        sub = argv[1] if len(argv) > 1 else "read"
        if sub == "read":
            show(asyncio.run(call("scope_cursor_read")), "scope_cursor_read")
        elif sub == "set":
            kw = {}
            if len(argv) > 2: kw["mode"] = argv[2]
            if len(argv) > 3: kw["source"] = argv[3]
            if len(argv) > 4: kw["ax"] = int(argv[4])
            if len(argv) > 5: kw["bx"] = int(argv[5])
            if len(argv) > 6: kw["tunit"] = argv[6]
            show(asyncio.run(call("scope_cursor_set", **kw)), "scope_cursor_set")
        else:
            print("用法: try.py cursor [read | set <mode> <chan> <ax> <bx>]"); return 1
    elif what == "list":
        from scope_mcp.server import build_server
        mcp = build_server()
        for t in mcp._tool_manager.list_tools():
            print("%-18s %s" % (t.name, (t.description or "").split("\n")[0]))
    else:
        # raw <tool> <json-args>
        args = json.loads(argv[2]) if len(argv) > 2 else {}
        show(asyncio.run(call(what, **args)), what)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
