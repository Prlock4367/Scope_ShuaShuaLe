# -*- coding: utf-8 -*-
"""波形获取与本地分析 —— 抓一次，然后在这份数据上把该算的都算出来。

设计原则：**仪器自带的 :MEASure:FREQuency? 不可尽信**。
它数的是边沿，遇到「低频包络 × 高频载波」这类调幅信号，读数会随采样率漂移
（实测同一路信号在 29 kHz / 4 kHz / 499 Hz 之间乱跳）。所以凡是要下结论的地方，
一律把波形抓回来本地算，并且用互相独立的方法交叉验证。

只依赖 numpy，不引 matplotlib —— 出图在 cli/ 那边，库留给未来 MCP 直接用。
"""
import numpy as np

from . import link

INVALID = link.INVALID


def fetch(inst, chan="CHANnel2", mode="NORMal", fmt="BYTE"):
    """读一屏波形 → (t0, dt, volts)。

    换算公式来自实机校准：
        V = (raw - yorigin - yreference) * yincrement
        t = (i - xreference) * xincrement + xorigin
    注意 PREamble 有 10 个字段，漏掉 <count> 会导致后面全部错位。
    """
    link.send(inst, ":WAVeform:SOURce %s" % chan)
    link.send(inst, ":WAVeform:MODE %s" % mode)
    link.send(inst, ":WAVeform:FORMat %s" % fmt)
    pre = [float(x) for x in link.q(inst, ":WAVeform:PREamble?").split(",")]
    if len(pre) != 10:
        raise RuntimeError("PREamble 字段数应为 10，实得 %d" % len(pre))
    xinc, xorg, xref, yinc, yorg, yref = pre[4], pre[5], pre[6], pre[7], pre[8], pre[9]
    raw = inst.query_binary_values(":WAVeform:DATA?", datatype="B", container=bytes)
    volts = np.array([(b - yorg - yref) * yinc for b in raw], dtype=float)
    return (0.0 - xref) * xinc + xorg, xinc, volts


def screen_data_broken(volts):
    """AUTO 深存储（5.6M）下屏幕数据口会返回「逐点 0/255 交替」的坏帧。

    判据：只有两个电平，且几乎每个相邻点都在跳。正常波形（哪怕方波）都会
    成段保持同一电平，不会逐点交替 —— 这个判据不看幅度，换档也不会误判。
    """
    n = len(volts)
    if n < 32:
        return False
    levels = set(round(float(v), 4) for v in volts)
    if len(levels) != 2:
        return False
    flips = sum(1 for i in range(1, n) if volts[i] != volts[i - 1])
    return flips >= n - 3


def crossings(volts, hyst_frac=0.1):
    """用中值阈值 + 回差数上升沿 → (次数, 阈值, 回差)。回差是为了抗噪。"""
    lo, hi = float(volts.min()), float(volts.max())
    mid = (lo + hi) / 2.0
    hyst = (hi - lo) * hyst_frac
    rises, high = 0, False
    for v in volts:
        if v > mid + hyst:
            high = True
        elif v < mid - hyst and high:
            high = False
            rises += 1
    return rises, mid, hyst


def rise_intervals(volts, hyst_frac=0.1):
    """带回差扫描，记录相邻两个上升沿之间的采样点间隔 → (次数, 间隔列表, 阈值, 回差)。

    与 crossings() 的区别：那个只回**次数**；这个回**间隔**，
    频率才能用「间隔中位数」算，而不是「次数 ÷ 窗口总长」。
    """
    lo, hi = float(volts.min()), float(volts.max())
    mid = (lo + hi) / 2.0
    hyst = (hi - lo) * hyst_frac
    iv, high, last = [], False, None
    for i, v in enumerate(volts):
        if v > mid + hyst:
            if not high:
                if last is not None:
                    iv.append(i - last)
                last = i
            high = True
        elif v < mid - hyst and high:
            high = False
    return len(iv) + (1 if last is not None else 0), iv, mid, hyst


