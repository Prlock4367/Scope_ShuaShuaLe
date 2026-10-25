# -*- coding: utf-8 -*-
"""E 组工具：采集控制（4 个，只读 1 + 写 3）。

    scope_acquire_status   看触发状态/采集模式/存储深度/采样率（只读）
    scope_run              连续采集（写）
    scope_stop             停止并冻结（写）
    scope_single           单次触发（写）

为什么独立成组：**采集是"停还是跑"直接决定读数有没有意义**。
真机上 `scope_measure` 在跑动状态下读数会漂（边沿计数抖），
先 stop 再读才稳 —— 这是上游 README 也强调过的一条。
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


def register_read(mcp):
    """只读：采集状态。"""

    @serialized
    @mcp.tool(annotations=READ_ONLY)
    def scope_acquire_status() -> dict:
        """读取采集与触发运行状态（**不改任何设置**）。

        返回：
        - trigger_status  触发状态：TD（已触发）/ WAIT（等触发）/ RUN（自动运行中）/ STOP / AUTO
        - acquire_type    采集模式：NORMal（普通）/ AVERages（平均）/ PEAK（峰值）/ HRESolution（高分辨率）
        - averages        平均次数（仅 AVERages 模式有效）
        - mem_depth       存储深度（点数）。本机装 MEMDepth 选件，实测 14000 点
        - sample_rate     当前采样率（Sa/s）
        - sweep           触发扫描方式：AUTO / NORMal / SINGle

        **怎么用**：读数值前先看 trigger_status。若是 RUN 或 WAIT，
        读数可能不稳；要稳定读数先 scope_stop 或 scope_single。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        out = fmt.ok(ts=fmt.stamp())
        fields = {
            "trigger_status": ":TRIGger:STATus?",
            "sweep": ":TRIGger:SWEep?",
            "acquire_type": ":ACQuire:TYPE?",
            "averages": ":ACQuire:AVERages?",
            "mem_depth": ":ACQuire:MDEPth?",
            "sample_rate": ":ACQuire:SRATe?",
        }
        for key, cmd in fields.items():
            out[key] = fmt.value(key, conn.q(inst, cmd))

        # 一句人话总判
        st = out.get("trigger_status", {}).get("value")
        out["verdict"] = {
            "TD": "已触发、画面已冻结 —— 适合读数。",
            "STOP": "已停止 —— 适合读数。",
            "WAIT": "正在等触发 —— 读数可能随时变化。",
            "RUN": "自动运行中 —— 读数会漂，要稳定读数请先 stop。",
            "AUTO": "自动运行中 —— 同上。",
        }.get(str(st).upper() if st else "", "状态未知，建议先 scope_status 看连通性。")
        return out

    return mcp


