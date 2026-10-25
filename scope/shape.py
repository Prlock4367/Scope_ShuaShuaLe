# -*- coding: utf-8 -*-
"""波形形态判读与质量告警 —— 回答"这是什么波、这读数能不能信"。

与 wave.py 的分工：
    wave.py   算**数值**（统计 / 频谱 / 自相关 / 包络 / 量化台阶）
    shape.py  下**判断**（形态归类 + 质量告警 + 一句话结论）

设计要点：
    - **型号无关**。只吃 (volts, dt, scale_vdiv)，任何示波器抓回来的波形都能喂进来。
      不写任何 "RIGOL" / 型号字样 —— 日后开源给别人用，别人也能直接吃。
    - **不重复造轮子**。过零用 wave.crossings（已有迟滞），量化用 wave.quant_report。
    - **告警要能教模型下一步**。每条告警都带"怎么修"，不只是一句"有风险"。

形态判读的启发来自 erebusnz/rigol-mcp（MIT, Copyright (c) 2026 Stig Manning）
的 waveform_analysis.py，但：
    - 判定顺序重排、阈值外提为常量便于调
    - 与 wave.py 的现有能力合并（频谱/自相关/量化体检本仓库已独立实现）
    - 输出改为结构化 dict（原版直接拼文本），便于 MCP 返回与二次消费
"""
import numpy as np

from . import wave

# 屏幕竖直方向 8 格 → 满屏 Vpp = scale × 8。这是全行业示波器通用约定，不是某型号特有。
SCREEN_V_DIV = 8.0

# 填充率低于此值 → 判为噪声底：波形几乎没撑开，读数全是 ADC 台阶造出来的
NOISE_FILL = 0.10
# 填充率在此与 NOISE_FILL 之间 → 真信号但偏小，可用但会抖，给低幅告警
LOW_FILL = 0.20

# 过零迟滞系数（取半幅的比例）。不加迟滞，噪声会在均值附近抖出多次穿越，
# 把频率整数倍地虚高 —— 真机实测踩过的坑，别去掉迟滞。
CROSS_HYST_FRAC = 0.25

# 双峰判据：靠近上下轨（±15% Vpp）的采样占比超过此值 → 判为脉冲/方波
PULSE_RAIL_FRAC = 0.15
PULSE_NEAR_RAIL = 0.70

# 闭锁（削顶）判据：连续 N 点几乎不变且贴近轨 → 削平
CLIP_FLAT_EPS_FRAC = 0.001     # 相邻点变化小于 Vpp 的这个比例算"平"
CLIP_RAIL_BAND_FRAC = 0.03     # 距轨多近算"贴轨"
CLIP_MIN_RUN_FRAC = 0.005      # 最短平台点数占比
CLIP_MIN_RUN_ABS = 5           # 与上取大。下限放低是因为真机采样率高时
                               # 平台点数多，但示波器屏幕点数少时平台也短

# 周期抖动：半周期间隔的变异系数超过此值 → 可能非周期/调频/混叠
JITTER_CV_WARN = 0.20
# DC 基线漂移：头尾（各取整数个周期）均值差超过 Vpp 此比例 → 判漂移
BASELINE_SHIFT_FRAC = 0.15
# 突发：头/尾段 RMS 低于主体此比例 → 判静默（突发放中间）
BURST_QUIET_FRAC = 0.15


