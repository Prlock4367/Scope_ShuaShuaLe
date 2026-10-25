# -*- coding: utf-8 -*-
"""D 组工具：控制（需显式开启写权限）。

    scope_configure   改通道 / 时基 / 触发（先读原值 → 过闸门 → 再下发）
    scope_autofit     自动布防（手算四个数，**不走 :AUToscale**）

本组**只有在 SCOPE_MCP_ALLOW_CONTROL=1 时才注册** —— 未开启时这些工具
在 tools/list 里**根本不存在**，模型看不见、也调不到。

这是刻意的：让"能不能改仪器"变成 server 的属性，而不是靠模型自觉。

每一条写命令都走同一条路：
    第 1 层闸门 scope/scpi.py  校验命令是否合法（逐字比对）
    第 2 层守卫 guard.requires_write  校验本次会话是否有资格
    第 3 层 link.apply() 语义  逐条**先实时读原值**再下发
"""
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import conn, format as fmt, state as st
from ..guard import requires_write, serialized

WRITE = ToolAnnotations(
    read_only_hint=False,
    destructive_hint=True,      # ← 会改仪器状态，客户端据此弹确认框
    idempotent_hint=False,
    open_world_hint=True,
)

# 允许改的项 —— 与 scope/scpi.py 的 B_LEVEL 白名单对应。
# 这里再列一遍是**给模型看的菜单**（它需要知道有哪些项可改），
# 不是第二道闸门 —— 真正的校验在 scpi.py。
CHANNEL_ITEMS = {
    "scale":    (":CHANnel%s:SCALe",    "V/div", "垂直档位"),
    "offset":   (":CHANnel%s:OFFSet",   "V",     "垂直偏移"),
    "coupling": (":CHANnel%s:COUPling", "",      "耦合（AC / DC / GND）"),
    "probe":    (":CHANnel%s:PROBe",    "",      "探头衰减比（1 / 10 / 100）"),
    "bwlimit":  (":CHANnel%s:BWLimit",  "",      "带宽限制（ON / OFF）"),
    "display":  (":CHANnel%s:DISPlay",  "",      "显示开关（ON / OFF）"),
}

TIMEBASE_ITEMS = {
    "scale":  (":TIMebase:MAIN:SCALe",  "s/div", "主时基"),
    "offset": (":TIMebase:MAIN:OFFSet", "s",     "时基偏移"),
    "mode":   (":TIMebase:MODE",        "",      "时基模式（MAIN / XY / ROLL）"),
}

TRIGGER_ITEMS = {
    "level":  (":TRIGger:EDGe:LEVel",  "V", "边沿触发电平"),
    "source": (":TRIGger:EDGe:SOURce", "",  "触发源（CHANnel1 / CHANnel2）"),
    "slope":  (":TRIGger:EDGe:SLOPe",  "",  "斜率（POSitive / NEGative / RFALl）"),
    "sweep":  (":TRIGger:SWEep",       "",  "扫描方式（AUTO / NORMal / SINGle）"),
}


