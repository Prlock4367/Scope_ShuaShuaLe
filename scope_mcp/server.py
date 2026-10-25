# -*- coding: utf-8 -*-
"""scope_mcp —— 示波器 MCP server（薄壳）。

**MCP 里零仪器逻辑**：全部下沉 `scope/`。本包只有 6 个文件：
    server.py     入口 + 工具注册（本文件）
    conn.py       连接适配（惰性连接 / 复用 / 错误翻译）
    guard.py      权限层（第二层闸门 / 单会话串行锁）
    format.py     统一返回结构（{value, unit, raw, valid}）
    state.py      状态项与测量项定义（A 组共用一份）
    tools/        工具实现，按组切分

用法
----
    # 只读模式（默认，安全）
    python -m scope_mcp.server

    # 开启写权限（会改仪器设置，先看清改哪几项）
    SCOPE_MCP_ALLOW_CONTROL=1 python -m scope_mcp.server

调试
----
    python -m scope_mcp.server --selftest      # 离线自检：注册表 + 返回结构
    npx @modelcontextprotocol/inspector <python> -m scope_mcp.server

挂到 MCP 客户端（示例）
----------------------
    {
      "mcpServers": {
        "scope": {
          "command": "<绝对路径>/python.exe",
          "args": ["-m", "scope_mcp.server"],
          "cwd": "<项目根绝对路径>"
        }
      }
    }
"""
import argparse
import sys


def build_server():
    """构造 MCPServer 实例并注册工具组。

    **所有工具在这里一次性注册** —— server.py 是唯一知道"有哪几组工具"的地方。
    加一组工具 = 加一行 `xxx.register(mcp)`。
    """
    from mcp.server.mcpserver import MCPServer

    from . import guard
    from .tools import capture, cursor, math_tools, measure_ext, status, trigger_ext

    mcp = MCPServer(
        name="scope-mcp",
        version="0.3.0",
        instructions=(
            "RIGOL DS2102E（DS2000E 系列）示波器的采集与判读。\n"
            "- 先用只读工具：scope_status 看连通性，scope_state 看配置，"
            "scope_measure 看读数，scope_capture 抓波形。\n"
            "- 波形以**摘要 + 文件路径**返回，不回原始采样点；需要细节就把 "
            "capture_id 传给 scope_analyze（可做频谱/包络/量化体检/形态判读）。\n"
            "- scope_capture 与 scope_analyze 会返回 tolerance 字段，"
            "**先看它判断读数可不可信，再下结论**。\n"
            "- 需要量两点之间差多少（上升沿 10%→90%、建立时间），用游标："
            "scope_cursor_set 定位，scope_cursor_read 读 Δt/ΔV。\n"
            "- 触发抓不稳：先用 scope_trigger_status 看清现状，"
            "再 scope_trigger_config 调 sweep/holdoff/nreject 三招之一。\n"
            "- 本 server 默认**只读**。写操作需显式开启，被拒时返回值会说明原因。\n"
            "- 仪器是独占资源，工具**必须串行调用**，不要并发。\n"
            "- 仪器状态一律以实时查询为准，不要用历史截图或记录推断当前状态。"
        ),
    )

    status.register(mcp)          # A 组 · 只读发现
    capture.register(mcp)         # B 组 · 只读取数（含判读入口 scope_analyze）
    cursor.register_read(mcp)     # C 组 · 游标读（常驻）
    measure_ext.register_read(mcp)  # F 组 · 统计/延迟（只读）
    math_tools.register_read(mcp) # G 组 · 数学运算/采集参数（只读部分）
    trigger_ext.register_read(mcp)  # H 组 · 触发现状（只读）

    if guard.write_enabled():
        from .tools import acquire, configure
        configure.register(mcp)          # D 组 · 控制
        cursor.register_write(mcp)       # C 组 · 游标写
        acquire.register_write(mcp)      # E 组 · 采集控制（run/stop/single/autoset）
        measure_ext.register_write(mcp)  # F 组 · 频率计（设源是写操作）
        math_tools.register_write(mcp)   # G 组 · 数学运算/参考波形/采集参数（写）
        trigger_ext.register_write(mcp)  # H 组 · 触发配置（写）

    return mcp


def main(argv=None):
    ap = argparse.ArgumentParser(description="示波器 MCP server")
    ap.add_argument("--selftest", action="store_true",
                    help="离线自检：打印已注册工具与返回结构，不连仪器")
    args = ap.parse_args(argv)

    from . import conn
    conn.apply_log_policy()          # 默认封死库内 print，保护 JSON-RPC 通道

    mcp = build_server()

    if args.selftest:
        return _selftest(mcp)

    mcp.run()          # 默认 transport="stdio"


def _selftest(mcp):
    """离线自检 —— 不连仪器，只验证 server 形状。"""
    from . import guard
    tools = mcp._tool_manager.list_tools()
    print("=" * 62)
    print("  scope_mcp 自检")
    print("=" * 62)
    print("  server : %s v%s" % (mcp.name, mcp.version))
    print("  写权限 : %s" % ("开" if guard.write_enabled() else "关（只读模式）"))
    print("  工具数 : %d" % len(tools))
    print()
    for t in tools:
        ann = t.annotations
        ro = getattr(ann, "read_only_hint", None) if ann else None
        print("  %-18s %-6s %s"
              % (t.name, "只读" if ro else "读写",
                 (t.description or "").split("\n")[0][:44]))
    print()
    print("  返回结构自检:")
    from . import format as fmt
    print("    value():", fmt.value("vpp", 3.31, "V", "峰峰值"))
    print("    value() 无效:", fmt.value("freq", 9.9e37, "Hz", "频率"))
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