def fmt_si(value, unit="", digits=4):
    """1350000 → '1.35 MHz'。模型读带单位的数比读裸数字省 token 也不易错。

    零、NaN 单独处理；其余按 1e9/1e6/1e3/1/1e-3/1e-6/1e-9 吸附。
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "%s %s" % (value, unit)
    if v != v:                                    # NaN
        return "— %s" % unit if unit else "—"
    if v == 0:
        return "0 %s" % unit if unit else "0"
    for thr, pre in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, ""),
                     (1e-3, "m"), (1e-6, "µ"), (1e-9, "n")):
        if abs(v) >= thr:
            return "%.*g %s%s" % (digits, v / thr, pre, unit)
    return "%.*g %s" % (digits, v, unit)


def _hyst_crossings(volts, dt, hyst):
    """带迟滞的均值过零时刻（线性插值到真穿越点）—— 用于周期估计。

    与 wave.crossings 的区别：那个只回**次数**（给频率用），这个回**时刻列表**
    （给周期/抖动用）。共用同一套迟滞思想，系数一处在 CROSS_HYST_FRAC。
    """
    v = volts - volts.mean()
    n = len(v)
    out, state, pending = [], 0, None
    for i in range(1, n):
        if v[i - 1] * v[i] <= 0 and v[i] != v[i - 1]:
            # 上一采样点到本点的线性过零时刻
            pending = (i - 1) * dt + dt * (-v[i - 1]) / (v[i] - v[i - 1])
        if v[i] > hyst:
            if state == -1 and pending is not None:
                out.append(pending)
                pending = None
            state = 1
        elif v[i] < -hyst:
            if state == 1 and pending is not None:
                out.append(pending)
                pending = None
            state = -1
    return out


def _max_flat_run(volts, target, band, eps):
    """贴 target 且相邻点几乎不变的**最长连续段**长 —— 削顶判据。

    两条注意（都是离线测试踩出来的）：
      1. 只数**波形内部**，首尾各留 5% 不参与 —— 斜坡/阶跃的端点天然贴轨且
         变化极小，不排除会把自己误报成削顶。
      2. 平台必须"进入后再退出"，不能用 `run=1` 兜底 —— 否则一串贴着轨、
         但在缓慢爬升的点会被算成平台。这里用 `started` 标记真正的入平点。
    """
    n = len(volts)
    lo, hi = int(n * 0.05), n - int(n * 0.05)
    best = run = 0
    for i in range(lo, hi):
        if abs(volts[i] - target) < band and (i == lo or abs(volts[i] - volts[i - 1]) < eps):
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


def _seg_rms(seg):
    if len(seg) == 0:
        return 0.0
    m = float(np.mean(seg))
    return float(np.sqrt(np.mean((seg - m) ** 2)))


def describe(volts, dt, scale_vdiv=None, warns=None):
    """波形形态判读 + 质量告警 → 结构化结论。

    参数
        volts       电压数组（V）
        dt          采样间隔（s）
        scale_vdiv  该通道**真实**垂直档位（V/div）。给了才能判断"撑开没有"；
                    **不可从波形反推** —— 那是循环论证（本项目真机踩过：反推得
                    0.02 V/div，真实是 1 V/div，差 50 倍）
        warns       上游已有告警（如通道被自动打开），并入结果

    返回 dict：
        shape            形态定性文本
        shape_code       dc/pulse/ramp_up/ramp_down/aperiodic/damped/growing/sustained/unknown
        freq_hz          过零法估频（不可信时为 None）
        period_s         周期
        cycles           屏上可见周期数
        crossings        过零数
        fill             垂直填充率（无档位时为 None）
        tolerance        一句人话总判：这份数据能不能用
        warnings         告警列表（每条含修法）
    """
    volts = np.asarray(volts, dtype=float)
    n = len(volts)
    out_warn = list(warns or [])
    res = {
        "n": n, "dt": float(dt), "span_s": float(dt * n),
        "shape": "unknown", "shape_code": "unknown",
        "freq_hz": None, "period_s": None, "cycles": None, "crossings": 0,
        "fill": None, "scale_vdiv": scale_vdiv,
        "tolerance": "", "warnings": out_warn,
        "is_clipped": False, "clip_run": 0,
    }
    if n < 4:
        res["tolerance"] = "采样点太少（%d 点），无法判读。" % n
        out_warn.append("采样点不足 4，请确认时基与采集长度。")
        return res

    vmin, vmax = float(volts.min()), float(volts.max())
    vpp = vmax - vmin
    vmean = float(volts.mean())
    window = dt * n

    # ── 垂直填充率：判断"撑开没有"（须用真实档位）────────────────────
    fill = None
    if scale_vdiv and scale_vdiv > 0:
        full = scale_vdiv * SCREEN_V_DIV
        fill = vpp / full
        res["fill"] = float(fill)
        res["div_pp"] = float(vpp / scale_vdiv)

    # 噪声底提前拦截：撑开不足 10%，任何频率/形态结论都是台阶造出来的
    if fill is not None and fill < NOISE_FILL:
        res["shape"] = "低幅 / 疑似噪声（形态与频率判读已抑制）"
        res["shape_code"] = "unknown"
        res["tolerance"] = (
            "读数不可信：Vpp %s 只占满屏 %.1f%%（%s/div，约 %.2f 格）。"
            "此电平下波形由噪声/量化台阶主导，任何频率或形态结论都无意义。"
            % (fmt_si(vpp, "V"), fill * 100, fmt_si(scale_vdiv, "V"), vpp / scale_vdiv))
        out_warn.append(
            "请调小 V/div（垂直放大）直到信号占几格，再重抓；"
            "若预期就是大信号，请检查探头与接线。")
        return res

    # ── 先算削顶平台（须在形态判定前，否则削顶正弦会被误判成"脉冲/方波"——
    #    离线测试实测踩到过：clip 后的正弦双峰特征与方波无法区分，只能靠
    #    "顶部是否被削平"来分辨）────────────────────────────────────
    clip_run = 0
    if vpp > 1e-3:
        band = max(vpp * CLIP_RAIL_BAND_FRAC, 1e-3)
        eps = max(vpp * CLIP_FLAT_EPS_FRAC, 1e-4)
        min_run = max(int(n * CLIP_MIN_RUN_FRAC), CLIP_MIN_RUN_ABS)
        clip_run = max(_max_flat_run(volts, vmax, band, eps), _max_flat_run(volts, vmin, band, eps))
        res["clip_run"] = int(clip_run)
        res["is_clipped"] = clip_run >= min_run

    # ── 过零与形态判定 ──────────────────────────────────────────────
    hyst = vpp / 2.0 * CROSS_HYST_FRAC
    crossings = _hyst_crossings(volts, dt, hyst)
    res["crossings"] = len(crossings)

    # 双峰（脉冲/方波）：多数点贴在上下轨
    rail = vpp * PULSE_RAIL_FRAC
    near_rail = int(np.sum((np.abs(volts - vmin) < rail) | (np.abs(volts - vmax) < rail)))
    is_pulse = (near_rail / n) > PULSE_NEAR_RAIL and vpp > 1e-3

    half_periods = []
    period = None

    if vpp < 1e-3:
        res["shape"], res["shape_code"] = "DC / 平坦（Vpp < 1 mV）", "dc"
    elif res["is_clipped"] and not is_pulse:
        # 削顶：**仅当它不是方波时**才这么判。
        # 方波天生就有平顶 —— 拿"有没有平台"去判方波必然误报（离线测试踩到过：
        # 干净方波被判成削顶）。所以先排除 is_pulse，剩下的才是"本该是圆滑波形
        # 却被削平"的真削顶。
        res["shape"] = "削顶（有 %d 点连续被削平）" % int(clip_run)
        res["shape_code"] = "clipped"
        out_warn.append(
            "波形存在 %d 点连续贴轨的平台，且整体不像方波 —— 疑似前端过载削顶，"
            "当前读数（尤其 Vpp/Vtop/Vbase）可能失真。请调大 V/div 或减小探头衰减后重抓。"
            % int(clip_run))
    elif is_pulse:
        duty = float(np.mean(volts > vmean) * 100)
        res["shape"] = "脉冲 / 方波（占空比约 %.0f%%）" % duty
        res["shape_code"] = "pulse"
        res["duty_pct"] = duty
    elif len(crossings) < 2:
        d = np.diff(volts[:min(50, n)])
        pos, neg = int(np.sum(d > 0)), int(np.sum(d < 0))
        if pos > len(d) * 0.7:
            res["shape"], res["shape_code"] = "上升斜坡 / 正斜率", "ramp_up"
        elif neg > len(d) * 0.7:
            res["shape"], res["shape_code"] = "下降斜坡 / 负斜率", "ramp_down"
        else:
            res["shape"], res["shape_code"] = "非周期 / 复杂波形", "aperiodic"
        out_warn.append("屏上不足一个完整周期，时基偏窄；请放宽时基以看到完整波形。")
    else:
        half_periods = [crossings[i + 1] - crossings[i] for i in range(len(crossings) - 1)]
        avg_hp = sum(half_periods) / len(half_periods)
        period = avg_hp * 2
        freq = 1.0 / period if period > 0 else None
        cycles = window / period if period > 0 else 0
        res["period_s"], res["freq_hz"], res["cycles"] = period, freq, cycles

        # 包络趋势：首尾三分之一的 RMS 比
        third = max(n // 3, 1)
        r_first, r_last = _seg_rms(volts[:third]), _seg_rms(volts[-third:])
        ratio = (r_last / r_first) if r_first > 0 else 1.0
        if ratio < 0.7:
            res["shape"] = "衰减振荡（RMS 幅值约衰减 %.0f%%）" % ((1 - ratio) * 100)
            res["shape_code"] = "damped"
        elif ratio > 1.4:
            res["shape"] = "增长振荡（RMS 幅值约增长 %.0f%%，含起振过程）" % ((ratio - 1) * 100)
            res["shape_code"] = "growing"
        else:
            res["shape"], res["shape_code"] = "持续振荡", "sustained"

    # ── 质量告警（每条带修法）──────────────────────────────────────
    if scale_vdiv and fill is not None and LOW_FILL > fill >= NOISE_FILL:
        out_warn.append(
            "低幅：Vpp %s 仅占满屏 %.0f%%（%s/div）。读数可用但偏噪，"
            "建议调小 V/div 让信号多占几格。" % (fmt_si(vpp, "V"), fill * 100, fmt_si(scale_vdiv, "V")))

    # 削顶告警已在形态判定阶段给出（削顶优先），此处不重复

    # 周期抖动：只在"有效幅度"的半周期上算，滤掉衰减信号尾部的噪声穿越
    if period and len(half_periods) >= 4:
        sig_thr = vpp * 0.15
        # 用过零时刻落在哪两个采样之间来判断该半周期是否为有效幅度
        idx = [int(round(c / dt)) for c in crossings]
        sig = []
        for i in range(len(half_periods)):
            a, b = idx[i], idx[i + 1]
            a, b = max(a, 0), min(b, n - 1)
            if b > a and float(np.max(np.abs(volts[a:b + 1] - vmean))) > sig_thr:
                sig.append(half_periods[i])
        if len(sig) >= 4:
            m = sum(sig) / len(sig)
            sd = (sum((x - m) ** 2 for x in sig) / len(sig)) ** 0.5
            cv = sd / m if m > 0 else 0
            res["jitter_cv"] = float(cv)
            if cv > JITTER_CV_WARN:
                out_warn.append(
                    "周期抖动 CV=%.0f%%（%d 个有效半周期）—— 可能非周期、调频或混叠。"
                    "请核对采样率与信号频率。" % (cv * 100, len(sig)))

    # DC 基线漂移：头尾各取**整数个周期**求均值。不这么算会把跨非整数周期
    # 的部分周期平均误判成漂移（如窗口从 +峰 起、到 −峰 止）。
    if period and period > 0 and dt > 0:
        cyc = int(round(period / dt))
        if cyc >= 4 and (window / period) >= 3 and 2 * cyc <= n:
            mh = float(np.mean(volts[:cyc]))
            mt = float(np.mean(volts[-cyc:]))
            if abs(mt - mh) > vpp * BASELINE_SHIFT_FRAC and vpp > 1e-3:
                out_warn.append(
                    "DC 基线从 %.4g V 漂到 %.4g V（差 %s）—— 捕获可能横跨一个瞬态或建立过程。"
                    % (mh, mt, fmt_si(mt - mh, "V")))

    # 突发：头/尾静默
    if n >= 10:
        seg = max(n // 5, 2)
        body = _seg_rms(volts[seg:-seg]) if n > 2 * seg else _seg_rms(volts)
        if body > 0:
            if _seg_rms(volts[:seg]) < body * BURST_QUIET_FRAC:
                out_warn.append("起段静默后转活跃 —— 突发/瞬态从捕获中途开始。请提前触发点或用预触发。")
            if _seg_rms(volts[-seg:]) < body * BURST_QUIET_FRAC:
                out_warn.append("末段转为静默 —— 突发/瞬态在捕获中途结束。请放宽时基或后移触发点。")

    # 周期不足
    if 0 < len(crossings) < 4:
        out_warn.append("不足 2 个完整周期，仪器自带频率测量可能回 9.9E37（无效哨兵）。请放宽时基。")

    # 左右边沿不在均值 → 起止于半周期中间
    edge_thr = max(vpp * 0.05, 4e-3)
    if abs(float(volts[0]) - vmean) > edge_thr:
        out_warn.append("左边沿 %s 不在均值 —— 波形从半周期中间开始，触发偏移或时基可能需调整。"
                        % fmt_si(float(volts[0]), "V"))
    if abs(float(volts[-1]) - vmean) > edge_thr:
        tip = ""
        if period:
            tip = " 修法：offset = N×%s − 6×scale（N 取整数）。" % fmt_si(period / 2, "s")
        out_warn.append("右边沿 %s 不在均值 —— 波形在半个周期处结束。%s"
                        % (fmt_si(float(volts[-1]), "V"), tip))

    # ── 一句人话总判 ────────────────────────────────────────────────
    if not out_warn:
        res["tolerance"] = "读数可信：形态清晰，未见质量告警。"
    elif fill is not None and fill < LOW_FILL:
        res["tolerance"] = "读数边缘可用：信号偏小、偏噪，建议放大档位后复测。"
    else:
        res["tolerance"] = "读数基本可用，但有 %d 条告警，下结论前请先逐条核对。" % len(out_warn)

    return res