def register(mcp):

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_configure(
        channel: Annotated[str | None, Field(
            description="要改的通道：CHANnel1 / CHANnel2。改通道参数时必填。",
        )] = None,
        channel_set: Annotated[dict | None, Field(
            description=('通道设置，如 {"scale": 0.5, "offset": 0, "coupling": "DC"}。'
                         "可改项：scale(V/div) offset(V) coupling probe bwlimit display"),
        )] = None,
        timebase_set: Annotated[dict | None, Field(
            description=('时基设置，如 {"scale": 1e-3}。'
                         "可改项：scale(s/div) offset(s) mode"),
        )] = None,
        trigger_set: Annotated[dict | None, Field(
            description=('触发设置，如 {"level": 1.5, "source": "CHANnel2"}。'
                         "可改项：level(V) source slope sweep"),
        )] = None,
    ) -> dict:
        """修改示波器设置。**会真改仪器**。

        ⚠️ 三项注意：
        1. 只有**显式给出**的项会被改动，未提及的参数一律不动。
        2. 每条命令下发前会**先实时读原值**，返回值里逐条列出"原值 → 新值"，
           你可以据此复核这次到底改了什么。
        3. 不做 :AUToscale（它一次改一堆，无法逐条记录原值）。

        本仪器只有两个通道，数值参数用十进制小数（0.5 表示 500 mV/div）。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        changes, bad = [], []

        if channel_set:
            chan = st.norm_chan(channel)
            if chan is None:
                return fmt.fail(
                    "改通道参数必须指定 channel",
                    "channel 用 CHANnel1 或 CHANnel2（也接受 CH1 / 1）。")
            idx = chan[-1]
            for key, val in channel_set.items():
                if key not in CHANNEL_ITEMS:
                    bad.append({"where": "channel_set", "key": key,
                                "allowed": sorted(CHANNEL_ITEMS)})
                    continue
                tpl, unit, label = CHANNEL_ITEMS[key]
                changes.append((tpl % idx, val, unit, "%s %s" % (chan, label)))

        for group, spec, where in (
            (timebase_set, TIMEBASE_ITEMS, "timebase_set"),
            (trigger_set, TRIGGER_ITEMS, "trigger_set"),
        ):
            for key, val in (group or {}).items():
                if key not in spec:
                    bad.append({"where": where, "key": key, "allowed": sorted(spec)})
                    continue
                cmd, unit, label = spec[key]
                changes.append((cmd, val, unit, label))

        if bad:
            return fmt.fail("有无法识别的设置项，**未下发任何命令**",
                            "非法项：%s" % bad)

        if not changes:
            return fmt.fail("没有给出任何要改的设置项",
                            "至少要给 channel_set / timebase_set / trigger_set 之一。")

        # ── 逐条：先读原值 → 过闸门 → 下发 ───────────────────────────
        from scope import link as _link
        from scope import scpi as _scpi

        applied, denied = [], []
        for cmd, new, unit, label in changes:
            old = conn.q(inst, cmd + "?")
            try:
                _scpi.guard(cmd)              # 闸门：不在白名单就抛
            except _scpi.Denied as exc:
                denied.append({"cmd": cmd, "label": label, "reason": str(exc)})
                continue
            try:
                _link.send(inst, "%s %s" % (cmd, _fmt_val(new)))
                applied.append({"cmd": cmd, "label": label, "unit": unit,
                                "old": _num(old), "new": _num(new),
                                "changed": _num(old) != _num(new)})
            except Exception as exc:
                denied.append({"cmd": cmd, "label": label,
                               "reason": "%s: %s" % (type(exc).__name__, exc)})

        return fmt.ok(
            ts=fmt.stamp(),
            applied=applied,
            rejected=denied,
            untouched="未在请求中出现的参数一个都没动。",
            note="old 为下发前实时读到的原值。",
        )

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_autofit(
        source: Annotated[str, Field(
            description="要布防的通道：CHANnel1 / CHANnel2",
        )] = "CHANnel1",
        cycles: Annotated[float, Field(
            description="希望屏上显示几个周期，默认 2.5",
        )] = 2.5,
    ) -> dict:
        """自动布防一个通道：调垂直档位、时基、触发电平、垂直偏移。

        **不走 :AUToscale** —— 它一次改一堆设置且无法逐条记录原值，已在项目
        红线清单里。本工具手算四项，每一项都先读原值再下发。

        流程：**窗口扫描**（时基从 1µs 到 50ms 逐档试，找到能看见信号的窗口
        —— 防止时间基停在窄档时窗口里连一个周期都没有）→ 抓波形 → 量出
        Vpp 和周期（上升沿间隔中位数，抗暂态漂移）→ 算出档位/时基 → 逐项下发。
        波形中点同时落到触发电平与屏幕中心（offset = -level），
        波形站得住也看得全。返回值列出每项的"原值 → 新值"、测量依据，
        以及窗口扫描走过的每一档。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        chan = st.norm_chan(source)
        if chan is None:
            return fmt.fail("通道名无法识别：%r" % source,
                            "请用 CHANnel1 或 CHANnel2。")

        from scope import link as _link
        from scope import wave as _wave

        # 第一步：垂直归位（1 V/div、offset 0）。AUTO 按完的典型烂摊子是
        # 0.1 V/div + offset -2.7 —— 信号整体落到窗口外，ADC 钉死在一条轨上，
        # 抓回来永远是平线，时基扫到天亮也没用。先回中性窗，信号才看得见。
        # 照例先读原值再写，原值留 probe 痕；最终 plan 会按实测重设这两项。
        v_scale_old = _num(conn.q(inst, "%s:SCALe?" % chan))
        v_off_old = _num(conn.q(inst, "%s:OFFSet?" % chan))
        _link.send(inst, "%s:SCALe %s" % (chan, _fmt_val(1.0)))
        _link.send(inst, "%s:OFFSet %s" % (chan, _fmt_val(0.0)))
        probe = [{"step": "vertical_norm", "scale_old": v_scale_old,
                  "offset_old": v_off_old, "scale_new": 1.0, "offset_new": 0.0}]

        # 第二步：窗口扫描。时间基可能停在极窄档（AUTO 对慢信号的老毛病是
        # 收到 1µs/div），窗口里连一个周期都装不下 → Vpp=0。
        # 从窄到宽逐档试，直到窗口里能看见信号（Vpp 和边沿都非零）。
        s = None
        for tb_try in (1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 5e-2):
            old_tb = conn.q(inst, ":TIMebase:MAIN:SCALe?")
            _link.send(inst, ":TIMebase:MAIN:SCALe %s" % _fmt_val(tb_try))
            probe.append({"tb_try": tb_try, "tb_old": _num(old_tb)})
            try:
                t0, dt, volts = _wave.fetch(inst, chan)
                s = _wave.stat(volts, dt)
            except Exception:
                s = None
            if s and s["vpp"] > 0.05 and s["rises"] >= 1:
                break
            s = None
        if not s:
            return fmt.fail(
                "扫完 1µs~50ms 六档时基都看不见信号，无法布防",
                "先确认该通道已开启（scope_state 看 %s DISPlay）、探头已接好、" % chan
                + "信号幅度是否太小。")
        vpp = s["vpp"]

        # 垂直：让波形占约 5 格（8 格满幅）
        want_scale = _wave.nearest_125(vpp / 5.0)
        # 水平：屏上 14 格，目标 cycles 个周期
        period = 1.0 / s["freq_est"] if s["freq_est"] > 0 else None
        want_tb = _wave.suggest_timebase(period, cycles=cycles) if period else None
        # 触发电平：取波形中点
        want_level = (s["vmax"] + s["vmin"]) / 2.0

        plan = [(("%s:SCALe" % chan), want_scale, "V/div", "垂直档位")]
        if want_tb:
            plan.append((":TIMebase:MAIN:SCALe", want_tb, "s/div", "主时基"))
        plan.append((":TRIGger:EDGe:LEVel", want_level, "V", "触发电平"))
        # 垂直偏移：把波形中点挪到屏幕中心。不设它，大信号配小档位
        # 会从屏顶溢出（0~5 V 信号配 1 V/div、offset=0 时顶掉整整 1 V）。
        # 与触发电平取反号，电平正好落在屏幕中心线上 —— 2026-09-24 实机
        # 手调验证过的配平关系（offset -0.392 / level +0.392）。
        plan.append((("%s:OFFSet" % chan), -want_level, "V", "垂直偏移"))

        applied = []
        for cmd, val, unit, label in plan:
            old = conn.q(inst, cmd + "?")
            try:
                _link.send(inst, "%s %s" % (cmd, _fmt_val(val)))
                applied.append({"cmd": cmd, "label": label, "unit": unit,
                                "old": _num(old), "new": _num(val)})
            except Exception as exc:
                applied.append({"cmd": cmd, "label": label, "unit": unit,
                                "old": _num(old), "error": str(exc)})

        return fmt.ok(
            ts=fmt.stamp(),
            source=chan,
            basis={"vpp": vpp, "vmin": s["vmin"], "vmax": s["vmax"],
                   "freq_est": s["freq_est"], "freq_src": s.get("freq_src", ""),
                   "points": s["n"]},
            probe=probe,
            applied=applied,
            note=("手算四项（档位/时基/电平/偏移），逐项先读原值再下发；"
                  "未走 :AUToscale。档位吸附到仪器的 1-2-5 序列。"),
        )

    return mcp


def _fmt_val(v):
    """数值 → SCPI 参数。字符串原样（ON / DC / POSitive）。"""
    if isinstance(v, bool):
        return "ON" if v else "OFF"
    if isinstance(v, (int, float)):
        return "%.6e" % v
    return str(v)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return v
