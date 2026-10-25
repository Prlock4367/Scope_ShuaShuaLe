# -*- coding: utf-8 -*-
"""F 组工具：测量扩展（3 个）。

    scope_measure_stat     任一测量项的统计（均值/最大/最小/标准差/当前）
    scope_measure_delay    双通道延迟/相位（8 项）
    scope_freq_counter     硬件频率计（源可含 EXT）

这三样是 DS2000E 相对 DS1000Z 的**增量能力**，现有开源 MCP 全没有
（它们建在 `:MEASure:ITEM` 上，DS2000E 无此命令）。
命令全部核过 DS2000E 编程手册。
"""
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import conn, format as fmt, state as st
from ..guard import requires_write, serialized

READ_ONLY = ToolAnnotations(
    read_only_hint=True, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True,
)
WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False,
    idempotent_hint=True, open_world_hint=True,
)

# 支持统计的测量项（手册 :MEASure:<ITEM>:S* 节，30 项）
STAT_ITEMS = (
    "FREQuency", "PERiod", "PWIDth", "NWIDth", "PDUTy", "NDUTy",
    "RTIMe", "FTIMe", "VMAX", "VMIN", "VPP", "VTOP", "VBASe", "VAMP",
    "VAVG", "VRMS", "PVRMs", "OVERshoot", "PREShoot", "MARea", "MPARea",
    "RPHase", "FPHase", "RDELay", "FDELay", "RFPhase", "FRPhase",
    "RFDelay", "FRDelay",
)
# 统计后缀 →（命令段, 含义）
STAT_SUFFIX = {
    "average": ("SAVerage", "统计平均"),
    "max": ("SMAXimum", "统计最大值"),
    "min": ("SMINimum", "统计最小值"),
    "current": ("SCURrent", "当前值"),
    "stdev": ("SDEViation", "统计标准差"),
}

# 双源延迟/相位 8 项：命令 → 说明
DELAY_ITEMS = {
    "rdelay": (":MEASure:RDELay", "上升沿→上升沿 延迟（s）"),
    "fdelay": (":MEASure:FDELay", "下降沿→下降沿 延迟（s）"),
    "rfdelay": (":MEASure:RFDelay", "上升沿→下降沿 延迟（s）"),
    "frdelay": (":MEASure:FRDelay", "下降沿→上升沿 延迟（s）"),
    "rphase": (":MEASure:RPHase", "上升沿→上升沿 相位（°）"),
    "fphase": (":MEASure:FPHase", "下降沿→下降沿 相位（°）"),
    "rfphase": (":MEASure:RFPhase", "上升沿→下降沿 相位（°）"),
    "frphase": (":MEASure:FRPhase", "下降沿→上升沿 相位（°）"),
}

# 频率计源（手册 :MEASure:COUNter:SOURce）
COUNTER_SOURCES = ("OFF", "CHANnel1", "CHANnel2", "EXT")


def _norm_item(item):
    """测量项名归一（接受 vpp / VPP / Vpp）→ 手册写法。"""
    s = (item or "").strip().lower()
    for cand in STAT_ITEMS:
        if cand.lower() == s:
            return cand
    # 按去掉大小写后前缀匹配（用户可能写 vpp、fre 等）
    for cand in STAT_ITEMS:
        if cand.lower().startswith(s) and len(s) >= 3:
            return cand
    return None


