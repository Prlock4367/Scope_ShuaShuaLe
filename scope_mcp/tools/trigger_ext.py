# -*- coding: utf-8 -*-
"""H 组工具：触发配置（2 个）。

    scope_trigger_config      基础触发：类型 / 源 / 电平 / 斜率 / 耦合 / 扫描 / 释抑 / 噪声抑制
    scope_trigger_advanced    高级触发：脉冲宽度 / 斜率时间 / 欠幅 / 码型 / 建立保持

为什么拆两个：
    基础 8 项是**任何触发类型都用得上**的公共旋钮（手册 2-245～2-248 页）；
    高级几组各自有独立的子参数树（脉冲 5 项、斜率 6 项、欠幅 8 项、
    码型 3 项、建立保持 8 项）。混在一个工具里参数会长到没人愿意读。

⚠️ 选件状态（本机实测）：
    TRIGger 选件 = 0（**未装**）→ PULSe / RUNT / SLOPe / SHOLd / PATTern
    这些都是**标配**功能，不属选件，可以正常用。
    真正未装的是：CAN / DELay / DURATion / NEDGe / TIMeout / USB / VIDeo(HDTV) / WINDows。
    本模块只覆盖标配的 5 种，未装的**一个都不注册**（模型看不见就不会去调）。

手册依据：
    :TRIGger:COUPling  2-245   {DC|AC|LFReject|HFReject}
    :TRIGger:HOLDoff   2-245   100ns ~ 10s
    :TRIGger:MODE      2-246   16 种（本模块只放标配部分）
    :TRIGger:NREJect   2-246   Bool
    :TRIGger:STATus?   2-247   TD/WAIT/RUN/AUTO/STOP
    :TRIGger:SWEep     2-248   {AUTO|NORMal|SINGle}
    :TRIGger:EDGe:*    2-261   SOURce {CHANnel1|CHANnel2|EXT|ACLine} / SLOPe / LEVel
    :TRIGger:PULSe:*   2-275   WHEN/LWIDth/UWIDth/LEVel/SOURce
    :TRIGger:SLOPe:*   2-293   WHEN/TLOWer/TUPPer/LEVel{WINDow,ALEVel,BLEVel}/SOURce
    :TRIGger:RUNT:*    2-285   WHEN/POLarity/WLOWer/WUPPer/ALEVel/BLEVel/SOURce
    :TRIGger:PATTern:* 2-272   PATTern{HLXRF}/SOURce/LEVel
    :TRIGger:SHOLd:*   2-289   TYPe{setup,hold,both}/CSrc/DSrc/SLOPe/STIMe/HTIMe/PATTern
"""
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import conn, format as fmt
from ..guard import requires_write, serialized

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True,
)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True,
)

# 手册 2-246 `:TRIGger:MODE` 里**标配**可用的部分。
# 未装选件的（CAN/DELay/DURATion/NEDGe/TIMeout/USB/VIDeo/WINDows）
# 与需要解码选件的串行总线（RS232/IIC/SPI）一律不放进来。
MODE_STD = ("EDGE", "PULSe", "RUNT", "SLOPe", "PATTern", "SHOLd")

