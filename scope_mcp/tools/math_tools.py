# -*- coding: utf-8 -*-
"""G 组工具：数学运算 / 参考波形 / 采集参数（3 个）。

    scope_math          数学运算开关与配置：ADD/SUB/MULT/DIV/FFT/LOGic/ADVanced
    scope_reference     参考波形：存/重置/选择源（易失内存，断电即失）
    scope_acquisition   采集模式：普通/平均/峰值/高分辨率 + 平均次数 + 存储深度

为什么这三样放一组：**都是"改变量的语义"而不是"改触发点"**。
    scope_math       —— 把 CH1/CH2 变成 MATH 波形（MATH 还能当 REF 的源）
    scope_reference  —— 存一帧到内部内存当基准，用来肉眼比对前后差异
    scope_acquisition—— 决定这一帧是怎么采的（平均 N 次 = 降噪、峰值 = 抓毛刺）

三者都**只影响画面内容，不影响探头上的物理信号**。

写命令的白名单已在 `scope/scpi.py` 的 B_LEVEL 里逐条登记；
本模块不重复做合法性判断（那是第 1 层闸门的事），只管"怎么用"。
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

# 手册 2-45 页 `:CALCulate:MODE`
MATH_MODES = ("ADD", "SUB", "MULTiply", "DIVision", "FFT", "LOGic", "ADVanced", "OFF")

# 手册 2-58 页 `:CALCulate:FFT:WINDow`
FFT_WINDOWS = ("RECTangle", "HANNing", "HAMMing", "BLACkman")

# 手册 2-16 页 `:ACQuire:TYPE`
ACQ_TYPES = ("NORMal", "AVERages", "PEAK", "HRESolution")

# 手册 `:ACQuire:AVERages`：2^n，n = 1..13
AVG_VALID = tuple(2 ** n for n in range(1, 14))

# 手册 `:ACQuire:MDEPth`：AUTO 或 7e3/7e4/7e5/7e6/2.8e7（最后一项为选件）
MEMPTH_VALID = ("AUTO", "7000", "70000", "700000", "7000000", "28000000")

# 手册 `:REFerence:SOURce`
REF_SOURCES = ("CHANnel1", "CHANnel2", "MATH")

# 数学运算各自有两个操作数 A / B
MATH_BINARY = {
    "ADD":      ":CALCulate:ADD",
    "SUB":      ":CALCulate:SUB",
    "MULTiply": ":CALCulate:MULTiply",
    "DIVision": ":CALCulate:DIVision",
    "LOGic":    ":CALCulate:LOGic",
}

# 运算模式 → 该模式下的可读子项（给模型看的菜单，顺带当作读回清单）
MATH_SUBITEMS = {
    "ADD":      ("SA", "SB", "INVert", "VSCale", "VOFFset"),
    "SUB":      ("SA", "SB", "INVert", "VSCale", "VOFFset"),
    "MULTiply": ("SA", "SB", "INVert", "VSCale", "VOFFset"),
    "DIVision": ("SA", "SB", "INVert", "VSCale", "VOFFset"),
    "LOGic":    ("SA", "SB", "INVert", "VSCale", "VOFFset",
                 "OPERator", "ATHReshold", "BTHReshold"),
    "FFT":      ("SOURce", "WINDow", "SPLit", "VSMode", "HSPan", "HCENter"),
    "ADVanced": ("EXPRession", "VARiable", "INVert", "VSCale", "VOFFset"),
}


def _read_mode(conn_mod, inst):
    """实时读当前数学运算模式（手册归一回值：ADD/SUB/MULT/DIV/FFT/LOG/ADV/OFF）。"""
    return conn_mod.q(inst, ":CALCulate:MODE?")


def register_read(mcp):
    """只读：数学运算与采集参数的现状。"""

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_math_status() -> dict:
        """读取数学运算（MATH）的当前配置（**不改任何设置**）。

        返回：
        - mode       当前运算：ADD / SUB / MULTiply / DIVision / FFT / LOGic / ADVanced / OFF
        - mode_raw   仪器原样回值（ADD/SUB/MULT/DIV/FFT/LOG/ADV/OFF）
        - items      该模式下的全部子项现值（源、档位、偏移、FFT 窗等）

        **注意**：MATH 显示的是"算出来的波形"，不是探头上的真实信号。
        拿 MATH 做定量结论（尤其 FFT 幅度）前，先确认源通道（SA/SB/SOURce）
        和档位（VSCale/VOFFset）没被人调过。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        raw = _read_mode(conn, inst)
        mode = _norm_mode(raw)
        out = fmt.ok(ts=fmt.stamp())
        out["mode"] = fmt.value("mode", raw)
        out["mode_raw"] = raw
        out["items"] = _read_subitems(conn, inst, mode)
        if not mode or mode == "OFF":
            out["note"] = "MATH 当前关闭。要用先 scope_math(mode=...)。"
        return out

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_acquisition_status() -> dict:
        """读取采集参数现状（**不改任何设置**）。

        返回（与 scope_acquire_status 的分工：那个看"停还是跑"，
        这个看"这一帧是怎么采的"）：
        - acquire_type  采集模式：NORMal / AVERages / PEAK / HRESolution
        - averages      平均次数（仅 AVERages 模式有效，值域 2^n）
        - mem_depth     存储深度（点数）
        - sample_rate   当前采样率（Sa/s）
        - ref           REF 参考波形的显示开关与源（只读推断：REF 无独立开关查询）

        **怎么用**：测噪声要选 AVERages（多平均降随机噪声）；
        抓毛刺要选 PEAK（保留每个采样区间内的极值）；
        测小信号又不想丢带宽选 HRESolution。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        out = fmt.ok(ts=fmt.stamp())
        for key, cmd in (("acquire_type", ":ACQuire:TYPE?"),
                         ("averages", ":ACQuire:AVERages?"),
                         ("mem_depth", ":ACQuire:MDEPth?"),
                         ("sample_rate", ":ACQuire:SRATe?"),
                         ("sweep", ":TRIGger:SWEep?")):
            out[key] = fmt.value(key, conn.q(inst, cmd))

        t = str(out.get("acquire_type", {}).get("value") or "").upper()
        # 仪器回的是缩写（NORM / AVER / PEAK / HRES），先归一
        t = {"NORM": "NORMAL", "AVER": "AVERAGES", "AVERAGES": "AVERAGES",
             "PEAK": "PEAK", "HRES": "HRESOLUTION",
             "HRESOLUTION": "HRESOLUTION"}.get(t, t)
        out["advice"] = {
            "NORMAL": "普通模式，等间隔采样 —— 通用首选。",
            "AVERAGES": "平均模式，随机噪声被压低 —— 适合看噪声底/小信号细节。",
            "PEAK": "峰值模式，保留区间内极值 —— 适合抓窄毛刺，代价是底噪看起来更胖。",
            "HRESOLUTION": "高分辨率模式，靠数字滤波换垂直分辨率 —— 适合小信号，带宽会降。",
        }.get(t, "未知采集模式（仪器回值 %r）。" % t)
        return out

    return mcp


def register_write(mcp):
    """写：数学运算配置 / 参考波形 / 采集参数。"""

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_math(
        mode: Annotated[str, Field(
            description=("数学运算类型：ADD / SUB / MULTiply / DIVision / FFT / "
                         "LOGic / ADVanced / OFF（关闭）。"),
        )] = "OFF",
        source_a: Annotated[str | None, Field(
            description="操作数 A 的源（ADD/SUB/MULT/DIV/LOGic 用）：CHANnel1 / CHANnel2。",
        )] = None,
        source_b: Annotated[str | None, Field(
            description="操作数 B 的源（ADD/SUB/MULT/DIV/LOGic 用）：CHANnel1 / CHANnel2。",
        )] = None,
        fft_window: Annotated[str | None, Field(
            description=("FFT 窗函数（mode=FFT 时用）：RECTangle / HANNing / "
                         "HAMMing / BLACkman。不同类型信号选不同窗，见返回 advice。"),
        )] = None,
        fft_source: Annotated[str | None, Field(
            description="FFT 的源通道（mode=FFT 时用）：CHANnel1 / CHANnel2。",
        )] = None,
        fft_vsmode: Annotated[str | None, Field(
            description="FFT 纵轴类型（mode=FFT 时用）：VRMS（线性均方根）/ DB（分贝）。",
        )] = None,
    ) -> dict:
        """设置数学运算（MATH）。**会真改仪器画面**。

        ⚠️ 写操作。只改**显式给出**的项；每条命令下发前先实时读原值，
        返回值逐条列出"原值 → 新值"。

        常见用法：
        - 看两通道差值：`mode="SUB", source_a="CHANnel1", source_b="CHANnel2"`
        - 开 FFT 看频谱：`mode="FFT", fft_source="CHANnel1", fft_window="HANNing"`
        - 关掉 MATH：`mode="OFF"`

        **FFT 幅度不可直接当真实幅度读** —— 与窗函数、采样率、栅格都有关系。
        要定量频谱请用 scope_analyze(kind="spectrum")（走 numpy 自己算）。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        plan, bad = [], []

        if mode:
            m = _match_mode(mode)
            if m is None:
                return fmt.fail("数学运算类型无法识别：%r" % mode,
                                "可选：%s" % (", ".join(MATH_MODES),))
            plan.append((":CALCulate:MODE", m, "MATH 运算类型"))

            # 只有二元运算才有 SA / SB
            if m in MATH_BINARY:
                base = MATH_BINARY[m]
                if source_a:
                    plan.append((base + ":SA", _norm_chan(source_a, bad, "source_a"), ""))
                if source_b:
                    plan.append((base + ":SB", _norm_chan(source_b, bad, "source_b"), ""))
            elif m == "FFT":
                if fft_source:
                    plan.append((":CALCulate:FFT:SOURce",
                                 _norm_chan(fft_source, bad, "fft_source"), "FFT 源"))
                if fft_window:
                    w = _match_one(fft_window, FFT_WINDOWS)
                    if w is None:
                        bad.append({"key": "fft_window", "got": fft_window,
                                    "allowed": list(FFT_WINDOWS)})
                    else:
                        plan.append((":CALCulate:FFT:WINDow", w, "FFT 窗函数"))
                if fft_vsmode:
                    v = _match_one(fft_vsmode, ("VRMS", "DB"))
                    if v is None:
                        bad.append({"key": "fft_vsmode", "got": fft_vsmode,
                                    "allowed": ["VRMS", "DB"]})
                    else:
                        plan.append((":CALCulate:FFT:VSMode", v, "FFT 纵轴类型"))

        if bad:
            return fmt.fail("有无法识别的参数，**未下发任何命令**", "问题项：%s" % bad)
        if not plan:
            return fmt.fail("没有给出任何要改的项", "至少给 mode，或 mode 之外的子项。")

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

        return fmt.ok(ts=fmt.stamp(), applied=applied, rejected=denied,
                      untouched="未在请求中出现的项一个都没动。",
                      note="old 为下发前实时读到的原值（SCPI 原样字符串）。")

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_reference(
        action: Annotated[str, Field(
            description=("动作：save（把源通道当前波形存为参考）/ "
                         "reset（把参考波形的档位与位置恢复为存时的设置）/ "
                         "source（只改参考源，不存）。"),
        )] = "save",
        source: Annotated[str | None, Field(
            description="参考源：CHANnel1 / CHANnel2 / MATH。只有**当前已开启**的通道能当源。",
        )] = None,
    ) -> dict:
        """管理 REF 参考波形（内部易失内存）。

        ⚠️ 写操作。REF 用来做**前后比对**：存一帧正常波形，之后改动电路
        再叠上去看差异。

        三条手册事实（2-211 页 `:REFerence:SAVe`）：
        1. 只存**屏幕区域**的波形，不是整段存储深度。
        2. 存在**易失内存**，**断电即失**。
        3. 存的时候会把当时的垂直档位与位置一并记下；用 reset 可把
           参考波形恢复成"存时看到的比例"，便于和当前波形同尺度比对。

        `:REFerence:DISPlay` 控制的是**REF 菜单是否打开**，不是"波形显示开关"，
        所以本工具改的是源与存储动作，不碰菜单。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        from scope import link as _link
        from scope import scpi as _scpi

        act = str(action or "").strip().lower()
        plan = []

        if source:
            s = _norm_chan(source, [], "source", allow_math=True)
            if s is None:
                return fmt.fail("参考源无法识别：%r" % source,
                                "可选：CHANnel1 / CHANnel2 / MATH。")
            plan.append((":REFerence:SOURce", s, "参考源"))

        if act == "save":
            plan.append((":REFerence:SAVe", None, "存为参考波形（易失内存）"))
        elif act == "reset":
            plan.append((":REFerence:RESet", None, "恢复参考波形的档位/位置"))
        elif act == "source":
            if not source:
                return fmt.fail("action=source 必须同时给 source",
                                "例如 scope_reference(action='source', source='CHANnel2')。")
        else:
            return fmt.fail("action 无法识别：%r" % action,
                            "可选：save / reset / source。")

        applied, denied = [], []
        for cmd, val, label in plan:
            try:
                _scpi.guard(cmd)
            except _scpi.Denied as exc:
                denied.append({"cmd": cmd, "label": label, "reason": str(exc)})
                continue
            try:
                _link.send(inst, cmd + (" " + val if val else ""))
                applied.append({"cmd": cmd, "label": label})
            except Exception as exc:
                denied.append({"cmd": cmd, "label": label,
                               "reason": "%s: %s" % (type(exc).__name__, exc)})

        return fmt.ok(ts=fmt.stamp(), applied=applied, rejected=denied,
                      note=("REF 存在易失内存，**断电即失**；"
                            "只存屏幕区域波形，不是整段存储深度。"))

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_acquisition(
        acquire_type: Annotated[str | None, Field(
            description=("采集模式：NORMal（普通）/ AVERages（平均，降随机噪声）/ "
                         "PEAK（峰值，抓毛刺）/ HRESolution（高分辨率，小信号）。"),
        )] = None,
        averages: Annotated[int | None, Field(
            description="平均次数，必须是 2 的幂且 ≤ 8192（2,4,8,...,8192）。仅在 AVERages 模式有意义。",
        )] = None,
        mem_depth: Annotated[str | None, Field(
            description=("存储深度（点数）：AUTO / 7000 / 70000 / 700000 / "
                         "7000000 / 28000000（最后一项需选件）。"),
        )] = None,
    ) -> dict:
        """设置采集参数。**会真改仪器**。

        ⚠️ 写操作。只改**显式给出**的项；每条下发前先实时读原值。

        典型场景：
        - 测噪声底要不要平均：`acquire_type="AVERages", averages=128`
          （平均 N 次，随机噪声按 √N 下降；代价是响应变慢）
        - 抓窄毛刺：`acquire_type="PEAK"`
        - 长时间窗又想要多点：`mem_depth="700000"`

        **注意**：改 mem_depth 会影响实际采样率（存储深度 ÷ 时间窗）。
        改完请调 scope_acquisition_status 复核 sample_rate。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        plan, bad = [], []

        if acquire_type:
            t = _match_one(acquire_type, ACQ_TYPES)
            if t is None:
                bad.append({"key": "acquire_type", "got": acquire_type,
                            "allowed": list(ACQ_TYPES)})
            else:
                plan.append((":ACQuire:TYPE", t, "采集模式"))

        if averages is not None:
            try:
                n = int(averages)
            except (TypeError, ValueError):
                bad.append({"key": "averages", "got": averages,
                            "allowed": "2 的幂，2..8192"})
            else:
                if n not in AVG_VALID:
                    bad.append({"key": "averages", "got": n,
                                "allowed": list(AVG_VALID)})
                else:
                    plan.append((":ACQuire:AVERages", str(n), "平均次数"))

        if mem_depth:
            d = str(mem_depth).strip().upper()
            if d not in MEMPTH_VALID:
                bad.append({"key": "mem_depth", "got": mem_depth,
                            "allowed": list(MEMPTH_VALID)})
            else:
                plan.append((":ACQuire:MDEPth", d, "存储深度"))

        if bad:
            return fmt.fail("有无法识别的参数，**未下发任何命令**", "问题项：%s" % bad)
        if not plan:
            return fmt.fail("没有给出任何要改的项",
                            "至少给 acquire_type / averages / mem_depth 之一。")

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
                applied.append({"cmd": cmd, "label": label, "old": old, "new": val})
            except Exception as exc:
                denied.append({"cmd": cmd, "label": label,
                               "reason": "%s: %s" % (type(exc).__name__, exc)})

        return fmt.ok(ts=fmt.stamp(), applied=applied, rejected=denied,
                      effective_sample_rate=fmt.value(
                          "sample_rate", conn.q(inst, ":ACQuire:SRATe?")),
                      note="old 为下发前实时读到的原值。")

    return mcp