def stat(volts, dt):
    """一次波形的基本统计。

    freq_est 三级兜底（2026-09-24 重写）：
    1. **上升沿间隔中位数**（首选）—— 抗暂态漂移、抗个别缺角边沿，
       对干净方波也顺带修掉旧法「最后一个不完整周期」的系统偏差
       （6 个边沿 ÷ 6.27 ms = 957 Hz → 中位间隔直接给真值 1000 Hz）；
    2. **自相关主周期**（dominant_period）—— 极窄脉冲/单一隆起这种
       数不出第二个边沿的形状（如包络级节点，首脉冲独高、后面全塌）；
    3. **次数 ÷ 窗口总长**（旧法）—— 前两级都不行时的最后兜底。
    freq_src 记录用的是哪一级：median / autocorr / window / none。
    """
    n = len(volts)
    span = dt * n
    rises, mid, _ = crossings(volts)

    freq, src = float("nan"), "none"
    _, iv, _, _ = rise_intervals(volts)
    if len(iv) >= 2:
        freq = 1.0 / (float(np.median(iv)) * dt)
        src = "median"
    if not np.isfinite(freq):
        p, _ = dominant_period(volts, dt)
        if np.isfinite(p) and p > 0:
            freq = 1.0 / p
            src = "autocorr"
    if not np.isfinite(freq) and rises and span > 0:
        freq = rises / span
        src = "window"

    return {
        "n": n, "dt": dt, "span": span,
        "vmin": float(volts.min()), "vmax": float(volts.max()),
        "vpp": float(volts.max() - volts.min()),
        "mean": float(volts.mean()), "std": float(volts.std()),
        "rms_ac": float(np.sqrt(((volts - volts.mean()) ** 2).mean())),
        "rises": rises, "thr": mid,
        "freq_est": freq, "freq_src": src,
    }


# ── 频谱与自相关 ─────────────────────────────────────────────────────
def pick_peaks(spec, ntop=6, sep=3):
    """挑**互相分开**的谱峰。

    不能直接取振幅最大的 n 个频点 —— 一个宽峰会占掉相邻好几个频点，
    打出来就是"前 6 峰全是 1102 Hz"这种没用的表。
    sep 指定峰之间至少隔几个频点。
    """
    idx = [i for i in range(1, len(spec) - 1)
           if spec[i] > spec[i - 1] and spec[i] >= spec[i + 1]]
    idx.sort(key=lambda i: -spec[i])
    picked = []
    for i in idx:
        if all(abs(i - j) >= sep for j in picked):
            picked.append(i)
        if len(picked) >= ntop:
            break
    return picked or [int(np.argmax(spec))]


