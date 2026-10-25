# -*- coding: utf-8 -*-
"""B 组工具：只读取数（3 个）。

    scope_capture      抓一段波形 → 摘要 + 落盘路径 + capture_id
    scope_analyze      对上一次抓取做频域/包络/量化体检
    scope_screenshot   截图 → 路径

**铁律：原始波形绝不进 content。** 只回 6–8 个统计标量 + 文件路径。
反例（同行）：把 1400 点浮点数组直接回给模型 = 上下文爆炸。

handle pattern：
    scope_capture 返回 capture_id，模型在下一步作为**普通参数**传回来。
    状态落磁盘（CSV 本来就要落），不藏进程内存 —— server 重启不丢。
"""
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import captures, conn, format as fmt, state as st
from ..guard import serialized

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=False,      # 抓波形每次结果不同，不幂等
    open_world_hint=True,
)


def register(mcp):

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_capture(
        source: Annotated[str, Field(
            description="通道：CHANnel1 / CHANnel2（也接受 CH1 / 1）",
        )] = "CHANnel1",
        mode: Annotated[str, Field(
            description=("取数模式：NORMal = 屏上波形（约 1000 点，快）；"
                         "RAW = 内存全深度（慢，但采到全部存储深度）"),
        )] = "NORMal",
    ) -> dict:
        """抓取一段波形，返回**统计摘要 + 数据文件路径 + capture_id**。

        原始采样点**不会**出现在返回值里 —— 它们落盘成 CSV，只把路径给你。
        需要细节分析就把 capture_id 传给 scope_analyze。

        返回的 capture_id 是后续 scope_analyze 的入参，形如 "20260924-134500-CHANnel1"。
        csv_path 是落盘位置（`records/<日期>-附件/` 下）。

        摘要含：点数 n、采样间隔 dt、时间跨度 span、vpp、均值、标准差、
        上升沿数、估频，以及每个量的 valid 标志。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        chan = st.norm_chan(source)
        if chan is None:
            return fmt.fail("通道名无法识别：%r" % source,
                            "只有两个通道，请用 CHANnel1 或 CHANnel2（也接受 CH1 / 1）。")

        mode = (mode or "NORMal").strip()
        if mode.lower() not in ("normal", "raw"):
            return fmt.fail("取数模式无法识别：%r" % mode,
                            "只能是 NORMal（屏上波形）或 RAW（内存全深度）。")

        try:
            from scope import wave as _wave
            t0, dt, volts = _wave.fetch(inst, chan, mode=mode, fmt="BYTE")
        except Exception as exc:
            return fmt.fail(
                "抓波形失败（%s: %s）" % (type(exc).__name__, exc),
                "常见原因：通道未开启、时基过大导致点数过多、或链路中断。"
                "先用 scope_state 确认该通道 DISPlay 为 1，再重试。")

        s = _wave.stat(volts, dt)
        cid, path = captures.save(chan, t0, dt, volts)

        # ⭐ 边沿数可信度自检 —— 取自「读数 → 可信的读数 → 结论」三级里的第二级。
        # 实测注意：CH1 档位 1 V/div、信号仅 80 mV（2.56 个量化台阶）时，
        # stat() 报出 rises=146、freq_est=10.4 MHz —— **146 个"上升沿"全部是
        # 量化噪声在阈值附近抖动**。若原样返回，模型会拿着一个假频率下结论。
        warn = []
        levels = len(set(round(float(v), 6) for v in volts[:]))
        if levels <= 4:
            warn.append(
                "边沿计数与频率不可信：波形只落在 %d 个离散电平上，"
                "是被 ADC 量化出的台阶，不是真实边沿。请缩小档位后重抓。" % levels)

        # ⭐ 形态判读：给"这是什么波 + 这读数能不能信"。
        # 用**真实档位**（不可反推 —— 见 _scale_for 的坑）。
        from scope import shape as _shape
        scale, _src = _scale_for(chan)
        shp = _shape.describe(volts, dt, scale_vdiv=scale, warns=warn)

        out = fmt.summary(
            {
                "vpp": s["vpp"], "mean": s["mean"], "std": s["std"],
                "vmin": s["vmin"], "vmax": s["vmax"],
                "freq_est": None if warn else s["freq_est"],
            },
            n=s["n"], dt=dt, span=s["span"],
            csv_path=path, capture_id=cid,
        )
        out.update({
            "source": chan, "mode": mode,
            "rises": s["rises"],
            "rises_valid": not warn,
            "distinct_levels": levels,
            "shape": shp["shape"],
            "shape_code": shp["shape_code"],
            "shape_freq_hz": shp["freq_hz"],
            "vertical_fill": shp["fill"],
            "is_clipped": shp["is_clipped"],
            "tolerance": shp["tolerance"],
            "warnings": shp["warnings"],
            "note": ("原始采样点已落盘，未进上下文。"
                     "把 capture_id 传给 scope_analyze 做频谱/包络/量化/形态分析。"),
        })
        return out

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_analyze(
        capture_id: Annotated[str, Field(
            description="scope_capture 返回的 capture_id，如 20260924-134500-CHANnel1",
        )],
        kind: Annotated[str, Field(
            description=("分析类型：spectrum = 频谱+自相关+主频；"
                         "envelope = 包络+峰值计数+周期抖动；"
                         "noise = 量化台阶/离散度/有效位数；"
                         "shape = 波形形态判读+质量告警（这是什么波、读数能不能信）；"
                         "all = 全部"),
        )] = "all",
    ) -> dict:
        """对**上一次抓取**的波形做本地分析（不重新碰仪器）。

        这是判读层入口：把"这信号干不干净"变成可判断的数。
        入参 capture_id 来自 scope_capture，数据从磁盘读 —— 所以
        **同一份数据可以反复用不同 kind 分析，不用重新抓**。

        - spectrum：主频、前几个谱峰、谱尖锐度（峰/底比）。真载波几十上百，
          宽带噪声在 10 上下。
        - envelope：|v| 整流平滑后的包络峰 —— 平均频率、周期抖动%、峰高离散%。
        - noise：量化台阶报告。注意 **true_sigma 扣掉量化噪声后的真实噪声**；
          若它接近 0，说明屏上"杂波"全部来自档位台阶，读数低于可分辨下限。
        - shape：形态归类（正弦/方波/斜坡/衰减振荡/削顶…）+ 质量告警
          （削顶、低幅、突发、基线漂移、周期抖动、周期不足），每条带修法。

        返回值中的 valid=false 表示该项算不出来（数据不足或信号特征不符），
        不是 0。
        """
        data = captures.load(capture_id)
        if data is None:
            return fmt.fail(
                "找不到抓取记录：%r" % capture_id,
                "capture_id 必须来自本次会话的 scope_capture 返回值。"
                "先调 scope_capture 抓一次波形。")

        t0, dt, volts = data
        kinds = (["spectrum", "envelope", "noise", "shape"]
                 if kind == "all" else [kind])
        bad = [k for k in kinds if k not in ("spectrum", "envelope", "noise", "shape")]
        if bad:
            return fmt.fail("分析类型无法识别：%s" % bad,
                            "只能是 spectrum / envelope / noise / shape / all 之一。")

        from scope import wave as _wave
        out = fmt.ok(capture_id=capture_id, ts=fmt.stamp(), points=len(volts))

        if "spectrum" in kinds:
            f0, a0, tops, _ = _wave.spectrum(volts, dt)
            top, med, ratio = _wave.flatness(volts, dt)
            period, corr = _wave.dominant_period(volts, dt)
            out["spectrum"] = {
                "f_dominant_hz": f0 if f0 == f0 else None,
                "amp": a0,
                "peaks": [{"f_hz": f, "amp": a} for f, a in tops[:6]],
                "peak_to_floor": ratio if ratio == ratio else None,
                "autocorr_period_s": period if period == period else None,
                "autocorr_coef": corr if corr == corr else None,
                "hint": ("peak_to_floor 高（几十以上）= 干净谱线；"
                         "10 上下 = 宽带底噪主导。"),
            }

        if "envelope" in kinds:
            env = _wave.envelope(volts, win=11)
            peaks, f_avg, jitter, amp_spread = _wave.count_peaks(env, dt)
            out["envelope"] = {
                "peak_count": len(peaks),
                "f_avg_hz": f_avg if f_avg == f_avg else None,
                "period_jitter_pct": jitter if jitter == jitter else None,
                "peak_height_spread_pct": amp_spread if amp_spread == amp_spread else None,
                "hint": "jitter / spread 大 = 包络不稳，回头看触发与信源。",
            }

        if "noise" in kinds:
            chan = chan_of(capture_id)
            scale, scale_src = _scale_for(chan)
            if scale is None:
                out["noise"] = {
                    "verdict": "无法判断：读不到当前档位，量化报告需要档位才能算 LSB。",
                    "scale_source": scale_src}
            else:
                rep = _wave.quant_report(volts, scale)
                out["noise"] = {
                    k: (v if not isinstance(v, float) or v == v else None)
                    for k, v in rep.items() if k != "occupancy"
                }
                out["noise"]["scale_vdiv"] = scale
                out["noise"]["scale_source"] = scale_src
                out["noise"]["verdict"] = _quant_verdict(rep)

        if "shape" in kinds:
            from scope import shape as _shape
            chan = chan_of(capture_id)
            scale, scale_src = _scale_for(chan)
            shp = _shape.describe(volts, dt, scale_vdiv=scale)
            out["shape"] = {
                "verdict": shp["shape"],
                "shape_code": shp["shape_code"],
                "freq_hz_zero_cross": shp["freq_hz"],
                "period_s": shp["period_s"],
                "cycles_visible": shp["cycles"],
                "zero_crossings": shp["crossings"],
                "vertical_fill": shp["fill"],
                "is_clipped": shp["is_clipped"],
                "tolerance": shp["tolerance"],
                "warnings": shp["warnings"],
                "scale_source": scale_src,
            }

        return out

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_screenshot(
        tag: Annotated[str, Field(
            description="文件名备注，便于日后检索，如 before_fix / ch2_noise",
        )] = "",
    ) -> dict:
        """截取示波器屏幕，返回 PNG 文件路径。

        ⚠️ **耗时 30–60 秒**（BMP 约 1.15 MB，走 VXI-11 传输慢），
        且**返回的是路径不是图片数据** —— 本工具不把图像塞进上下文。

        仅用于**人眼要看的场合**：菜单状态、光标位置、显示渲染。
        读数值请用 scope_measure，读波形形状请用 scope_capture —— 那两个快得多。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)
        try:
            from scope import link as _link
            path = _link.screenshot(inst, tag=tag)
        except Exception as exc:
            return fmt.fail(
                "截图失败（%s: %s）" % (type(exc).__name__, exc),
                "截图走 :DISPlay:DATA? 一条只读命令，慢是正常的。"
                "若超时，检查链路是否稳定后重试。")
        return fmt.ok(ts=fmt.stamp(), png_path=path, tag=tag,
                      note="仅一条只读命令 :DISPlay:DATA?，未改动任何设置。")

    return mcp


