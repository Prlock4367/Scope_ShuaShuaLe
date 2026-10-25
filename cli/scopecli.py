# -*- coding: utf-8 -*-
"""示波器统一命令行入口 —— 所有活儿都从这个命令走。

用法:
    VENV=/path/to/python        # 换成你自己环境的解释器，或直接用 python

    $VENV ./scopecli.py check            # 连通性 + 错误队列
    $VENV ./scopecli.py state            # 状态 + 测量项（只读）
    $VENV ./scopecli.py read --csv       # 全量只读诊断（状态+测量+波形）
    $VENV ./scopecli.py station --tag 站名 # 测站记录：冻结帧一次落盘（截图+状态+波形CSV）
    $VENV ./scopecli.py rec --tag 备注    # 连续轮询记录（纯只读）
    $VENV ./scopecli.py shot --tag 备注   # 截图
    $VENV ./scopecli.py noise --chan CHANnel2 -n 10
    $VENV ./scopecli.py spec  --time 1e-3
    $VENV ./scopecli.py env   --time 1e-2          # 包络分析 + 出图
    $VENV ./scopecli.py wave  --chan CHANnel2      # 多时基对照图
    $VENV ./scopecli.py probe                      # 档位/时基扫描（改设置）
    $VENV ./scopecli.py fit --apply --shot         # 自动布防（改设置）
    $VENV ./scopecli.py offset --chan CHANnel2 --value 3.0   # 垂直偏移（改设置）

分级（详见 scope/scpi.py）
    check / state / read / station / shot / noise / rec   只读，随时可跑
    spec / env / wave / probe / fit       会改设置，跑完自动还原（--keep 则不还原）
    offset                                会改设置且不还原（挪波形位置就是要它留在那）

新需求 → 加一个子命令，不要再新开脚本。
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scope import link, scpi, wave            # noqa: E402

CHANS = ("CHANnel1", "CHANnel2")

# ── 状态项（A 级只读查询）────────────────────────────────────────────
STATE = [
    ("*IDN?",                    "仪器身份"),
    (":SYSTem:ERRor?",           "错误队列"),
    (":CHANnel1:DISPlay?",       "CH1 显示"),
    (":CHANnel1:SCALe?",         "CH1 档位"),
    (":CHANnel1:OFFSet?",        "CH1 偏移"),
    (":CHANnel1:COUPling?",      "CH1 耦合"),
    (":CHANnel1:PROBe?",         "CH1 探头比"),
    (":CHANnel1:BWLimit?",       "CH1 带宽限制"),
    (":CHANnel2:DISPlay?",       "CH2 显示"),
    (":CHANnel2:SCALe?",         "CH2 档位"),
    (":CHANnel2:OFFSet?",        "CH2 偏移"),
    (":CHANnel2:COUPling?",      "CH2 耦合"),
    (":CHANnel2:PROBe?",         "CH2 探头比"),
    (":CHANnel2:BWLimit?",       "CH2 带宽限制"),
    (":TIMebase:MAIN:SCALe?",    "主时基"),
    (":TIMebase:MAIN:OFFSet?",   "时基偏移"),
    (":TIMebase:MODE?",          "时基模式"),
    (":TRIGger:STATus?",         "触发状态"),
    (":TRIGger:MODE?",           "触发类型"),
    (":TRIGger:SWEep?",          "触发扫描"),
    (":TRIGger:COUPling?",       "触发耦合"),
    (":TRIGger:EDGe:SOURce?",    "边沿触发源"),
    (":TRIGger:EDGe:SLOPe?",     "边沿斜率"),
    (":TRIGger:EDGe:LEVel?",     "边沿触发电平"),
]

# 测量项：key, 中文名, SCPI 命令, 单位, 小数位
MEAS = [
    ("freq",   "频率",     ":MEASure:FREQuency?", "Hz", 3),
    ("period", "周期",     ":MEASure:PERiod?",    "s",  9),
    ("vpp",    "峰峰值",   ":MEASure:VPP?",       "V",  4),
    ("vmax",   "最大值",   ":MEASure:VMAX?",      "V",  4),
    ("vmin",   "最小值",   ":MEASure:VMIN?",      "V",  4),
    ("vtop",   "高电平",   ":MEASure:VTOP?",      "V",  4),
    ("vbase",  "低电平",   ":MEASure:VBASe?",     "V",  4),
    ("vavg",   "平均值",   ":MEASure:VAVG?",      "V",  4),
    ("vrms",   "有效值",   ":MEASure:VRMS?",      "V",  4),
    ("vamp",   "幅度",     ":MEASure:VAMP?",      "V",  4),
    ("rtime",  "上升时间", ":MEASure:RTIMe?",     "s",  9),
    ("ftime",  "下降时间", ":MEASure:FTIMe?",     "s",  9),
    ("pduty",  "正占空比", ":MEASure:PDUTy?",     "%",  2),
    ("pwid",   "正脉宽",   ":MEASure:PWIDth?",    "s",  9),
]

DEFAULT_TIMES = (1e-5, 1e-4, 1e-3, 1e-2, 5e-2, 2e-1)


# ── 小工具 ───────────────────────────────────────────────────────────
def fmt(x, digits=4, width=14):
    if x is None or x != x or abs(x) >= link.INVALID:
        return "%*s" % (width, "——")
    return "%*.{}f".format(digits) % (width, x)


def plot_setup():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
    plt.rcParams["axes.unicode_minus"] = False
    return plt


def out_png(name):
    return os.path.join(link.record_dir(), "%s_%s.png" % (name, link.stamp()))


# ── 子命令 ───────────────────────────────────────────────────────────
def cmd_check(inst, args):
    """连通性 + 错误队列。"""
    bad = 0
    for cmd, label in STATE[:1] + STATE[1:2] + [
            (":CHANnel1:DISPlay?", "CH1 显示"), (":CHANnel1:SCALe?", "CH1 档位"),
            (":CHANnel2:DISPlay?", "CH2 显示"), (":CHANnel2:SCALe?", "CH2 档位"),
            (":TIMebase:MAIN:SCALe?", "主时基"), (":TRIGger:STATus?", "触发状态")]:
        t = time.time()
        try:
            r = link.q(inst, cmd)
            print("  [%5.0fms] %-9s %-24s = %s" % ((time.time() - t) * 1000, label, cmd, r))
            if cmd == ":SYSTem:ERRor?" and not r.startswith("0,"):
                bad += 1
        except Exception as exc:
            print("  [  --- ] %-9s %-24s ! %s" % (label, cmd, type(exc).__name__))
            bad += 1
    print("\n%s" % ("✓ 连接正常，仪器状态干净" if bad == 0 else "⚠ 有 %d 项异常" % bad))
    return 0 if bad == 0 else 1


def cmd_state(inst, args):
    """状态 + 测量项，只读。"""
    print("=" * 76)
    print("一、当前仪器状态（只读）")
    print("=" * 76)
    state = {}
    for cmd, label in STATE:
        t = time.time()
        try:
            r = link.q(inst, cmd)
        except Exception as exc:
            r = "! %s" % type(exc).__name__
        state[label] = r
        ms = "  [%4.0fms]" % ((time.time() - t) * 1000) if not args.quiet else ""
        print("%s  %-9s %-24s = %s" % (ms, label, cmd, r))

    st = state.get("触发状态", "")
    if st:
        print("\n  触发状态解读：%s" % link.trig_status_cn(st))

    print()
    print("=" * 76)
    print("二、测量项（即时查询；—— 表示该通道读不到）")
    print("=" * 76)
    print("  %-8s %-20s" % ("项目", "命令") + "".join("  %-16s" % c for c in CHANS))
    for key, label, cmd, unit, digits in MEAS:
        row = []
        for ch in CHANS:
            try:
                raw = link.q(inst, "%s %s" % (cmd, ch))
                val = float(raw)
                val = float("nan") if abs(val) >= link.INVALID else val
            except scpi.Denied:
                val = float("nan")
            except Exception:
                val = float("nan")
            row.append(fmt(val, digits))
        print("  %-8s %-20s" % (label, cmd) + "".join("  %-16s" % v for v in row))

    print("\n  档位对照（1 个量化台阶值多少伏，见 link.lsb）：")
    for ch in CHANS:
        try:
            sc = float(link.q(inst, ":%s:SCALe?" % ch))
            print("    %-9s %6.4g V/div → 1 LSB = %.4g V"
                  % (ch, sc, link.lsb(sc)))
        except Exception:
            pass
    return 0


def cmd_read(inst, args):
    """全量只读诊断：状态 + 测量 + 波形 + 本地统计。"""
    rc = cmd_state(inst, args)
    print()
    print("=" * 76)
    print("三、波形回传 + 本地统计")
    print("=" * 76)
    for ch in CHANS:
        try:
            t0, dt, v = wave.fetch(inst, ch)
            s = wave.stat(v, dt)
            print("  %s: %d 点, dt=%.3g s, 跨度=%.6g s" % (ch, s["n"], s["dt"], s["span"]))
            print("        本地统计 min=%+.4f V max=%+.4f V Vpp=%.4f V"
                  % (s["vmin"], s["vmax"], s["vpp"]))
            print("        中值阈值 %.4f V 上穿越 %d 次 → 估频 %.4g Hz"
                  % (s["thr"], s["rises"], s["freq_est"]))
            if args.csv:
                # 数据文件落记录目录，不落在代码目录里 —— 代码目录只放代码
                p = os.path.join(link.record_dir(),
                                 "wave_%s_%s.csv" % (ch, link.stamp()))
                link.write_csv(p, t0, dt, v)
                print("        → %s" % p)
        except Exception as exc:
            print("  %s: 读取失败 %s: %s" % (ch, type(exc).__name__, exc))
    print("\n错误队列复查: %s" % link.q(inst, ":SYSTem:ERRor?"))
    return rc


def cmd_shot(inst, args):
    """截图（只读，约 3-5 s，2026-09-30 提速后）。"""
    print("  截图中，VXI-11 传 1.15 MB ...")
    t = time.time()
    p = link.screenshot(inst, tag=args.tag)
    print("  完成，耗时 %.0f s → %s" % (time.time() - t, p))
    print("  ⚠ 截图只代表按下这一刻，不能拿它当仪器现状")
    return 0


def cmd_station(inst, args):
    """测站记录（只读）：把当前冻结帧一次落盘 —— 截图 + 双通道波形 CSV。

    流程约定（2026-09-30 定）：用户在面板按 STOP 冻结目标画面 → 跑本命令（约 3-4 秒）
    → 用户按 RUN 挪下一站。本命令不 STOP 不 RUN、不改任何设置；仪器未冻结则拒跑。
    2026-10-13 用户定：不再查状态/测量项 —— :MEASure:XXX? 会"启用"对应测量项、
    顶掉屏幕下方测量显示（编程手册 2-138/2-148），且逐项往返慢；波形用 NORM 屏幕模式最快。
    """
    st = link.q(inst, ":TRIGger:STATus?").strip()
    if st != "STOP":
        print("✗ 仪器处于 %s（未冻结），已中止，什么都没存。" % st)
        print("  先在面板按 STOP 冻结画面，再跑 station。")
        return 1
    print("✓ 冻结确认（STOP），开始记录测站：%s" % (args.tag or "(未命名)"))

    # ponytail: 与 grab 同规 —— 一次记录收进专属子文件夹，不在当日-附件里平铺（2026-10-22 用户定）
    ts = link.stamp().split("-")[1]
    outdir = os.path.join(link.record_dir(),
                          "测站-%s-%s" % (args.tag, ts) if args.tag else "测站-%s" % ts)
    os.makedirs(outdir, exist_ok=True)

    files = []
    if args.fast:
        print("\n[快采] 跳过截图，只记波形")
    else:
        print("\n[1/2] 截图 ...")
        t = time.time()
        p = link.screenshot(inst, outdir=outdir, tag=args.tag)
        files.append(p)
        print("      %.0f s → %s" % (time.time() - t, p))

    print("[2/2] 双通道屏幕波形 → CSV ...")
    for ch in CHANS:
        try:
            t0, dt, v = wave.fetch(inst, ch)
            s = wave.stat(v, dt)
            name = "wave_%s_%s_%s.csv" % (ch, args.tag, link.stamp()) if args.tag \
                else "wave_%s_%s.csv" % (ch, link.stamp())
            p = os.path.join(outdir, name)
            link.write_csv(p, t0, dt, v)
            files.append(p)
            print("      %s: %d 点 dt=%.3gs Vpp=%.3f V 本地估频=%.4g Hz\n        → %s"
                  % (ch, s["n"], s["dt"], s["vpp"], s["freq_est"], p))
        except Exception as exc:
            print("      %s: 读取失败 %s: %s" % (ch, type(exc).__name__, exc))

    print("\n错误队列复查: %s" % link.q(inst, ":SYSTem:ERRor?"))
    print("\n✓ 本测站共 %d 个文件：" % len(files))
    for p in files:
        print("    %s" % p)
    print("\n下一步：面板按 RUN → 探头挪下一站 → 冻结 → 再跑 station。")
    return 0


def cmd_noise(inst, args):
    """通道噪声体检：量化台阶 / 连续采集离散度 / 频谱 + 自相关。"""
    ch = args.chan
    print("=" * 74)
    print("一、当前读数条件（决定这份数据能不能用）")
    print("=" * 74)
    scale = float(link.q(inst, ":%s:SCALe?" % ch))
    print("  档位            : %.4g V/div" % scale)
    print("  1 LSB 电压      : %.4g V   (%.1f 格 × 档位 ÷ 256)"
          % (link.lsb(scale), link.DIV_VERT))
    for cmd, label in [(":%s:PROBe?" % ch, "探头比"), (":%s:COUPling?" % ch, "耦合"),
                       (":%s:BWLimit?" % ch, "带宽限制")]:
        print("  %-14s: %s" % (label, link.q(inst, cmd)))
    tb = float(link.q(inst, ":TIMebase:MAIN:SCALe?"))
    print("  主时基          : %.4g s/div   → 屏宽 %.4g s" % (tb, tb * 14))
    print("  触发电平        : %.4g V  (源 %s)"
          % (float(link.q(inst, ":TRIGger:EDGe:LEVel?")),
             link.q(inst, ":TRIGger:EDGe:SOURce?")))

    print()
    print("=" * 74)
    print("二、连续 %d 次采集 —— 真实纹波应当每次几乎一样" % args.n)
    print("=" * 74)
    print("  %-4s %-7s %10s %10s %10s %10s %10s %8s"
          % ("次", "点数", "min/V", "max/V", "Vpp/V", "均值/V", "σ/V", "上穿"))
    rows = []
    for i in range(args.n):
        _, dt, v = wave.fetch(inst, ch)
        s = wave.stat(v, dt)
        rows.append(s)
        print("  %-4d %-7d %10.4f %10.4f %10.4f %10.4f %10.4f %8d"
              % (i + 1, s["n"], s["vmin"], s["vmax"], s["vpp"], s["mean"],
                 s["std"], s["rises"]))
        if args.gap:
            time.sleep(args.gap)

    vpps = [r["vpp"] for r in rows]
    riss = [r["rises"] for r in rows]
    print()
    print("  【离散程度】Vpp 在 %.4f ~ %.4f V 之间跳（极差 %.4f V，为均值的 %.0f%%）"
          % (min(vpps), max(vpps), max(vpps) - min(vpps),
             100 * (max(vpps) - min(vpps)) / max(sum(vpps) / len(vpps), 1e-9)))
    print("  【上穿次数】%d ~ %d 次（若为真纹波，这个数应当稳定）"
          % (min(riss), max(riss)))

    _, dt, v = wave.fetch(inst, ch)
    qr = wave.quant_report(v, scale)
    print("  【量化对照】1 LSB = %.4g V；本次 Vpp %.4f V ≈ %.1f 个台阶"
          % (qr["lsb_v"], v.max() - v.min(), qr["span_lsb"]))
    print("              数据落在 %d 个台阶上，其中 %.0f%% 挤在最常见的 3 个台阶里"
          % (qr["levels"], qr["top3_share"] * 100))
    print("              σ = %.2f LSB，扣掉量化本身的 0.289 LSB → 真实噪声 σ ≈ %.2f LSB = %.4f V"
          % (qr["sigma_lsb"], qr["true_sigma_lsb"], qr["true_sigma_v"]))

    print()
    print("=" * 74)
    print("三、单次波形频谱 + 自相关 —— 离散峰=有来源，平坦=宽带拾取")
    print("=" * 74)
    x = v - v.mean()
    top, med, ratio = wave.flatness(x, dt)
    f_pk, a_pk, tops, _ = wave.spectrum(x, dt)
    print("  等效采样率 %.4g Sa/s，分析带宽 %.4g Hz，频率分辨率 %.4g Hz"
          % (1 / dt, 1 / dt / 2, 1 / dt / len(x)))
    print("  前 %d 个谱峰：" % len(tops))
    for f, a in tops:
        print("     %12.4g Hz   %10.5f V" % (f, a))
    print("  谱底噪中位数 %.5f V，最大谱峰 %.5f V，峰/底比 %.1f"
          % (med, top, ratio))
    print("  → 峰/底比只有个位数，说明是宽带底噪而非一条干净谱线")
    p, c = wave.dominant_period(x, dt)
    if p == p:
        print("  自相关：存在周期 %.4g s（%.4g Hz），相关系数 %+.3f" % (p, 1 / p, c))
    else:
        print("  自相关：无相关系数 >0.25 的显著峰 → 无稳定周期（随机噪声特征）")
    return 0


def cmd_spec(inst, args):
    """频谱 + 自相关：客观判主频。会改时基。"""
    ch = args.chan
    orig = float(link.q(inst, ":TIMebase:MAIN:SCALe?"))
    link.apply(inst, [(":TIMebase:MAIN:SCALe", args.time, "s/div")])
    time.sleep(max(1.0, min(3.0, args.time * 20)))       # 慢时基要等重采

    t0, dt, v = wave.fetch(inst, ch)
    if not args.keep:
        link.apply(inst, [(":TIMebase:MAIN:SCALe", orig, "s/div")])

    n = len(v)
    s = wave.stat(v, dt)
    print("通道 %s | 时基 %.4g s/div | 点数 %d | dt=%.4g s | 屏宽 %.4g s | 采样率 %.4g Sa/s"
          % (ch, args.time, n, dt, s["span"], 1 / dt))
    print("直流 %.4f V，交流 Vpp %.4f V，交流 RMS %.4f V"
          % (s["mean"], s["vpp"] - 0, s["rms_ac"]))

    ac = v - v.mean()
    f_pk, a_pk, tops, _ = wave.spectrum(ac, dt)
    print("\n频谱前 %d 峰（去直流、汉宁窗）:" % len(tops))
    for f, a in tops:
        print("    %12.4f Hz   幅度 %10.4f" % (f, a))

    acf = wave.autocorr(ac)
    print("\n自相关（找第一个显著峰）:")
    peaks = []
    for i in range(3, n - 3):
        if acf[i] > acf[i - 1] and acf[i] >= acf[i + 1] and acf[i] > 0.25:
            peaks.append(i)
        if len(peaks) >= 5:
            break
    if peaks:
        for i in peaks:
            print("    lag %5d 点 = %10.4g s  相关系数 %+.3f   → %8.3f Hz"
                  % (i, i * dt, acf[i], 1.0 / (i * dt)))
    else:
        print("    没有相关系数 >0.25 的显著峰 → 波形无稳定周期")

    print("\n针对 100 转/分（0.6000 s 周期、每转 1 脉冲）的定向检查:")
    print("    屏宽 %.4g s，覆盖 %.2f 个周期" % (s["span"], s["span"] / 0.6))
    if s["span"] >= 0.6:
        print("    lag %d 点处自相关 %+.3f  （接近 1 = 存在 0.6 s 周期成分）"
              % (int(round(0.6 / dt)), wave.corr_at(ac, dt, 0.6)))
    else:
        print("    ⚠ 屏宽不足一个周期，这一档看不出 100 转的信号")
    print("    50 Hz（0.02 s）处自相关 %+.3f" % wave.corr_at(ac, dt, 0.02))

    print("\n转速换算 n[rpm] = 60 × f ÷ N（N = 每转脉冲数）:")
    for N in (1, 2, 6, 12, 30, 60):
        print("    N=%2d 时：%.4f Hz → %8.1f 转/分" % (N, f_pk, 60 * f_pk / N))
    return 0


def cmd_env(inst, args):
    """包络分析 —— 把载波滤掉，客观数包络周期，评估干净程度。会改时基。"""
    ch = args.chan
    orig = float(link.q(inst, ":TIMebase:MAIN:SCALe?"))
    link.apply(inst, [(":TIMebase:MAIN:SCALe", args.time, "s/div")])
    time.sleep(2.0)
    t0, dt, v = wave.fetch(inst, ch)
    if not args.keep:
        link.apply(inst, [(":TIMebase:MAIN:SCALe", orig, "s/div")])

    n, span = len(v), len(v) * dt
    print("=" * 70)
    print("通道 %s | 时基 %.4g s/div | 点数 %d | 屏宽 %.4g s | 采样率 %.4g Sa/s"
          % (ch, args.time, n, span, 1 / dt))

    raw = v - np_median(v)
    print("原始: 直流 %.4f V | 峰值 %+.3f / %+.3f V | Vpp %.3f V | RMS %.3f V"
          % (np_median(v), raw.min(), raw.max(), raw.max() - raw.min(),
             float((raw ** 2).mean() ** 0.5)))

    f_raw, _, top_raw, _ = wave.spectrum(raw, dt)
    print("\n[原始信号 FFT 前 5 峰]")
    for f, a in top_raw[:5]:
        print("    %12.3f Hz   幅度 %10.3f" % (f, a))

    env = wave.envelope(v, args.win)
    env -= env.mean()
    f_env, _, top_env, env_spec = wave.spectrum(env, dt)
    print("\n[包络 FFT 前 5 峰]")
    for f, a in top_env[:5]:
        print("    %12.3f Hz   幅度 %10.3f" % (f, a))

    ac = wave.autocorr(env)
    lag = int(round(1.0 / f_env / dt)) if f_env > 0 else 0
    print("\n[自相关] 主频 %.4f Hz → lag %d 点，自相关 %+.3f（接近 1 = 周期稳定）"
          % (f_env, lag, ac[lag] if 0 < lag < len(ac) else float("nan")))

    peaks, f_cnt, jitter, spread = wave.count_peaks(env, dt, ref_freq=f_env)
    if len(peaks) >= 2:
        print("\n[包络峰计数] 共 %d 个峰，跨 %.4g s → 平均频率 %.4f Hz"
              % (len(peaks), (peaks[-1] - peaks[0]) * dt, f_cnt))
        print("    周期抖动 %.3f%% | 峰高离散 %.2f%%" % (jitter, spread))
    else:
        print("\n[包络峰计数] 只找到 %d 个峰，不足以统计" % len(peaks))

    vmin_env = float(np_percentile(env, 10))
    print("\n[基线] 包络谷底 %.4f V | 峰值 %.4f V | 峰谷比 %.1f:1"
          % (vmin_env, env.max(), env.max() / vmin_env if vmin_env > 0 else float("inf")))

    print("\n[转速换算]")
    for N in (1, 2, 6, 12, 30, 60):
        print("    N=%2d 时：%.4f Hz → %8.1f 转/分" % (N, f_env, 60 * f_env / N))

    plt = plot_setup()
    freq_all, spec_all = env_spec
    tt = (t0 + _arange(n) * dt) * 1e3
    fig, ax = plt.subplots(3, 1, figsize=(11, 7.2))
    ax[0].plot(tt, v, lw=0.6, color="#c8a000")
    ax[0].set_ylabel("原始波形 (V)", fontsize=9)
    ax[0].set_title("%s  时基 %.4g s/div   Vpp %.3f V"
                    % (ch, args.time, raw.max() - raw.min()), fontsize=10)
    ax[0].grid(lw=0.3, alpha=0.45)
    ax[1].plot(tt, env, lw=1.0, color="#1f77b4")
    if peaks:
        ax[1].plot(_arange(n)[peaks] * dt * 1e3, env[peaks], "rv", ms=4, label="包络峰")
        ax[1].legend(fontsize=8)
    ax[1].set_ylabel("包络 (V)", fontsize=9)
    ax[1].set_title("包络频率 %.4f Hz   周期抖动 %.2f%%   峰高离散 %.2f%%"
                    % (f_env, jitter, spread), fontsize=9)
    ax[1].grid(lw=0.3, alpha=0.45)
    ax[2].plot(freq_all, spec_all, lw=0.8, color="#2ca02c")
    ax[2].set_xlim(0, min(1000, 1 / dt / 8))
    ax[2].set_xlabel("频率 (Hz)", fontsize=9)
    ax[2].set_ylabel("包络频谱", fontsize=9)
    ax[2].grid(lw=0.3, alpha=0.45)
    fig.tight_layout()
    out = out_png("envelope_%s" % ch)
    fig.savefig(out, dpi=110)
    print("\n→ %s" % out)
    return 0


def cmd_wave(inst, args):
    """多档时基波形对照图。会改时基和档位。"""
    ch = args.chan
    orig_tb = float(link.q(inst, ":TIMebase:MAIN:SCALe?"))
    orig_sc = float(link.q(inst, ":%s:SCALe?" % ch))
    if args.scale:
        link.apply(inst, [(":%s:SCALe" % ch, args.scale, "V/div")])
    scale = float(link.q(inst, ":%s:SCALe?" % ch))
    print("通道 %s，档位 %.4g V/div" % (ch, scale))

    series, stats = [], []
    for ts in args.times:
        link.send(inst, ":TIMebase:MAIN:SCALe %g" % ts)
        time.sleep(1.2)
        t0, dt, v = wave.fetch(inst, ch)
        s = wave.stat(v, dt)
        series.append((ts, t0, dt, v))
        stats.append(s)
        print("  %8.4g s/div  屏宽 %8.4g s  Vpp %8.4f V  min %+8.4f max %+8.4f  "
              "上穿越 %4d  估频 %10.4g Hz"
              % (ts, s["span"], s["vpp"], s["vmin"], s["vmax"], s["rises"],
                 s["freq_est"]))

    if not args.keep:
        link.apply(inst, [(":TIMebase:MAIN:SCALe", orig_tb, "s/div"),
                          (":%s:SCALe" % ch, orig_sc, "V/div")])

    plt = plot_setup()
    n = len(series)
    fig, axes = plt.subplots(n, 1, figsize=(11, 1.5 * n + 0.6), sharey=True)
    if n == 1:
        axes = [axes]
    for ax, (ts, t0, dt, v), s in zip(axes, series, stats):
        x = [(t0 + i * dt) * 1e3 for i in range(len(v))]
        ax.plot(x, v, lw=0.7, color="#c8a000")
        ax.set_ylabel("%.3g s/div" % ts, fontsize=8, rotation=0, ha="right", va="center")
        ax.grid(True, lw=0.3, alpha=0.45)
        ax.tick_params(labelsize=7)
        ax.text(0.995, 0.06, "Vpp=%.3f V  上穿越=%d" % (s["vpp"], s["rises"]),
                transform=ax.transAxes, ha="right", fontsize=7, color="#444")
    axes[-1].set_xlabel("时间 (ms)", fontsize=8)
    axes[len(axes) // 2].set_ylabel("电压 (V)", fontsize=9)
    fig.suptitle("%s 多时基对照   %s" % (ch, time.strftime("%Y-%m-%d %H:%M:%S")),
                 fontsize=10)
    fig.tight_layout(rect=(0.01, 0.01, 1, 0.97))
    out = out_png("waveview_%s" % ch)
    fig.savefig(out, dpi=110)
    print("\n→ %s" % out)
    return 0


# 档位扫描（V/div）：从粗到细
PROBE_SCALES = (5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02)
# 时基扫描（s/div）：10 µs 到 1 s
PROBE_TIMES = (1e-5, 1e-4, 1e-3, 5e-3, 1e-2, 5e-2, 1e-1, 5e-1, 1.0)


def cmd_probe(inst, args):
    """档位 / 时基探测 —— 判断读到的是真信号还是量化假象 / 干扰。"""
    ch = args.chan
    orig_sc = float(link.q(inst, ":%s:SCALe?" % ch))
    orig_tb = float(link.q(inst, ":TIMebase:MAIN:SCALe?"))
    print("通道 %s。原值: 档位 %.4g V/div, 时基 %.4g s/div"
          % (ch, orig_sc, orig_tb))
    print("(下面每一步只改档位或时基，其余不动；跑完%s还原)\n"
          % ("不" if args.keep else "自动"))

    print("=" * 80)
    print("A. 档位探测 —— 时基固定在当前值")
    print("=" * 80)
    print("  %-12s %10s %10s %10s %12s %12s %8s"
          % ("档位", "Vpp(V)", "Vmax(V)", "Vmin(V)", "频率(Hz)", "本地估频", "1 LSB(V)"))
    for sc in PROBE_SCALES:
        link.send(inst, ":%s:SCALe %g" % (ch, sc))
        time.sleep(0.6)
        vpp = link.num(inst, ":MEASure:VPP? %s" % ch)
        vmax = link.num(inst, ":MEASure:VMAX? %s" % ch)
        vmin = link.num(inst, ":MEASure:VMIN? %s" % ch)
        freq = link.num(inst, ":MEASure:FREQuency? %s" % ch)
        _, dt, v = wave.fetch(inst, ch)
        s = wave.stat(v, dt)
        print("  %-12s %s %s %s %s %s %10.4f"
              % ("%.3g V/div" % sc, fmt(vpp), fmt(vmax), fmt(vmin),
                 fmt(freq, 1, 12), fmt(s["freq_est"], 1, 12), link.lsb(sc)))
        sys.stdout.flush()

    mid = wave.nearest_125(max(1.0, min(PROBE_SCALES)))
    link.send(inst, ":%s:SCALe %g" % (ch, mid))
    time.sleep(0.5)

    print()
    print("=" * 80)
    print("B. 时基探测 —— 档位固定 %.4g V/div" % mid)
    print("=" * 80)
    print("  %-12s %10s %14s %12s %10s %12s"
          % ("时基", "Vpp(V)", "频率(Hz)", "本地估频", "上穿越", "屏宽(s)"))
    for ts in PROBE_TIMES:
        link.send(inst, ":TIMebase:MAIN:SCALe %g" % ts)
        time.sleep(1.0)
        vpp = link.num(inst, ":MEASure:VPP? %s" % ch)
        freq = link.num(inst, ":MEASure:FREQuency? %s" % ch)
        _, dt, v = wave.fetch(inst, ch)
        s = wave.stat(v, dt)
        print("  %-12s %s %s %s %12d %s"
              % ("%.3g s/div" % ts, fmt(vpp), fmt(freq, 1, 14),
                 fmt(s["freq_est"], 1, 12), s["rises"], fmt(s["span"], 6, 12)))
        sys.stdout.flush()

    if not args.keep:
        link.apply(inst, [(":%s:SCALe" % ch, orig_sc, "V/div"),
                          (":TIMebase:MAIN:SCALe", orig_tb, "s/div")])
    print("\n错误队列: %s" % link.q(inst, ":SYSTem:ERRor?"))
    return 0


# ── fit：把显示调到"能看" ────────────────────────────────────────────
# 为什么不走 `:AUTOSet`：手册里没有这条命令。
# AUTO 键的真实命令是 `:AUToscale`，本工具也不用它 —— 它一次改一堆东西，
# 看不到改了什么，也没法逐条记录原值。这里只手算四个数。
VDIV_TRY = (5.0, 2.0, 1.0, 0.5, 10.0, 20.0)
TIMEBASE_TRY = (200e-6, 500e-6, 1e-3, 100e-6, 2e-3, 50e-6, 5e-3, 20e-6, 10e-3)


def cmd_fit(inst, args):
    """四步走：放开视野读幅度 → 垂直+电平给对 → 试时基读出周期 → 定时基。"""
    ch = args.chan
    orig_sc = float(link.q(inst, ":%s:SCALe?" % ch))
    orig_os = float(link.q(inst, ":%s:OFFSet?" % ch))
    orig_lv = float(link.q(inst, ":TRIGger:EDGe:LEVel?"))
    orig_tb = float(link.q(inst, ":TIMebase:MAIN:SCALe?"))
    print("通道 %s 原值: 档位 %.4g V/div, 偏移 %+.4g V, 触发电平 %.4g V, 时基 %.4g s/div"
          % (ch, orig_sc, orig_os, orig_lv, orig_tb))

    if not args.apply:
        print("\n[只读侦察] 摘要：")
        for cmd, label in STATE[1:]:
            if ch.split("nel")[1] in cmd or "TIM" in cmd or "TRIG" in cmd:
                print("  %-14s %s" % (label, link.q(inst, cmd)))
        print("\n（未改动任何设置。要改加 --apply）")
        return 0

    print("\n[第 1 步] 放开视野，把幅度读出来")
    link.apply(inst, [(":%s:OFFSet" % ch, 0.0, "V")])
    vmax = vmin = float("nan")
    for vdiv in VDIV_TRY:
        link.send(inst, ":%s:SCALe %.6e" % (ch, vdiv))
        time.sleep(0.6)
        vmax = link.num(inst, ":MEASure:VMAX? %s" % ch)
        vmin = link.num(inst, ":MEASure:VMIN? %s" % ch)
        print("  %-11s VMIN=%-12s VMAX=%-12s"
              % ("%.3g V/div" % vdiv,
                 ("%.4f" % vmin) if vmin == vmin else "无效",
                 ("%.4f" % vmax) if vmax == vmax else "无效"))
        if vmax == vmax and vmin == vmin:
            break
    if vmax != vmax or vmin != vmin:
        print("\n✗ 所有档位都读不出幅度：探头上没有信号。先查接线，不乱改设置。")
        return 1

    print("\n[第 2 步] 垂直档位 + 触发电平一起给对（电平进幅度中间，触发才工作）")
    # 偏移符号是实测出来的：`:OFFSet` 要取 **负** 的中心电压。
    # 实测（0~3 V 方波、0.5 V/div）：偏移 +1.52 V → VMAX 读数变 9.9e37（顶部被切）；
    # 偏移 −1.52 V → VMIN −0.04 / VMAX 3.04，完整进屏。
    # 电平必须落在幅度中间 —— 落在幅度之外时示波器永远等不到触发，AUTO 下就一直自由扫描，
    # 这就是"波形一直在跳"的成因（手册 2-247）。
    center = (vmax + vmin) / 2
    span = vmax - vmin
    link.apply(inst, [
        (":%s:SCALe" % ch, wave.nearest_125(span / 6), "V/div"),
        (":%s:OFFSet" % ch, -center, "V"),
        (":TRIGger:EDGe:LEVel", center, "V"),
    ])
    time.sleep(1.0)

    print("\n[第 3 步] 时基逐档试，直到能读出周期（相当于手动转 s/div）")
    period = float("nan")
    for sdiv in TIMEBASE_TRY:
        link.send(inst, ":TIMebase:MAIN:SCALe %.6e" % sdiv)
        period = link.num(inst, ":MEASure:PERiod? %s" % ch)
        print("  %-13s 周期 = %s" % ("%.3g s/div" % sdiv,
              ("%.6g s" % period) if period == period else "无效"))
        if period == period:
            break
    if period != period:
        print("\n✗ 试遍时基都读不出周期：探头上没有周期性信号。先查接线，不乱改设置。")
        return 1

    print("\n[第 4 步] 按周期定时基")
    link.apply(inst, [(":TIMebase:MAIN:SCALe", wave.suggest_timebase(period), "s/div")])

    print("\n[回读]")
    for cmd, label in STATE[1:]:
        if ch in cmd or "TIM" in cmd or "TRIG" in cmd:
            print("  %-14s %s" % (label, link.q(inst, cmd)))
    print("\n[测量]")
    for key, label, cmd, unit, digits in MEAS[:5]:
        raw = link.try_query(inst, "%s %s" % (cmd, ch))
        print("  %-10s %s" % (label, raw))
    st = link.q(inst, ":TRIGger:STATus?")
    print("\n[判定] %s" % link.trig_status_cn(st))

    if args.shot:
        cmd_shot(inst, args)
    if not args.keep:
        print("\n[还原] 回到 fit 之前的值（--keep 可跳过）")
        link.apply(inst, [(":%s:SCALe" % ch, orig_sc, "V/div"),
                          (":%s:OFFSet" % ch, orig_os, "V"),
                          (":TRIGger:EDGe:LEVel", orig_lv, "V"),
                          (":TIMebase:MAIN:SCALe", orig_tb, "s/div")])
    return 0


def cmd_offset(inst, args):
    """设置通道垂直偏移：正 = 波形上移，单位 V（B 级写命令，不自动还原）。

    偏移符号为实测确认（见 cmd_fit 内注释）：偏移 +1.52 V 时波形整体上移出屏顶。
    """
    link.apply(inst, [(":%s:OFFSet" % args.chan, args.value, "V")])
    print("  回读: %s" % link.q(inst, ":%s:OFFSet?" % args.chan))
    return 0


# ── rec：连续轮询记录（20261009 连续记录验证转正）──────────────────────
def _rec_loop(inst, args):
    """轮询主循环：写一行 flush 一行 —— 进程被硬杀已写数据也不丢。

    连续 4 行查询全失败（≈1 s）判定会话卡死 → close 重建 VXI-11 会话。
    20261010 实测：单会话会阻塞 83 分钟不报错，靠这条看门狗兜底。
    """
    path = os.path.join(link.record_dir(), "recpoll_%s_%s.csv"
                        % (args.tag or "记录", link.stamp()))
    pc0, sc0 = _scope_epoch(inst)
    print("起点钟差 %+.1f s（正 = 仪器快），行距 %.2f s，停 = Ctrl+C / 杀进程"
          % (sc0 - pc0, args.row))
    print("→ %s" % path, flush=True)
    n = bad = fails = 0
    gaps = []
    t0 = prev = time.time()
    fh = open(path, "w", newline="", encoding="utf-8")
    w = csv.writer(fh)
    w.writerow(["pc_time", "scope_time", "freq_ch2_Hz", "pwid_ch2_s",
                "vpp_ch1_V", "vpp_ch2_V"])
    fh.flush()
    try:
        while True:
            pc = time.strftime("%H:%M:%S") + ".%03d" % (int(time.time() * 1000) % 1000)
            st = link.try_query(inst, ":SYSTem:TIME?")
            f2 = link.num(inst, ":MEASure:FREQuency? CHANnel2")
            pw = link.num(inst, ":MEASure:PWIDth? CHANnel2")
            v1 = link.num(inst, ":MEASure:VPP? CHANnel1")
            v2 = link.num(inst, ":MEASure:VPP? CHANnel2")
            w.writerow([pc, st, "%.6g" % f2, "%.6g" % pw, "%.6g" % v1, "%.6g" % v2])
            fh.flush()
            now = time.time()
            gaps.append(now - prev)
            prev = now
            for v in (f2, pw, v1, v2):
                if v != v:                      # nan = 无效读数
                    bad += 1
            n += 1
            if st is None and f2 != f2 and pw != pw and v1 != v1 and v2 != v2:
                fails += 1
                if fails >= 4:
                    print("  [看门狗] 连续 %d 行查询全失败，重建会话…" % fails, flush=True)
                    try:
                        inst.close()
                    except Exception:
                        pass
                    inst = link.connect()
                    link.drain(inst)
                    fails = 0
            else:
                fails = 0
            if n % 20 == 0:
                print("  %5d 行 | %6.0f s | freq %s Hz | 脉宽 %s s | vpp %s / %s V"
                      % (n, now - t0,
                         "——" if f2 != f2 else "%.2f" % f2,
                         "——" if pw != pw else "%.4f" % pw,
                         "——" if v1 != v1 else "%.2f" % v1,
                         "——" if v2 != v2 else "%.2f" % v2), flush=True)
            d = t0 + n * args.row - time.time()
            if d > 0:
                time.sleep(d)
    except KeyboardInterrupt:
        print("\n[停止]")
    finally:
        fh.close()
        span = time.time() - t0
        gaps.sort()
        print("行数 %d，跨度 %.1f s，平均行距 %.0f ms"
              % (n, span, span / max(n - 1, 1) * 1000), flush=True)
        if gaps:
            print("行距中位 %.0f ms，最大 %.0f ms，>1 s 断档 %d 次"
                  % (gaps[len(gaps) // 2] * 1000, gaps[-1] * 1000,
                     sum(1 for g in gaps if g > 1)))
        print("无效读数 %d / %d（%.1f%%）" % (bad, n * 3, 100.0 * bad / max(n * 3, 1)))
        pc1, sc1 = _scope_epoch(inst)
        print("钟差漂移 %+.1f s；错误队列: %s"
              % ((sc1 - pc1) - (sc0 - pc0), link.drain(inst)), flush=True)


def cmd_rec(inst, args):
    """连续轮询记录：CH2 频率/正脉宽 + 双通道 VPP 定节拍写 CSV，直到停（只读）。

    纯只读：不发任何写命令，示波器怎么调是用户的事，录制不碰设置（20261010 定）。
    频率记 CH2：输出正常方波，波形参数测量可信；CH1 是毛刺簇信号，其
    "频率"读数随时基/采样率漂移，不记（20261010 用户定）。
    正脉宽（单齿）：频率测量需帧内两个同向沿，低转速末端（<约50rpm@
    100ms/div）频率会变 nan，靠脉宽撑到停车（20261010 停机实验定）。
    """
    _rec_loop(inst, args)
    return 0


def _scope_epoch(inst):
    """读仪器日期+时间 → (取 PC 时刻, 仪器 epoch 秒)。"""
    pc = time.time()
    y, mo, d = [int(x) for x in link.q(inst, ":SYSTem:DATE?").split(",")]
    h, mi, s = [int(float(x)) for x in link.q(inst, ":SYSTem:TIME?").split(",")]
    return pc, time.mktime((y, mo, d, h, mi, s, 0, 0, -1))


def cmd_clock(inst, args):
    """仪器钟 vs 电脑钟：默认只看偏差；--sync 把仪器钟对到电脑钟。

    对钟只写 :SYSTem:DATE/:TIME（B 级，先报原值），不动任何采集设置。
    写的分辨率是 1 s，写完回读残差，仍差满 1 s 就再补一次。
    """
    pc, scope = _scope_epoch(inst)
    print("  电脑钟 %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(pc)))
    print("  仪器钟 %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(scope)))
    print("  偏差   %+.1f s（正 = 仪器快）" % (scope - pc))
    if not args.sync:
        return 0
    print("\n[对钟] 原值 %s / %s → 改成电脑钟"
          % (link.q(inst, ":SYSTem:DATE?"), link.q(inst, ":SYSTem:TIME?")))
    lt = time.localtime()
    link.send(inst, ":SYSTem:DATE %d,%d,%d" % (lt.tm_year, lt.tm_mon, lt.tm_mday))
    link.send(inst, ":SYSTem:TIME %d,%d,%d" % (lt.tm_hour, lt.tm_min, lt.tm_sec))
    pc, scope = _scope_epoch(inst)
    off = scope - pc
    if abs(off) >= 1:          # 写入到回读之间耗掉了近 1 s → 补写一次
        lt = time.localtime()
        link.send(inst, ":SYSTem:TIME %d,%d,%d" % (lt.tm_hour, lt.tm_min, lt.tm_sec))
        pc, scope = _scope_epoch(inst)
        off = scope - pc
    print("  写入完成，回读残差 %+.1f s" % off)
    return 0


# numpy 的轻量引用（只在绘图/统计子命令里用到，延迟到用时再 import）
def np_median(x):
    import numpy as np
    return float(np.median(x))


def np_percentile(x, q):
    import numpy as np
    return float(np.percentile(x, q))


def _arange(n):
    import numpy as np
    return np.arange(n)


# ── 参数集：每个子命令只挂它真用得上的参数，--help 才不会误导 ─────────
A_QUIET = ((("--quiet",), dict(action="store_true", help="少打时间戳")),)
A_CHAN = ((("--chan",), dict(default="CHANnel1", choices=list(CHANS), help="通道")),)
A_KEEP = ((("--keep",), dict(action="store_true", help="跑完不还原设置（默认自动还原）")),)
A_CSV = ((("--csv",), dict(action="store_true", help="导出波形 CSV")),)
A_TAG = ((("--tag",), dict(default="", help="文件名标签")),)
A_FAST = ((("--fast",), dict(action="store_true",
                             help="快采：跳过截图，只记波形+读数（约5秒）")),)
A_N = ((("-n", "--n"), dict(type=int, default=10, help="连续采集次数")),
       (("-g", "--gap"), dict(type=float, default=0.0, help="每次采集之间停几秒")))
A_TIME = ((("--time",), dict(type=float, default=1e-3, help="主时基 s/div")),)
A_WIN = ((("--win",), dict(type=int, default=11, help="包络平滑窗口（点数）")),)
A_SCALE = ((("--scale",), dict(type=float, default=None, help="通道档位 V/div")),)
A_TIMES = ((("--times",), dict(type=float, nargs="*", default=list(DEFAULT_TIMES),
                               help="要对照的时基序列 s/div")),)
A_FIT = ((("--apply",), dict(action="store_true", help="真正下发（默认只侦察）")),
         (("--shot",), dict(action="store_true", help="顺带截图")))
A_VALUE = ((("--value",), dict(type=float, required=True, help="偏移值 V（正 = 上移）")),)
A_SYNC = ((("--sync",), dict(action="store_true", help="把仪器钟对到电脑钟（写 :SYSTem:DATE/:TIME）")),)
A_REC = ((("--row",), dict(type=float, default=0.25, help="行距秒（默认 0.25，即 4 行/秒）")),)

# 名字 → (处理函数, 一句话说明, 参数集)
COMMANDS = {
    "check": (cmd_check, "连通性 + 错误队列（只读）", ()),
    "state": (cmd_state, "状态 + 测量项（只读）", A_QUIET),
    "read":  (cmd_read,  "全量只读诊断：状态 + 测量 + 波形", A_QUIET + A_CSV),
    "station": (cmd_station, "测站记录：冻结帧一次落盘 截图+状态+波形CSV（只读）", A_TAG + A_QUIET + A_FAST),
    "rec":   (cmd_rec,   "连续轮询记录：频率+VPP 定节拍写 CSV，纯只读不碰设置", A_TAG + A_REC),
    "shot":  (cmd_shot,  "截图（只读，30-60 s）", A_TAG),
    "noise": (cmd_noise, "通道噪声体检：量化台阶 / 离散度 / 频谱（只读）", A_CHAN + A_N),
    "spec":  (cmd_spec,  "频谱 + 自相关，客观判主频（改时基）", A_CHAN + A_TIME + A_KEEP),
    "env":   (cmd_env,   "包络分析 + 出图（改时基）", A_CHAN + A_TIME + A_WIN + A_KEEP),
    "wave":  (cmd_wave,  "多档时基波形对照图（改时基/档位）",
              A_CHAN + A_SCALE + A_TIMES + A_KEEP),
    "probe": (cmd_probe, "档位/时基扫描（改设置）", A_CHAN + A_KEEP),
    "fit":   (cmd_fit,   "自动布防：档位+电平+时基一次给对（改设置）",
              A_CHAN + A_FIT + A_TAG + A_KEEP),
    "offset": (cmd_offset, "设置通道垂直偏移（改设置，不还原）", A_CHAN + A_VALUE),
    "clock": (cmd_clock, "仪器钟 vs 电脑钟：看偏差 / --sync 对钟（改时钟）", A_SYNC),
}


def build_parser():
    ap = argparse.ArgumentParser(
        prog="scopecli", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default=None, help="覆盖默认地址 %s" % link.SCOPE_IP)
    sub = ap.add_subparsers(dest="cmd", metavar="子命令")
    for name, (fn, help_, argspec) in COMMANDS.items():
        p = sub.add_parser(name, help=help_, description=(fn.__doc__ or "").strip())
        for flag, kw in argspec:
            p.add_argument(*flag, **kw)
    return ap


def main(argv=None):
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0
    if not hasattr(args, "chan"):
        args.chan = "CHANnel1"

    fn = COMMANDS[args.cmd][0]
    try:
        inst = link.connect(args.ip)
    except Exception as exc:
        print("✗ 连接失败: %s: %s" % (type(exc).__name__, exc))
        print("  排查顺序: 网线/IP(%s) → 端口(80/111) → 断电重启" % link.SCOPE_IP)
        return 1
    print("✓ VXI-11 会话已建立：%s\n" % link.q(inst, "*IDN?"))
    stale = link.drain(inst)
    if stale:
        print("⚠ 连接时错误队列里已有 %d 条旧错误（不是本次命令造成的，但会干扰归因）：" % len(stale))
        for e in stale:
            print("    %s" % e)
        print()
    try:
        return fn(inst, args)
    except scpi.Denied as exc:
        print("\n✗ 命令被闸门拦下（这是保护，不是 bug）：\n  %s" % exc)
        return 2
    finally:
        inst.close()


if __name__ == "__main__":
    sys.exit(main())