# ── 内部小工具 ───────────────────────────────────────────────────────
def _norm_mode(raw):
    """手册归一回值 → 本模块用的规范名。"""
    t = str(raw or "").strip().upper()
    return {"ADD": "ADD", "SUB": "SUB", "MULT": "MULTiply", "DIV": "DIVision",
            "FFT": "FFT", "LOG": "LOGic", "ADV": "ADVanced",
            "OFF": "OFF"}.get(t, t or None)


def _match_mode(s):
    """用户输入 → SCPI 参数（含手册的长短写法）。"""
    t = str(s or "").strip().upper()
    for m in MATH_MODES:
        if t == m.upper():
            return m
    for short, full in (("MULT", "MULTiply"), ("DIV", "DIVision"),
                        ("LOG", "LOGic"), ("ADV", "ADVanced")):
        if t == short:
            return full
    return None


def _match_one(s, allowed):
    """在候选里做大小写无关匹配，返回候选里的规范写法。"""
    t = str(s or "").strip().upper()
    for a in allowed:
        if t == a.upper():
            return a
    # 允许前缀匹配（HANN → HANNing）
    if len(t) >= 3:
        for a in allowed:
            if a.upper().startswith(t):
                return a
    return None


def _norm_chan(s, bad, key, allow_math=False):
    """通道名归一。识别不了就记到 bad（调用方决定是否中止）。"""
    from .. import state as st
    if allow_math and str(s or "").strip().upper() == "MATH":
        return "MATH"
    ch = st.norm_chan(s)
    if ch is None:
        if isinstance(bad, list):
            bad.append({"key": key, "got": s,
                        "allowed": ["CHANnel1", "CHANnel2"] + (["MATH"] if allow_math else [])})
        return None
    return ch


def _read_subitems(conn_mod, inst, mode):
    """把当前模式下的子项现值全读一遍（统一走 fmt.value 包装）。"""
    if not mode or mode == "OFF":
        return {}
    sub = MATH_SUBITEMS.get(mode, ())
    out = {}
    if mode in MATH_BINARY:
        base = MATH_BINARY[mode]
        for s in sub:
            cmd = ("%s:%s" % (base, s)) if s in ("SA", "SB") \
                else (":CALCulate:%s:%s" % (mode, s))
            out[s] = fmt.value(s, conn_mod.q(inst, cmd + "?"))
    elif mode == "FFT":
        for s in sub:
            out[s] = fmt.value(s, conn_mod.q(inst, ":CALCulate:FFT:%s?" % s))
    elif mode == "ADVanced":
        for s in sub:
            out[s] = fmt.value(s, conn_mod.q(inst, ":CALCulate:ADVanced:%s?" % s))
    return out