def chan_of(capture_id):
    """从 capture_id 里解析出通道 —— id 形如 20260924-134500-CHANnel2。"""
    parts = str(capture_id or "").rsplit("-", 1)
    return parts[-1] if len(parts) == 2 else "CHANnel1"


def _scale_for(chan):
    """取该通道的**真实档位** → (V/div, 来源说明)。

    ⚠️ 这里踩过一个坑，值得记下来：
        起初是拿波形自己反推档位（vpp/5 再吸附到 1-2-5 序列）。
        真机上立刻出错：CH1 实际档位 1 V/div，反推得到 0.02 V/div —— **差 50 倍**，
        于是 quant_report 算出的 LSB 也是错的，进而 `true_sigma_lsb` 给出
        完全误导的结论。

        量化报告的全部意义就是回答"读数是不是被档位伪造的"，
        所以它**必须用仪器的真实档位**，不能从波形反推 —— 那是循环论证。
        档位是实时可查的（A 级只读命令），没有理由去猜。
    """
    try:
        inst = conn.instrument()
        raw = conn.q(inst, ":%s:SCALe?" % chan)
        if raw:
            return float(raw), "实时查询 %s:SCALe?" % chan
    except Exception as exc:
        pass
    return None, "查询失败"


def _quant_verdict(rep):
    """量化报告 → 一句人话结论。

    这是判读层的价值所在：不是把一堆数丢给模型，
    而是**先说清读数可不可信**。

    两级判据（按严重程度）：
      ① top3_share 接近 1 且 levels 只有个位数 → 信号被量化到只剩几级，
         读数严重不可信（真机实例：0V/0.04V/0.08V 三级，占 100%）
      ② levels 少或 span_lsb 很小 → 信号幅度相对档位太小，应缩小档位
    """
    if not rep:
        return "无法判断"
    levels = rep.get("levels") or 0
    top3 = rep.get("top3_share") or 0.0
    span = rep.get("span_lsb") or 0.0

    if levels <= 4 and top3 >= 0.95:
        return ("读数不可信：全部采样只落在 %d 个离散电平上（占 %.0f%%）。"
                "信号幅度相对当前档位太小，被 ADC 量化成了几级台阶 —— "
                "此时波形形状与频率读数都不可信。**应先缩小档位"
                "（把波形放大到屏上 5 格左右）再重新抓。**"
                % (levels, top3 * 100))
    if span < 16:
        return ("读数边缘：波形只占 %.1f 个量化台阶（理想应 ≥ 50）。"
                "档位偏大，建议缩小档位后重抓。" % span)
    if levels < 8:
        return "读数可用但有量化痕迹：%d 个电平，占比 %.0f%%。" % (levels, top3 * 100)
    return "读数正常：%d 个量化电平，分布无明显台阶堆积。" % levels