# 模式 → 该模式下可读的子项（给模型的菜单 + 读回清单）
MODE_ITEMS = {
    "EDGE":    (("SOURce",  ":TRIGger:EDGe:SOURce"),
                ("SLOPe",   ":TRIGger:EDGe:SLOPe"),
                ("LEVel",   ":TRIGger:EDGe:LEVel")),
    "PULSe":   (("SOURce",  ":TRIGger:PULSe:SOURce"),
                ("WHEN",    ":TRIGger:PULSe:WHEN"),
                ("LEVel",   ":TRIGger:PULSe:LEVel"),
                ("LWIDth",  ":TRIGger:PULSe:LWIDth"),
                ("UWIDth",  ":TRIGger:PULSe:UWIDth")),
    "SLOPe":   (("SOURce",  ":TRIGger:SLOPe:SOURce"),
                ("WHEN",    ":TRIGger:SLOPe:WHEN"),
                ("TLOWer",  ":TRIGger:SLOPe:TLOWer"),
                ("TUPPer",  ":TRIGger:SLOPe:TUPPer"),
                ("WINDow",  ":TRIGger:SLOPe:WINDow"),
                ("ALEVel",  ":TRIGger:SLOPe:ALEVel"),
                ("BLEVel",  ":TRIGger:SLOPe:BLEVel")),
    "RUNT":    (("SOURce",  ":TRIGger:RUNT:SOURce"),
                ("WHEN",    ":TRIGger:RUNT:WHEN"),
                ("POLarity", ":TRIGger:RUNT:POLarity"),
                ("WLOWer",  ":TRIGger:RUNT:WLOWer"),
                ("WUPPer",  ":TRIGger:RUNT:WUPPer"),
                ("ALEVel",  ":TRIGger:RUNT:ALEVel"),
                ("BLEVel",  ":TRIGger:RUNT:BLEVel")),
    "PATTern": (("SOURce",  ":TRIGger:PATTern:SOURce"),
                ("PATTern", ":TRIGger:PATTern:PATTern")),
    "SHOLd":   (("TYPe",    ":TRIGger:SHOLd:TYPe"),
                ("CSrc",    ":TRIGger:SHOLd:CSrc"),
                ("DSrc",    ":TRIGger:SHOLd:DSrc"),
                ("SLOPe",   ":TRIGger:SHOLd:SLOPe"),
                ("STIMe",   ":TRIGger:SHOLd:STIMe"),
                ("HTIMe",   ":TRIGger:SHOLd:HTIMe"),
                ("PATTern", ":TRIGger:SHOLd:PATTern")),
}

BASIC_ITEMS = {
    "source":  (":TRIGger:EDGe:SOURce", "CHANnel1|CHANnel2|EXT|ACLine", "触发源（边沿触发时生效）"),
    "slope":   (":TRIGger:EDGe:SLOPe",  "POSitive|NEGative|RFALl",      "边沿斜率"),
    "level":   (":TRIGger:EDGe:LEVel",  "Real",                          "边沿触发电平(V)"),
    "coupling": (":TRIGger:COUPling",   "DC|AC|LFReject|HFReject",       "触发耦合（仅边沿触发有效）"),
    "sweep":   (":TRIGger:SWEep",       "AUTO|NORMal|SINGle",            "扫描方式"),
    "holdoff": (":TRIGger:HOLDoff",     "Real 100ns~10s",                "释抑时间(s)"),
    "nreject": (":TRIGger:NREJect",     "ON|OFF",                        "噪声抑制开关"),
}

# 各高级模式的 WHEN 取值（手册逐条核过）
WHEN_BY_MODE = {
    "PULSe": ("PGReater", "PLESs", "NGReater", "NLESs", "PGLess", "NGLess"),
    "SLOPe": ("PGReater", "PLESs", "NGReater", "NLESs", "PGLess", "NGLess"),
    "RUNT":  ("NONE", "GREater", "LESS", "GLESs"),
}