def register_read(mcp):
    """F 组只读部分：统计与延迟（两条命令都是查询，真只读）。"""

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_measure_stat(
        source: Annotated[str, Field(
            description="通道：CHANnel1 / CHANnel2（也接受 CH1 / 1）",
        )] = "CHANnel1",
        item: Annotated[str, Field(
            description=("测量项：vpp / vmax / vmin / vtop / vbase / vamp / vavg / vrms / "
                         "freq / period / pwidth / nwidth / pduty / nduty / rtime / ftime / "
                         "overshoot / preshoot / marea / mparea 等（大小写不敏感）"),
        )] = "vpp",
        stat: Annotated[str, Field(
            description=("统计类型：average（均值）/ max / min / current（当前）/ "
                         "stdev（标准差）/ all（全都要）"),
        )] = "all",
    ) -> dict:
        """读取某个测量项的**统计值** —— 均值/最大/最小/当前/标准差。

        与 scope_measure 的区别：
        - scope_measure 读的是**瞬时值**（就是 current）
        - 本工具读**统计值**，要多次采集才有意义

        ⚠️ 统计要**攒样本**：连续跑几秒后统计量才稳定。
        若返回的 max/min 相差很大，说明信号在变或还在攒样本。

        单位：电压项 V，频率 Hz，时间 s，占空比 %，相位 °。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        chan = st.norm_chan(source)
        if chan is None:
            return fmt.fail("通道名无法识别：%r" % source,
                            "只有两个通道：CHANnel1 / CHANnel2（也接受 CH1 / 1）。")
        it = _norm_item(item)
        if it is None:
            return fmt.fail("测量项无法识别：%r" % item,
                            "可用项见 description；大小写不敏感。")

        suffixes = (list(STAT_SUFFIX.items()) if stat == "all"
                    else [(stat, STAT_SUFFIX.get(stat))])
        if any(s[1] is None for s in suffixes):
            return fmt.fail("统计类型无法识别：%r" % stat,
                            "只能是 average / max / min / current / stdev / all。")

        out = fmt.ok(ts=fmt.stamp(), source=chan, item=it, stats={})
        for key, (seg, label) in suffixes:
            cmd = ":MEASure:%s:%s? %s" % (it, seg, chan)
            out["stats"][key] = fmt.value(key, conn.q(inst, cmd))
            out["stats"][key]["label"] = label
        out["note"] = ("统计需多次采集才稳定；max 与 min 差距大 = 信号在变或样本还少。"
                       "先用 scope_run 跑一会，或 scope_stop 冻结后连读几次。")
        return out

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_measure_delay(
        source1: Annotated[str, Field(
            description="参考通道（基准）：CHANnel1 / CHANnel2",
        )] = "CHANnel1",
        source2: Annotated[str, Field(
            description="被测通道：CHANnel1 / CHANnel2",
        )] = "CHANnel2",
        items: Annotated[list[str] | None, Field(
            description=("要测的项：rdelay / fdelay / rfdelay / frdelay / "
                         "rphase / fphase / rfphase / frphase。"
                         "省略 = 全部 8 项。"),
        )] = None,
    ) -> dict:
        """测两个通道之间的**延迟（秒）或相位（度）**。

        8 种组合（r=上升沿，f=下降沿，前一个字母属 source1）：
        - rdelay = source1 上升沿 → source2 上升沿，时间差（s）
        - rphase = 同上，相位差（°）
        - rfdelay/rfphase = source1 上升 → source2 下降
        - 其余类推

        **典型用途**：测放大器群延迟、测两路信号相位差、测时钟-数据时序。

        ⚠️ 读数稳定前建议先 scope_stop。返回 9.9e37 是仪器"测不到"哨兵
        （valid=false），不是 0。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        c1, c2 = st.norm_chan(source1), st.norm_chan(source2)
        if c1 is None or c2 is None:
            return fmt.fail("通道名无法识别：%r / %r" % (source1, source2),
                            "只有两个通道：CHANnel1 / CHANnel2。")

        keys = ([k for k in DELAY_ITEMS] if not items
                else [str(x).strip().lower() for x in items])
        bad = [k for k in keys if k not in DELAY_ITEMS]
        if bad:
            return fmt.fail("项无法识别：%s" % bad,
                            "可用：" + " ".join(DELAY_ITEMS.keys()))

        out = fmt.ok(ts=fmt.stamp(), source1=c1, source2=c2, results={})
        for k in keys:
            cmd, label = DELAY_ITEMS[k]
            raw = conn.q(inst, "%s? %s,%s" % (cmd, c1, c2))
            v = fmt.value(k, raw)
            v["label"] = label
            out["results"][k] = v
        return out

    return mcp


def register_write(mcp):
    """F 组写部分：频率计（设源会改仪器）。"""

    @serialized
    @mcp.tool(annotations=WRITE)          # 传 source 时会改频率计源 → 按写标注
    def scope_freq_counter(
        source: Annotated[str, Field(
            description=("频率计源：CHANnel1 / CHANnel2 / EXT（外部触发输入，不占通道）"
                         "/ OFF。留空 = 只读当前值，不改设置"),
        )] = "",
    ) -> dict:
        """硬件频率计：读频率，或设定它的信号源。

        - 不传 source：**只读**当前频率值。
        - 传 source：先切源（**写操作**），再读值。

        **频率计的精髓是 `EXT`**：源可选外部触发输入，
        这样测频**不占用任何一个模拟通道** —— 测转速、测外部时钟特别有用。

        ⚠️ 关闭时（源 = OFF）读数返回 9.9e37 哨兵，此时 `valid=false`。
        精度受时基限制（本机数据手册：≤±25 ppm）。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        changed = None
        if source:
            src = None
            for cand in COUNTER_SOURCES:
                if cand.lower() == source.strip().lower():
                    src = cand
                    break
            if src is None:
                return fmt.fail("频率计源无法识别：%r" % source,
                                "只能是 OFF / CHANnel1 / CHANnel2 / EXT。")
            # 写操作：要开控制权限
            from ..guard import write_enabled
            if not write_enabled():
                return fmt.fail(
                    "改频率计源是写操作，但 server 当前为只读模式。",
                    "若要改设置，请设置环境变量 SCOPE_MCP_ALLOW_CONTROL=1 后重启 server；"
                    "或省略 source 参数只读频率值。")
            from scope import link as _link
            from scope import scpi as _scpi
            old = conn.q(inst, ":MEASure:COUNter:SOURce?")
            try:
                _scpi.guard(":MEASure:COUNter:SOURce " + src)
            except _scpi.Denied as exc:
                return fmt.fail("命令被闸门拒绝：%s" % exc, "该命令未在 B 级白名单内。")
            _link.send(inst, ":MEASure:COUNter:SOURce " + src)
            changed = {"from": old, "to": src}

        cur = conn.q(inst, ":MEASure:COUNter:SOURce?")
        val = conn.q(inst, ":MEASure:COUNter:VALue?")
        out = fmt.ok(
            ts=fmt.stamp(),
            source=fmt.value("source", cur),
            frequency=fmt.value("frequency", val, "Hz", "频率计读数"),
            changed=changed,
        )
        fv = out["frequency"]
        if not fv.get("valid"):
            out["hint"] = ("频率计未开或读不到：源=%s。把 source 设为 CHANnel1/CHANnel2/EXT "
                           "再读（需写权限）。" % (cur or "?"))
        return out

    return mcp
