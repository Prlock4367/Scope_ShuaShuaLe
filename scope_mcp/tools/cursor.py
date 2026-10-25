# -*- coding: utf-8 -*-
"""C 组工具：游标（2 个，1 只读 + 1 写）。

    scope_cursor_read    读当前游标模式与全部读数（只读）
    scope_cursor_set     设置游标模式与 A/B 位置（写，需开启控制权限）

为什么要游标：手动游标能回答自动测量答不了的问题 ——
"这两点之间差多少"、"上升沿 10%→90% 在哪"，是做时序/建立时间必备的工具。
命令已核 DS2000E 手册（`:CURSor:MANual:*` / `:CURSor:TRACk:*`），
闸门白名单里也已登记。

型号无关性：只发标准 `:CURSor` 命令，不假设具体型号。
"""
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import conn, format as fmt, state as st
from ..guard import requires_write, serialized

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True,
)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True,
)

# 手册里的合法取值（DS2000E 编程手册 :CURSor 节）
MODES = ("OFF", "MANual", "TRACk", "AUTO", "X", "Y")
MAN_TYPES = ("TIME", "AMPLitude")
TUNITS = ("SECond", "HZ", "PERCent", "DEGRee")


def _tunit_cmd(u):
    """归一水平单位名 → 手册写法（:CURSor:MANual:TUNit 节）。"""
    s = (u or "").strip().upper()
    for cand in TUNITS:
        c = cand.upper()
        if s == c or (len(s) >= 3 and c.startswith(s[:3])):
            return cand
    return None


def _mode_cmd(mode):
    """归一模式名 → 手册写法。"""
    m = (mode or "").strip()
    if not m:
        return None
    low = m.lower()
    for cand in ("OFF", "MANual", "TRACk", "AUTO", "X", "Y"):
        if cand.lower() == low or cand.lower().startswith(low[:3]):
            return cand
    return None


