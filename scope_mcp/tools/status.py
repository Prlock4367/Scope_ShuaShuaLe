# -*- coding: utf-8 -*-
"""A 组工具：只读发现（4 个）。

    scope_status   仪器在不在、健不健康
    scope_state    现在什么档位 / 时基 / 触发
    scope_measure  现在读数是多少
    scope_errors   错误队列

四个全部 `read_only_hint=True` —— 客户端据此自动放行，不弹确认框。
"""
from typing import Annotated

from mcp.types import ToolAnnotations
from pydantic import Field

from .. import conn, format as fmt, state as st

READ_ONLY = ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=True,
)


def register(mcp):
    """把 4 个只读工具挂到 server 上。

    模块级注册（在 register 里 add_tool）而不是 import 时就注册 ——
    这样 server.py 是唯一知道"有哪些工具组"的地方，加一组只改一行。
    """

    @mcp.tool(annotations=READ_ONLY)
    def scope_status() -> dict:
        """检查示波器是否可连接，并返回错误队列。

        这是**第一个该调用的工具** —— 在问任何读数之前，先确认仪器在线且干净。
        返回带时间戳的连接状态、仪器身份字符串、以及连接时清掉的历史错误。

        连通且干净时返回 ok=true、connected=true；连不上时返回 ok=false，
        且 hint 字段给出排查顺序（**远程唯一的兜底手段是断电重启**）。
        """
        try:
            inst = conn.instrument()
            idn = conn.q_required(inst, "*IDN?")
            stale = conn_drain_errors(inst)
            return fmt.ok(
                ts=fmt.stamp(),
                connected=True,
                idn=idn,
                address=conn.SCOPE_IP,
                transport="VXI-11 (111)",
                stale_errors_on_connect=stale,
                note="仪器身份以 *IDN? 实时回值为准。",
            )
        except Exception as exc:
            return conn.conn_hint(exc)

    @mcp.tool(annotations=READ_ONLY)
    def scope_state() -> dict:
        """读取示波器当前完整配置：两个通道的档位/偏移/耦合/探头、时基、触发。

        **一次返回全部状态项**，不需要分多次问 —— 档位、时基、触发一起给，
        因为这些是后续几乎所有操作都要用到的信息。

        所有值都是**此刻实时查询**得到的，不是历史快照。
        返回值里的 ts 是查询时刻；unreadable 列出读不到的项目（一般为空）。
        """
        try:
            inst = conn.instrument()
            return st.read_state(inst, conn.q)
        except Exception as exc:
            return conn.conn_hint(exc)

    @mcp.tool(annotations=READ_ONLY)
    def scope_measure(
        source: Annotated[str, Field(
            description="通道，只有两个：CHANnel1 / CHANnel2（也接受 CH1 / 1 的写法）",
        )] = "CHANnel1",
        items: Annotated[list[str] | None, Field(
            description=("测量项英文名列表，如 [\"vpp\", \"freq\", \"rtime\"]。"
                         "可用项：freq period vpp vmax vmin vtop vbase vavg vrms "
                         "vamp rtime ftime pduty pwid。省略或不传 = 返回全部 14 项。"
                         "未识别的项会出现在返回值的 unknown 字段里。"),
        )] = None,
    ) -> dict:
        """读取指定通道的自动测量值（当前瞬时读数，不含历史）。

        重要：DS2000E 系列**没有** :MEASure:ITEM 聚合命令（那是 DS1000Z 的写法）。
        本工具内部把每个测量项映射到各自独立的查询命令，所以 items 必须逐项可查。

        返回的每个值都是 {key, label, value, unit, raw, valid} 结构：
        valid=false 表示仪器判该读数无效（回 9.9e37 哨兵），此时 value 为 null。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        chan = st.norm_chan(source)
        if chan is None:
            return fmt.fail(
                "通道名无法识别：%r" % source,
                "本仪器只有两个通道，请用 CHANnel1 或 CHANnel2（也接受 CH1 / 1）。")

        keys = items or [k for k, _c, _u, _l in st.MEASURE]
        unknown = [k for k in keys if k not in st.MEASURE_BY_KEY]

        values = {}
        for k in keys:
            if k in unknown:
                continue
            cmd, unit, label = st.MEASURE_BY_KEY[k]
            values[k] = fmt.value(k, conn.q(inst, "%s %s" % (cmd, chan)), unit, label)

        return fmt.ok(
            ts=fmt.stamp(),
            source=chan,
            values=values,
            invalid=[k for k, v in values.items() if not v["valid"]],
            unknown=unknown,
            note="valid=false 表示仪器判该读数无效（回 9.9e37 哨兵），不是 0。",
        )

    @mcp.tool(annotations=READ_ONLY)
    def scope_errors() -> dict:
        """读取并**清空**示波器错误队列。

        连接后错误队列里可能躺着上一条命令留下的旧错误。
        发现读数异常时调用本工具，看仪器到底在抱怨什么。
        返回的每条错误含原始码与建议修法。
        """
        try:
            inst = conn.instrument()
            errs = conn_drain_errors(inst)
            return fmt.ok(ts=fmt.stamp(), count=len(errs), errors=errs,
                          clean=(len(errs) == 0))
        except Exception as exc:
            return conn.conn_hint(exc)

    return mcp


def conn_drain_errors(inst):
    """读空错误队列 → 带解释的列表。"""
    from scope import link as _link
    out = []
    for raw in _link.drain(inst):
        out.append({"raw": raw, "hint": _error_hint(raw)})
    return out


def _error_hint(raw):
    """错误码 → 修法。抄 anthropic「错误当教学」。"""
    s = str(raw)
    if "-113" in s:
        return "Undefined header：命令拼写不对或本型号不支持。对照 data/ds2000e_commands.json。"
    if "-222" in s:
        return "Data out of range：参数超范围，检查档位/电平的合法取值。"
    if "-108" in s:
        return "Parameter not allowed：这条命令不接受该参数。"
    if "-100" in s:
        return "Command error：命令语法错。"
    return ""