def register_read(mcp):
    """只读：触发配置现状。"""

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_trigger_status() -> dict:
        """读取完整的触发配置现状（**不改任何设置**）。

        返回：
        - mode         触发类型：EDGE / PULSe / RUNT / SLOPe / PATTern / SHOLd
        - status       触发状态：TD（已触发）/ WAIT（等触发）/ RUN / AUTO / STOP
        - sweep        扫描方式：AUTO / NORMal / SINGle
        - coupling     触发耦合：DC / AC / LFReject / HFReject
        - holdoff      释抑时间（s）
        - nreject      噪声抑制开关
        - items        **当前触发类型专属**的全部子项（源/电平/宽度/时间…）

        **注意**：先看 mode 再看 items —— 子项随 mode 变，
        比如 mode=EDGE 时没有 LWIDth（那是脉冲触发的）。

        **怎么用**：读数值前先看 status。WAIT 说明还没触发，
        此时画面是上一次的残留或空白。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        out = fmt.ok(ts=fmt.stamp())
        raw_mode = conn.q(inst, ":TRIGger:MODE?")
        mode = _norm_mode(raw_mode)
        out["mode"] = fmt.value("mode", raw_mode)
        out["mode_code"] = mode
        for key, cmd in (("status", ":TRIGger:STATus?"),
                         ("sweep", ":TRIGger:SWEep?"),
                         ("coupling", ":TRIGger:COUPling?"),
                         ("holdoff", ":TRIGger:HOLDoff?"),
                         ("nreject", ":TRIGger:NREJect?")):
            out[key] = fmt.value(key, conn.q(inst, cmd))

        items = {}
        for name, cmd in MODE_ITEMS.get(mode, ()):
            items[name] = fmt.value(name, conn.q(inst, cmd + "?"))
        out["items"] = items

        st = str(out.get("status", {}).get("value") or "").upper()
        out["verdict"] = {
            "TD": "已触发 —— 画面冻结在触发点，适合读数。",
            "STOP": "已停止 —— 适合读数（但不保证是触发到的位置）。",
            "WAIT": "在等触发 —— 画面不代表稳定状态，读数无意义。",
            "RUN": "自动运行中 —— 读数会漂。",
            "AUTO": "自动运行中 —— 读数会漂。",
        }.get(st, "触发状态未知。")
        return out

    return mcp


def register_write(mcp):
    """写：基础触发配置 + 高级触发类型配置。"""

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_trigger_config(
        mode: Annotated[str | None, Field(
            description=("触发类型：EDGE（边沿）/ PULSe（脉冲宽度）/ RUNT（欠幅）/ "
                         "SLOPe（斜率）/ PATTern（码型）/ SHOLd（建立保持）。"
                         "**不传就是不改**。"),
        )] = None,
        source: Annotated[str | None, Field(
            description="触发源：CHANnel1 / CHANnel2 / EXT / ACLine（ACLine 仅边沿触发可用）。",
        )] = None,
        slope: Annotated[str | None, Field(
            description="边沿斜率：POSitive（上升）/ NEGative（下降）/ RFALl（双沿）。",
        )] = None,
        level: Annotated[float | None, Field(
            description="触发电平(V)。注意不是时基上的时间，是电压。",
        )] = None,
        coupling: Annotated[str | None, Field(
            description=("触发耦合：DC / AC（隔直，衰减 <75Hz）/ "
                         "LFReject（抑制 <75kHz）/ HFReject（抑制 >75kHz）。**仅边沿触发有效**。"),
        )] = None,
        sweep: Annotated[str | None, Field(
            description="扫描方式：AUTO（无触发也滚）/ NORMal（只在触到时更新）/ SINGle（一次后停）。",
        )] = None,
        holdoff: Annotated[float | None, Field(
            description=("释抑时间(s)，100ns ~ 10s。**怕抓错周期用这个** —— "
                         "设成信号周期略大，强制跳过若干周期再认触发。"),
        )] = None,
        nreject: Annotated[bool | None, Field(
            description="噪声抑制：True 打开（低带宽，抗噪但会引入延时误差）。",
        )] = None,
    ) -> dict:
        """设置基础触发参数（任何触发类型都用得上的公共旋钮）。

        ⚠️ 写操作。只改**显式给出**的项；每条下发前先实时读原值，
        返回值逐条列出"原值 → 新值"。

        常见的"触发抓不稳"三招：
        1. `sweep="NORMal"` —— AUTO 模式下没触发也在滚，画面看着就是"抖"。
        2. `holdoff=周期×1.2` —— 复杂波形里仪器会错认边沿，加释抑能锁死。
        3. `nreject=True` —— 信号上叠了高频噪声导致触发点乱跳。

        **注意** coupling 只在边沿触发下生效（手册 2-245 明说），
        其它模式下设了也没用。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        plan, bad = [], []

        if mode:
            m = _match_in(mode, MODE_STD)
            if m is None:
                return fmt.fail("触发类型无法识别：%r" % mode,
                                "本机可用（标配）：%s。选件功能未装，不在此列。"
                                % (", ".join(MODE_STD),))
            plan.append((":TRIGger:MODE", m, "触发类型"))

        if source:
            s = _match_chan(source)
            if s is None:
                bad.append({"key": "source", "got": source,
                            "allowed": ["CHANnel1", "CHANnel2", "EXT", "ACLine"]})
            else:
                plan.append((":TRIGger:EDGe:SOURce", s, "触发源"))

        if slope:
            s = _match_in(slope, ("POSitive", "NEGative", "RFALl"))
            if s is None:
                bad.append({"key": "slope", "got": slope,
                            "allowed": ["POSitive", "NEGative", "RFALl"]})
            else:
                plan.append((":TRIGger:EDGe:SLOPe", s, "边沿斜率"))

        if level is not None:
            plan.append((":TRIGger:EDGe:LEVel", _num_s(level), "边沿触发电平"))

        if coupling:
            c = _match_in(coupling, ("DC", "AC", "LFReject", "HFReject"))
            if c is None:
                bad.append({"key": "coupling", "got": coupling,
                            "allowed": ["DC", "AC", "LFReject", "HFReject"]})
            else:
                plan.append((":TRIGger:COUPling", c, "触发耦合"))

        if sweep:
            s = _match_in(sweep, ("AUTO", "NORMal", "SINGle"))
            if s is None:
                bad.append({"key": "sweep", "got": sweep,
                            "allowed": ["AUTO", "NORMal", "SINGle"]})
            else:
                plan.append((":TRIGger:SWEep", s, "扫描方式"))

        if holdoff is not None:
            h = float(holdoff)
            if not (1e-7 <= h <= 10.0):
                bad.append({"key": "holdoff", "got": holdoff,
                            "allowed": "100ns ~ 10s"})
            else:
                plan.append((":TRIGger:HOLDoff", "%.6e" % h, "释抑时间"))

        if nreject is not None:
            plan.append((":TRIGger:NREJect", "ON" if nreject else "OFF", "噪声抑制"))

        if bad:
            return fmt.fail("有无法识别的参数，**未下发任何命令**", "问题项：%s" % bad)
        if not plan:
            return fmt.fail("没有给出任何要改的项",
                            "至少给 mode / source / level / sweep / coupling / "
                            "holdoff / nreject 之一。")

        applied, denied = _apply(inst, plan)
        return fmt.ok(ts=fmt.stamp(), applied=applied, rejected=denied,
                      untouched="未在请求中出现的项一个都没动。",
                      note="old 为下发前实时读到的原值。")

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_trigger_advanced(
        kind: Annotated[str, Field(
            description=("高级触发类型：PULSe（脉冲宽度）/ SLOPe（斜率时间）/ "
                         "RUNT（欠幅）/ PATTern（码型）/ SHOLd（建立保持）。这是要配置哪一类。"),
        )],
        when: Annotated[str | None, Field(
            description=("判定条件。PULSe/SLOPe：PGReater/PLESs/NGReater/NLESs/PGLess/NGLess；"
                         "RUNT：NONE/GREater/LESS/GLESs。含义见返回 advice。"),
        )] = None,
        source: Annotated[str | None, Field(
            description="源通道：CHANnel1 / CHANnel2（SLOPe/RUNT/PATTern 只允许这两个）。",
        )] = None,
        lower: Annotated[float | None, Field(
            description=("下限值。PULSe=脉冲宽度下限LWIDth(s)；SLOPe=斜率时间下限TLOWer(s)；"
                         "RUNT=脉宽下限WLOWer(s)；SHOLd=建立时间STIMe(s)。"),
        )] = None,
        upper: Annotated[float | None, Field(
            description=("上限值。PULSe=脉冲宽度上限UWIDth(s)；SLOPe=斜率时间上限TUPPer(s)；"
                         "RUNT=脉宽上限WUPPer(s)；SHOLd=保持时间HTIMe(s)。"),
        )] = None,
        level_a: Annotated[float | None, Field(
            description="触发电平A(V)。SLOPe/RUNT 用（窗的上/下沿）。",
        )] = None,
        level_b: Annotated[float | None, Field(
            description="触发电平B(V)。SLOPe/RUNT 用。",
        )] = None,
        polarity: Annotated[str | None, Field(
            description="脉宽极性（RUNT 用）：POSitive / NEGative。",
        )] = None,
        pattern: Annotated[str | None, Field(
            description=("码型（PATTern 用）：两个字符，对应 CH1/CH2。"
                         "每个字符取 H（高）/ L（低）/ X（无关）/ R（上升）/ F（下降），"
                         "如 'HX' 或 'RX'。"),
        )] = None,
        hold_type: Annotated[str | None, Field(
            description="建立保持类型（SHOLd 用）：SETup / HOLd / SETHOLd。",
        )] = None,
        clock_src: Annotated[str | None, Field(
            description="时钟源（SHOLd 用）：CHANnel1 / CHANnel2。",
        )] = None,
        data_src: Annotated[str | None, Field(
            description="数据源（SHOLd 用）：CHANnel1 / CHANnel2。",
        )] = None,
    ) -> dict:
        """配置高级触发类型（脉冲宽度 / 斜率 / 欠幅 / 码型 / 建立保持）。

        ⚠️ 写操作。**自动把触发类型切到 kind 指定的那一种**，然后逐项下发。

        各类怎么用（判据都在手册 2-272～2-293 页）：

        - **PULSe 脉冲宽度**：抓"异常宽的脉冲"或"异常窄的毛刺"。
          `when="PGReater", lower=1e-6` = 正脉宽大于 1µs 时触发。
          配合 `lower`（LWIDth）或 `upper`（UWIDth），取决于是 Greater 还是 Less。

        - **SLOPe 斜率时间**：抓"边沿变缓"（驱动能力下降、电容增大）。
          `when="PGReater", lower=1e-6` = 正斜率时间大于 1µs。
          需先设 level_a / level_b 做双电平窗口。

        - **RUNT 欠幅**：抓"没到满幅的中间态脉冲" —— 数字信号最典型的故障。
          需先设 level_a / level_b 定义正常幅度范围。

        - **PATTern 码型**：按两通道的高低组合触发，抓特定逻辑状态。
          `pattern="HL"` = CH1 高且 CH2 低时触发。

        - **SHOLd 建立保持**：抓数字时序违例。需指定时钟线与数据线。

        返回 `advice` 会按 kind 给出该类的**参数使用顺序**（先设哪个后设哪个，
        顺序错了仪器可能回 -113 或参数被夹到边界）。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        k = _match_in(kind, ("PULSe", "SLOPe", "RUNT", "PATTern", "SHOLd"))
        if k is None:
            return fmt.fail("高级触发类型无法识别：%r" % kind,
                            "可选：PULSe / SLOPe / RUNT / PATTern / SHOLd。"
                            "（VIDeo / NEDGe / WINDows 等属选件，本机未装。）")

        plan, bad = [(":TRIGger:MODE", k, "触发类型 → %s" % k)], []

        if k == "PULSe":
            _add_choice(plan, bad, ":TRIGger:PULSe:WHEN", when,
                        WHEN_BY_MODE["PULSe"], "when")
            _add_chan(plan, bad, ":TRIGger:PULSe:SOURce", source, "source")
            _add_num(plan, bad, ":TRIGger:PULSe:LEVel", level_a, "level_a", 0)
            _add_num(plan, bad, ":TRIGger:PULSe:LWIDth", lower, "lower", 1)
            _add_num(plan, bad, ":TRIGger:PULSe:UWIDth", upper, "upper", 1)

        elif k == "SLOPe":
            _add_choice(plan, bad, ":TRIGger:SLOPe:WHEN", when,
                        WHEN_BY_MODE["SLOPe"], "when")
            _add_chan(plan, bad, ":TRIGger:SLOPe:SOURce", source, "source")
            _add_num(plan, bad, ":TRIGger:SLOPe:ALEVel", level_a, "level_a", 1)
            _add_num(plan, bad, ":TRIGger:SLOPe:BLEVel", level_b, "level_b", 1)
            _add_num(plan, bad, ":TRIGger:SLOPe:TLOWer", lower, "lower", 0)
            _add_num(plan, bad, ":TRIGger:SLOPe:TUPPer", upper, "upper", 0)

        elif k == "RUNT":
            _add_choice(plan, bad, ":TRIGger:RUNT:WHEN", when,
                        WHEN_BY_MODE["RUNT"], "when")
            _add_choice(plan, bad, ":TRIGger:RUNT:POLarity", polarity,
                        ("POSitive", "NEGative"), "polarity")
            _add_chan(plan, bad, ":TRIGger:RUNT:SOURce", source, "source")
            _add_num(plan, bad, ":TRIGger:RUNT:ALEVel", level_a, "level_a", 1)
            _add_num(plan, bad, ":TRIGger:RUNT:BLEVel", level_b, "level_b", 1)
            _add_num(plan, bad, ":TRIGger:RUNT:WLOWer", lower, "lower", 1)
            _add_num(plan, bad, ":TRIGger:RUNT:WUPPer", upper, "upper", 1)

        elif k == "PATTern":
            _add_chan(plan, bad, ":TRIGger:PATTern:SOURce", source, "source", no_ext=True)
            if pattern:
                p = str(pattern).strip().upper()
                if len(p) != 2 or any(c not in "HLXRF" for c in p):
                    bad.append({"key": "pattern", "got": pattern,
                                "allowed": "两个字符，各取 H/L/X/R/F"})
                else:
                    plan.append((":TRIGger:PATTern:PATTern", p, "码型"))

        elif k == "SHOLd":
            _add_choice(plan, bad, ":TRIGger:SHOLd:TYPe", hold_type,
                        ("SETup", "HOLd", "SETHOLd"), "hold_type")
            _add_chan(plan, bad, ":TRIGger:SHOLd:CSrc", clock_src,
                      "clock_src", no_ext=True)
            _add_chan(plan, bad, ":TRIGger:SHOLd:DSrc", data_src,
                      "data_src", no_ext=True)
            _add_num(plan, bad, ":TRIGger:SHOLd:STIMe", lower, "lower", 1)
            _add_num(plan, bad, ":TRIGger:SHOLd:HTIMe", upper, "upper", 1)

        if bad:
            return fmt.fail("有无法识别的参数，**未下发任何命令**", "问题项：%s" % bad)
        if len(plan) <= 1:
            return fmt.fail(
                "只给了 kind=%s，没有任何子参数，**未下发任何命令**" % k,
                "按 advice 的顺序补参数：%s" % _advice(k))

        applied, denied = _apply(inst, plan)
        return fmt.ok(ts=fmt.stamp(), kind=k, applied=applied, rejected=denied,
                      advice=_advice(k),
                      note=("已自动切换触发类型到 %s。"
                            "old 为下发前实时读到的原值。" % k))


# ── 内部小工具 ───────────────────────────────────────────────────────
# ── 内部小工具 ───────────────────────────────────────────────────────
# 手册归一写法 → 本模块用的规范名（`:TRIGger:MODE?` 的 Return Format 段）
_MODE_ALIAS = {
    "EDGE": "EDGE", "PULS": "PULSe", "RUNT": "RUNT", "WIND": "WINDows",
    "NEDG": "NEDGe", "SLOP": "SLOPe", "VID": "VIDeo", "PATT": "PATTern",
    "DEL": "DELay", "TIM": "TIMeout", "DURAT": "DURATion", "SHOL": "SHOLd",
    "RS232": "RS232", "IIC": "IIC", "SPI": "SPI", "USB": "USB", "CAN": "CAN",
}


def _norm_mode(raw):
    """手册归一回值 → 规范模式名（用于查 MODE_ITEMS）。"""
    t = str(raw or "").strip().upper()
    return _MODE_ALIAS.get(t, t or None)


def _apply(inst, plan):
    """逐条：读原值 → 过闸门 → 下发。"""
    from scope import link as _link
    from scope import scpi as _scpi

    applied, denied = [], []
    for cmd, val, label in plan:
        old = conn.q(inst, cmd + "?")
        try:
            _scpi.guard(cmd)
        except _scpi.Denied as exc:
            denied.append({"cmd": cmd, "label": label, "reason": str(exc)})
            continue
        try:
            _link.send(inst, "%s %s" % (cmd, val))
            applied.append({"cmd": cmd, "label": label,
                            "old": old, "new": val})
        except Exception as exc:
            denied.append({"cmd": cmd, "label": label,
                           "reason": "%s: %s" % (type(exc).__name__, exc)})
    return applied, denied


def _match_in(s, allowed):
    """大小写无关 + 前缀匹配，返回候选里的规范写法。"""
    t = str(s or "").strip().upper()
    if not t:
        return None
    for a in allowed:
        if t == a.upper():
            return a
    if len(t) >= 3:
        for a in allowed:
            if a.upper().startswith(t):
                return a
    return None


def _match_chan(s):
    """触发源归一：通道 / EXT / ACLine。"""
    from .. import state as st
    t = str(s or "").strip().upper()
    if t in ("EXT", "EXTERNAL"):
        return "EXT"
    if t in ("ACLINE", "ACL", "LINE"):
        return "ACLine"
    return st.norm_chan(s)


def _num_s(v):
    return "%.6e" % float(v)


def _add_choice(plan, bad, cmd, val, allowed, key):
    if val is None:
        return
    m = _match_in(val, allowed)
    if m is None:
        bad.append({"key": key, "got": val, "allowed": list(allowed)})
    else:
        plan.append((cmd, m, key))


def _add_chan(plan, bad, cmd, val, key, no_ext=False):
    if val is None:
        return
    m = _match_chan(val)
    if m is None or (no_ext and m in ("EXT", "ACLine")):
        bad.append({"key": key, "got": val,
                    "allowed": ["CHANnel1", "CHANnel2"] if no_ext
                               else ["CHANnel1", "CHANnel2", "EXT"]})
    else:
        plan.append((cmd, m, key))


def _add_num(plan, bad, cmd, val, key, min_exp=None):
    """数值项。min_exp 给下限的 10 的幂（None 表示不限）。"""
    if val is None:
        return
    try:
        f = float(val)
    except (TypeError, ValueError):
        bad.append({"key": key, "got": val, "allowed": "数值（秒）"})
        return
    if min_exp is not None and f < 10.0 ** min_exp:
        bad.append({"key": key, "got": val,
                    "allowed": "≥ %g s（2ns 是仪器最小分辨）" % (10.0 ** min_exp)})
        return
    plan.append((cmd, "%.6e" % f, key))


def _advice(kind):
    return {
        "PULSe": ("顺序：先 WHEN（定 Greater/Less）→ 再 SOURce → LEVel → 宽度。"
                  "Greater 用 lower(LWIDth)，Less 用 upper(UWIDth)，给错了仪器会夹边界。"),
        "SLOPe": ("顺序：先 WHEN → SOURce → ALEVel/BLEVel（双电平窗口）→ TLOWer/TUPPer。"
                  "**必须先设 level_a/level_b**，否则默认 0V 双电平算不出斜率。"),
        "RUNT": ("顺序：先 WHEN → POLarity → SOURce → ALEVel/BLEVel（正常幅度范围）→ 宽度。"
                 "**必须先设 level_a/level_b** 定义「正常幅度」，欠幅才有判据。"),
        "PATTern": ("顺序：SOURce → PATTern（两字符 H/L/X/R/F）。"
                    "X=无关，R/F=上升/下降沿。只开着一个通道时另一个通道的码型仍会生效，"
                    "注意别把关闭通道设成 H/L。"),
        "SHOLd": ("顺序：TYPe → CSrc/DSrc（时钟线/数据线）→ STIMe/HTIMe。"
                  "SETup 只看 lower(STIMe)，HOLd 只看 upper(HTIMe)，SETHOLd 两者都看。"),
    }.get(kind, "")