def register_write(mcp):
    """写：运行控制。"""

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_run() -> dict:
        """启动连续采集（等价按面板 RUN）。

        ⚠️ 写操作。**不改任何其它设置**，只是让采集跑起来。
        跑起来后读数会随信号实时变化 —— 要冻结画面用 scope_stop，
        要抓单次事件用 scope_single。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)
        from scope import link as _link
        _link.send(inst, ":RUN")
        return fmt.ok(ts=fmt.stamp(), sent=":RUN",
                      trigger_status=fmt.value("trigger_status",
                                               conn.q(inst, ":TRIGger:STATus?")),
                      note="已启动连续采集。")

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_stop() -> dict:
        """停止采集并冻结当前画面。

        ⚠️ 写操作。**读数的标准前置动作** —— 冻结后
        scope_measure / scope_capture 读到的才是一份稳定数据。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)
        from scope import link as _link
        _link.send(inst, ":STOP")
        return fmt.ok(ts=fmt.stamp(), sent=":STOP",
                      trigger_status=fmt.value("trigger_status",
                                               conn.q(inst, ":TRIGger:STATus?")),
                      note="已停止并冻结，可以读稳定数据了。")

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_single() -> dict:
        """单次采集：等一次触发事件后自动停止。

        ⚠️ 写操作。适合抓**偶发/单次**信号（上电冲击、毛刺）。
        发出后仪器进入"等触发"状态；**触发到之前读数无意义**，
        要确认是否已触发，调 scope_acquire_status 看 trigger_status 是否为 TD。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)
        from scope import link as _link
        _link.send(inst, ":SINGle")
        st = conn.q(inst, ":TRIGger:STATus?")
        return fmt.ok(ts=fmt.stamp(), sent=":SINGle",
                      trigger_status=fmt.value("trigger_status", st),
                      note=("已布防。若 trigger_status 仍是 WAIT/RUN，说明**还没等到触发**；"
                            "等信号到来后再调 scope_acquire_status 确认 TD。"))

    @requires_write
    @serialized
    @mcp.tool(annotations=WRITE)
    def scope_autoset() -> dict:
        """**自动设置**（面板 AUTO 键的软件版）。

        ⚠️ 写操作，而且**一次改一堆**：垂直档位、时基、触发电平、
        通道开关都可能被仪器自己改掉。这是它唯一的用法，没有参数。

        为什么和别的写工具不一样：
        别的工具都是"只改我点名的项，逐条报原值"；这一条做不到 ——
        调多少项是仪器自己决定的。所以这里改用**前后快照对比**：
        调之前把 21 项设置全读一遍，调完再读一遍，把**变了的项**列给你。
        想知道它到底动了什么，看返回里的 `changes`。

        **什么时候用**：屏幕上一片乱、完全看不出信号长什么样的时候。
        **什么时候别用**：
        - 已经调好、只想微调某一项 → 用 scope_configure
        - 要留证据、写记录 → 用 scope_autofit（手算，逐条可追溯）

        仪器要跑几秒（它在自己扫档位），期间不要发别的命令。
        """
        try:
            inst = conn.instrument()
        except Exception as exc:
            return conn.conn_hint(exc)

        from scope import link as _link
        from .. import state as st

        # 调之前：读完整快照
        before = st.read_state(inst, conn.q)

        try:
            # 链上 *OPC? 让命令阻塞到调完 —— 照抄 erebusnz/rigol-mcp 的做法
            # （AUToscale 要跑几秒，不阻塞就会读到调一半的状态）
            _link.send(inst, ":AUToscale")
            conn.q(inst, "*OPC?")
        except Exception as exc:
            return fmt.fail("自动设置失败：%s" % exc,
                            "仪器可能被占用或超时。重试一次；仍失败则等 30 秒。")

        # 调之后：再读一遍，找差异
        after = st.read_state(inst, conn.q)

        changes = []
        b_flat, a_flat = _flatten(before), _flatten(after)
        for key, item in a_flat.items():
            old = b_flat.get(key, {})
            ov, nv = old.get("value"), item.get("value")
            if ov != nv:
                changes.append({
                    "key": key, "label": item.get("label", ""),
                    "old": ov, "new": nv, "unit": item.get("unit", ""),
                })

        return fmt.ok(
            ts=fmt.stamp(),
            sent=":AUToscale",
            changes=changes,
            changed_count=len(changes),
            note=("这是面板 AUTO 键的软件版，改哪些项由仪器自己决定。"
                  "changes 是调前/调后两次快照的差异；没列出的项 = 这次没动。"),
        )

    return mcp


def _flatten(state_result):
    """read_state 的返回是嵌套的（channels.ch1 / timebase / trigger）→ 拉平成
    {key: value条目}，便于前后对比。"""
    out = {}
    for grp in ("channels", "timebase", "trigger"):
        block = state_result.get(grp) or {}
        if grp == "channels":
            for sub in block.values():       # ch1 / ch2
                for k, v in (sub or {}).items():
                    if isinstance(v, dict):
                        out[k] = v
        else:
            for k, v in block.items():
                if isinstance(v, dict):
                    out[k] = v
    return out
