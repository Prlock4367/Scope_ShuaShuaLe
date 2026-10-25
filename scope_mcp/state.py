# -*- coding: utf-8 -*-
"""仪器状态与测量项定义 —— A 组工具共用的**唯一一份**。

为什么单独立一个文件：状态项清单（24 条）和测量项清单（14 条）会被
`scope_status` / `scope_state` / `scope_measure` 三个工具共用。抄三遍就是
"11 个 scope_*.py" 的老路 —— 那是本仓库明确要避免的。

所有命令**逐字**来自 `data/ds2000e_commands.json`，新增前先跑 tools/extract_scpi.py 重抽。
"""
from . import format as fmt

# ── 状态项：一次给全 ──────────────────────────────────────────────────
# 每个工具必须返回"模型做下一步所必需的全部 key"。
# 基准测试证据：两个 server 通过率同为 90%，token 差 4.98×，
# 根因就是查询工具漏返回下一步要用的字段、逼模型多调一轮。
# → 所以这里**一次列全**：通道档位/偏移/耦合/探头/带宽 + 时基 + 触发。
#   不要让模型先问档位、再问时基。
STATE = [
    # 通道（CH1）
    ("ch1_display",  ":CHANnel1:DISPlay?",   "",   "CH1 显示"),
    ("ch1_scale",    ":CHANnel1:SCALe?",     "V/div", "CH1 档位"),
    ("ch1_offset",   ":CHANnel1:OFFSet?",    "V",  "CH1 偏移"),
    ("ch1_coupling", ":CHANnel1:COUPling?",  "",   "CH1 耦合"),
    ("ch1_probe",    ":CHANnel1:PROBe?",     "",   "CH1 探头比"),
    ("ch1_bwlimit",  ":CHANnel1:BWLimit?",   "",   "CH1 带宽限制"),
    # 通道（CH2）
    ("ch2_display",  ":CHANnel2:DISPlay?",   "",   "CH2 显示"),
    ("ch2_scale",    ":CHANnel2:SCALe?",     "V/div", "CH2 档位"),
    ("ch2_offset",   ":CHANnel2:OFFSet?",    "V",  "CH2 偏移"),
    ("ch2_coupling", ":CHANnel2:COUPling?",  "",   "CH2 耦合"),
    ("ch2_probe",    ":CHANnel2:PROBe?",     "",   "CH2 探头比"),
    ("ch2_bwlimit",  ":CHANnel2:BWLimit?",   "",   "CH2 带宽限制"),
    # 时基
    ("tb_scale",     ":TIMebase:MAIN:SCALe?", "s/div", "主时基"),
    ("tb_offset",    ":TIMebase:MAIN:OFFSet?", "s", "时基偏移"),
    ("tb_mode",      ":TIMebase:MODE?",      "",   "时基模式"),
    # 触发
    ("trig_status",  ":TRIGger:STATus?",     "",   "触发状态"),
    ("trig_mode",    ":TRIGger:MODE?",       "",   "触发类型"),
    ("trig_sweep",   ":TRIGger:SWEep?",      "",   "触发扫描"),
    ("trig_coupling", ":TRIGger:COUPling?",  "",   "触发耦合"),
    ("trig_source",  ":TRIGger:EDGe:SOURce?", "",  "边沿触发源"),
    ("trig_slope",   ":TRIGger:EDGe:SLOPe?", "",   "边沿斜率"),
    ("trig_level",   ":TRIGger:EDGe:LEVel?", "V",  "边沿触发电平"),
]

# ── 测量项 ────────────────────────────────────────────────────────────
# ⭐ 本项目核心差异：DS2000E **没有** `:MEASure:ITEM` 聚合命令。
#    现有开源 MCP 全部建在那条命令上，所以在 DS2000E 上全失效。
#    这里把每个测量项映射到它**自己的独立查询命令**，是机械映射，不是能力缺失。
MEASURE = [
    ("freq",   ":MEASure:FREQuency?", "Hz", "频率"),
    ("period", ":MEASure:PERiod?",    "s",  "周期"),
    ("vpp",    ":MEASure:VPP?",       "V",  "峰峰值"),
    ("vmax",   ":MEASure:VMAX?",      "V",  "最大值"),
    ("vmin",   ":MEASure:VMIN?",      "V",  "最小值"),
    ("vtop",   ":MEASure:VTOP?",      "V",  "高电平"),
    ("vbase",  ":MEASure:VBASe?",     "V",  "低电平"),
    ("vavg",   ":MEASure:VAVG?",      "V",  "平均值"),
    ("vrms",   ":MEASure:VRMS?",      "V",  "有效值"),
    ("vamp",   ":MEASure:VAMP?",      "V",  "幅度"),
    ("rtime",  ":MEASure:RTIMe?",     "s",  "上升时间"),
    ("ftime",  ":MEASure:FTIMe?",     "s",  "下降时间"),
    ("pduty",  ":MEASure:PDUTy?",     "%",  "正占空比"),
    ("pwid",   ":MEASure:PWIDth?",    "s",  "正脉宽"),
]

MEASURE_BY_KEY = {k: (c, u, l) for k, c, u, l in MEASURE}

CHANNELS = ("CHANnel1", "CHANnel2")


def norm_chan(source):
    """通道名归一 —— 接受 CH1 / 1 / CHANnel1 / chan1，输出 CHANnel1。

    poka-yoke：与其让模型猜格式，不如把常见写法全认了。
    认不出来返回 None，由调用方给结构化错误。
    """
    s = str(source or "").strip().upper().replace("CHANNEL", "").replace("CHAN", "")
    s = s.lstrip("_").strip()
    if s in ("1", "2"):
        return "CHANnel" + s
    return None


def read_state(inst, q):
    """逐条实时读仪器状态 → 带时间戳的完整快照。

    **一律实时查询**：不得依据历史截图或历史数据推断 ——
    仪器随时可能被复位或断电重启。
    """
    items, errors = {}, []
    for key, cmd, unit, label in STATE:
        raw = q(inst, cmd)
        if raw is None:
            errors.append({"key": key, "cmd": cmd})
            continue
        items[key] = fmt.value(key, raw, unit, label)
    return fmt.ok(
        ts=fmt.stamp(),
        channels={
            ch: {k: v for k, v in items.items() if k.startswith(ch)}
            for ch in ("ch1", "ch2")
        },
        timebase={k: v for k, v in items.items() if k.startswith("tb_")},
        trigger={k: v for k, v in items.items() if k.startswith("trig_")},
        unreadable=errors,
    )