def register_read(mcp):
    """注册只读游标工具（scope_cursor_read）—— 常驻，任何模式都有。"""
    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_cursor_read() -> dict:
        """读取当前游标模式与全部游标读数。

        返回：
        - mode        当前模式（OFF / MANual / TRACk / AUTO…）
        - AX / BX     A、B 游标的水平位置
        - XDELta      两游标水平差（Δt）
        - IXDelta     1/Δt —— 两游标间对应的频率
        - AY / BY     （手动模式）两游标处的电压
        - YDELta      两游标电压差（ΔV）

        游标是**只读查询**为主：本工具不改任何设置。
        要移动游标用 scope_cursor_set（需开启控制权限）。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        try:
            mode = conn.q(inst, ":CURSor:MODE?")
            out = fmt.ok(ts=fmt.stamp(), mode=mode)
            # 手动与跟踪两套游标的读数命令不同，按当前模式择一查询。
            # 关掉/自动模式就读不到，那时给提示而不是报错。
            is_manual = mode and "MAN" in mode.upper()
            is_track = mode and "TRAC" in mode.upper()
            if is_manual or is_track:
                pre = ":CURSor:MANual:" if is_manual else ":CURSor:TRACk:"
                # 读数一律用 *Value? 系列 —— 它们回**真实值**（秒/Hz/%/°，
                # 由 :CURSor:*:TUNit 决定），而 CAX?/CBX? 回的是"位置"，两者
                # 别混（手册 :CURSor:MANual:AXValue 节明确说明了这个分工）。
                fields = {
                    "AX": pre + "AXValue?", "BX": pre + "BXValue?",
                    "XDELta": pre + "XDELta?", "IXDelta": pre + "IXDelta?",
                }
                if is_manual:
                    fields.update({"AY": pre + "AYValue?", "BY": pre + "BYValue?",
                                   "YDELta": pre + "YDELta?"})
                # 单位一并读回，否则拿到的数不知道是秒还是 Hz
                try:
                    out["tunit"] = conn.q(inst, ":CURSor:MANual:TUNit?")
                except Exception:
                    pass
                for key, cmd in fields.items():
                    try:
                        out[key] = fmt.value(key, conn.q(inst, cmd))
                    except Exception as exc:
                        out[key] = fmt.fail("%s 查询失败：%s" % (key, exc),
                                            "该读数在手动模式下才有效。")
                out["unit_hint"] = ("AX/BX/XDELta 的单位由 tunit 决定："
                                    "SEC=s，HZ=Hz，PERC=%，DEGR=°。")
            else:
                out["note"] = ("当前模式 %s 下无游标读数。"
                               "请先用 scope_cursor_set 切到 MANual。" % (mode or "?"))
            return out
        except Exception as exc:
            return fmt.fail("读游标失败（%s: %s）" % (type(exc).__name__, exc),
                            "游标是只读查询；若链路卡住请先跑 scope_status 看连通性。")

    return mcp


def register_write(mcp):
    """注册写游标工具（scope_cursor_set）—— **仅开启控制权限时注册**。

    与 configure 一致：只读模式下不对外暴露写工具，模型连看都看不到，
    比"看得到但一调就被拒"更干净。
    """
    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_cursor_set(
        mode: Annotated[str, Field(
            description="游标模式：OFF（关）/ MANual（手动定位置）/ TRACk（跟随波形）",
        )] = "",
        source: Annotated[str, Field(
            description="游标作用的通道：CHANnel1 / CHANnel2（也接受 CH1 / 1）",
        )] = "",
        ax: Annotated[float | None, Field(
            description="A 游标位置。**单位由 tunit 决定**，默认 SECond（秒）。"
                        "⚠️ 该字段是整数屏幕位置 0–699，小数会被仪器截断。",
        )] = None,
        bx: Annotated[float | None, Field(
            description="B 游标位置，同 ax。",
        )] = None,
        tunit: Annotated[str, Field(
            description="水平单位：SECond（秒）/ HZ（频率）/ PERCent（百分比）/ DEGRee（相位）。"
                        "决定 ax/bx 与读回值的含义。",
        )] = "",
    ) -> dict:
        """设置游标模式 / 作用通道 / A、B 游标位置。

        ⚠️ 这是**写操作**：会改动仪器设置。每条下发前先读原值，返回里带出来。

        ⚠️ **关于 ax/bx 单位**：
            手册 `:CURSor:MANual:CAX` 节写明参数是
            `Integer 0 to 699，默认 150` —— 是**屏幕位置整数**，
            **不是秒**。它的"物理含义"由 `:CURSor:MANual:TUNit` 决定。
            把秒（2e-06）直接传进来会被仪器按整数 0 处理，
            读回的 Δt 是 8e-08 这种荒谬值。
            要按秒精确摆放，请先用 scope_capture 量出目标点对应的
            屏幕位置（0–699），再传整数。

        - 只传 mode 就只切模式；只传 ax/bx 就只移游标。
        - 设完可直接调 scope_cursor_read 读真实 Δt / ΔV。

        本工具需要 server 开启控制权限（SCOPE_MCP_ALLOW_CONTROL=1）。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        # 位置必须是整数屏幕位置；传小数会被仪器截断成 0 —— 显式拦截并告知
        for nm, val in (("ax", ax), ("bx", bx)):
            if val is not None and float(val) != int(float(val)):
                return fmt.fail(
                    "游标位置 %s=%r 必须是整数（屏幕位置 0–699）" % (nm, val),
                    "手册 :CURSor:MANual:CAX 的参数是 Integer 0–699，不是秒。"
                    "先 scope_capture 找到目标点在屏上的位置（0–699），再传这个整数。")

        # ── 逐条：先读原值 → 过闸门 → 下发（与 configure.py 同一套流程）──
        from scope import link as _link
        from scope import scpi as _scpi

        todo = []          # (cmd, value, 展示标签)

        if tunit:
            tu = _tunit_cmd(tunit)
            if tu is None:
                return fmt.fail("水平单位无法识别：%r" % tunit,
                                "只能是 SECond / HZ / PERCent / DEGRee 之一。")
            todo.append((":CURSor:MANual:TUNit", tu, "tunit"))

        if mode:
            norm = _mode_cmd(mode)
            if norm is None:
                return fmt.fail("游标模式无法识别：%r" % mode,
                                "只能是 OFF / MANual / TRACk 之一（大小写不敏感）。")
            todo.append((":CURSor:MODE", norm, "mode"))

        if source:
            chan = st.norm_chan(source)
            if chan is None:
                return fmt.fail("通道名无法识别：%r" % source,
                                "只有两个通道：CHANnel1 / CHANnel2（也接受 CH1 / 1）。")
            cur_mode = (conn.q(inst, ":CURSor:MODE?") or "").upper()
            pre = ":CURSor:TRACk:SOURce" if "TRAC" in cur_mode else ":CURSor:MANual:SOURce"
            todo.append((pre, chan, "source"))

        cur_mode = (conn.q(inst, ":CURSor:MODE?") or "").upper()
        pre = ":CURSor:TRACk:" if "TRAC" in cur_mode else ":CURSor:MANual:"
        for name, val in (("CAX", ax), ("CBX", bx)):
            if val is not None:
                todo.append((pre + name, "%d" % int(float(val)), name))

        if not todo:
            return fmt.fail("没有给出任何要改的项。",
                            "至少要传 mode / source / ax / bx 之一。")

        applied, denied = [], []
        for cmd, val, label in todo:
            old = conn.q(inst, cmd + "?")
            try:
                _scpi.guard(cmd + " " + str(val))      # 闸门：不在白名单就抛
            except _scpi.Denied as exc:
                denied.append({"item": label, "cmd": cmd, "reason": str(exc)})
                continue
            try:
                _link.send(inst, "%s %s" % (cmd, val))
                applied.append({"item": label, "cmd": cmd,
                                "old": old, "new": val})
            except Exception as exc:
                denied.append({"item": label, "cmd": cmd,
                               "reason": "%s: %s" % (type(exc).__name__, exc)})

        out = fmt.ok(ts=fmt.stamp(), applied=applied,
                     note="写操作，已逐条记录原值。用 scope_cursor_read 读回结果。")
        if denied:
            out["denied"] = denied
        return out

    return mcp