def spectrum(x, dt, frac=8, ntop=6):
    """频谱。只看 fs/frac 以下的低频段（避免混叠区噪声主导）。

    返回 (全局主频, 主峰幅度, [(f, amp), ...], (freqs, spec))
    """
    m = len(x)
    nfft = 1 << int(np.ceil(np.log2(max(64, m * frac))))
    win = np.hanning(m)
    a = (x - x.mean()) * win
    spec = np.abs(np.fft.rfft(a, nfft)) * 2.0 / win.sum()
    freq = np.fft.rfftfreq(nfft, dt)
    spec[0] = 0.0
    lim = max(2, nfft // frac)
    seg = spec[:lim]
    top = int(np.argmax(seg))
    tops = [(float(freq[i]), float(spec[i])) for i in pick_peaks(seg, ntop)]
    return float(freq[top]), float(spec[top]), tops, (freq, spec)


def flatness(x, dt, frac=8):
    """谱的"尖锐度" —— 用来区分「一条干净谱线」和「一片宽带底噪」。

    返回 (最大谱峰, 谱中位数, 峰/底比)。真载波能达到几十上百；宽带噪声在 10 上下。
    分析频段与 spectrum() 保持一致。
    """
    _, _, _, (freq, spec) = spectrum(x, dt, frac, ntop=1)
    nfft = 2 * (len(spec) - 1)
    lim = max(2, nfft // frac)
    band = spec[1:lim]
    if not len(band):
        return float("nan"), float("nan"), float("nan")
    med = float(np.median(band))
    top = float(band.max())
    return top, med, (top / med if med > 0 else float("inf"))


def autocorr(x):
    """归一化自相关（只留非负延迟）。系数接近 1 = 周期稳定。"""
    a = np.asarray(x, dtype=float)
    a = a - a.mean()
    if not a.any():
        return np.array([1.0])
    ac = np.correlate(a, a, "full")[len(a) - 1:]
    return ac / ac[0]


def dominant_period(x, dt, min_corr=0.25, skip=3):
    """自相关里找第一个显著峰 → (周期 s, 相关系数)。找不到返回 (nan, nan)。"""
    ac = autocorr(x)
    n = len(ac)
    for i in range(skip, n - 3):
        if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1] and ac[i] > min_corr:
            return i * dt, float(ac[i])
    return float("nan"), float("nan")


def corr_at(x, dt, period, min_corr=0.25):
    """指定周期处的自相关系数 —— 用于定向验证「有没有这个周期成分」。"""
    ac = autocorr(x)
    lag = int(round(period / dt))
    if 0 < lag < len(ac):
        return float(ac[lag])
    return float("nan")


# ── 包络 ─────────────────────────────────────────────────────────────
def smooth(x, w):
    return np.convolve(x, np.ones(w) / w, mode="same") if w >= 2 else np.asarray(x)


def envelope(volts, win=11, center=None):
    """|v| 整流 + 平滑 → 包络（已去均值）。用于「包络 × 载波」这种调幅信号。"""
    base = volts - (np.median(volts) if center is None else center)
    return smooth(np.abs(base), win)


def count_peaks(env, dt, gap_frac=0.5, ref_freq=None):
    """客观数包络峰 → (峰索引, 平均频率, 周期抖动%, 峰高离散%)。"""
    lo, hi = np.percentile(env, 15), float(env.max())
    thr = (lo + hi) / 2.0
    gap = max(3, int(gap_frac / ref_freq / dt)) if ref_freq and ref_freq > 0 else 10
    peaks = []
    for i in range(2, len(env) - 2):
        if env[i] > env[i - 1] and env[i] >= env[i + 1] and env[i] > thr:
            if not peaks or i - peaks[-1] >= gap:
                peaks.append(i)
    if len(peaks) < 2:
        return peaks, float("nan"), float("nan"), float("nan")
    gaps = np.diff(peaks) * dt
    pk = np.array([env[i] for i in peaks])
    return (peaks, float(1.0 / gaps.mean()),
            float(gaps.std() / gaps.mean() * 100),
            float(pk.std() / pk.mean() * 100))


# ── 量化台阶：判断读数是不是被档位伪造的 ─────────────────────────────
def quant_report(volts, scale_vdiv):
    """当前档位下，这份读数有多少是"真实"、多少是量化台阶造出来的。

    原理：量化噪声本身的 σ = LSB/sqrt(12)。观测 σ 扣掉这一项才是真实噪声。
    若扣完 ≤ 0，说明屏幕上看到的"杂波"全部来自台阶，读数低于可分辨下限。
    """
    lsb_v = link.lsb(scale_vdiv)
    levels, counts = np.unique(np.round(volts / lsb_v, 4), return_counts=True)
    sigma_lsb = float(volts.std() / lsb_v)
    quant_var = 1.0 / 12.0                       # (LSB 单位)
    true_var = sigma_lsb ** 2 - quant_var
    top3 = float(np.sort(counts)[::-1][:3].sum() / counts.sum())
    return {
        "lsb_v": lsb_v,
        "levels": int(len(levels)),
        "span_lsb": float((volts.max() - volts.min()) / lsb_v),
        "sigma_lsb": sigma_lsb,
        "sigma_v": float(volts.std()),
        "true_sigma_lsb": (sigma_lsb ** 2 - quant_var) ** 0.5 if true_var > 0 else 0.0,
        "true_sigma_v": (max(true_var, 0.0)) ** 0.5 * lsb_v,
        "top3_share": top3,
        "occupancy": list(zip([float(x) for x in levels],
                              [int(c) for c in counts])),
    }


# ── 档位/时基辅助 ────────────────────────────────────────────────────
def nearest_125(x):
    """吸附到示波器档位序列（1-2-5 步进）—— 档位只有这些值。"""
    import math
    if not (x > 0) or x != x:
        return 1.0
    cands = [m * 10 ** e for e in range(-7, 4) for m in (1, 2, 5)]
    return min(cands, key=lambda v: abs(math.log(v / x)))


def suggest_timebase(period, div_total=14.0, cycles=2.5):
    """水平 14 格（实测：1 µs/div × 14 = 14 µs 窗口）→ 目标屏上 2~3 个周期。"""
    if not (period > 0) or period != period:
        return 1e-3
    return nearest_125(period * cycles / div_total)
