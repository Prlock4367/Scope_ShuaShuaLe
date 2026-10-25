# -*- coding: utf-8 -*-
"""光标（本页只开放 关闭 / 手动）—— 网页四根线 ⇄ 仪器的像素坐标。

DS2000E 的 CAX/CBX/CAY/CBY 是**屏幕像素整数**（0–699 / 0–399），屏面
14×8 格 = 700×400 px → 一格 50 px、1/5 格 10 px。网页按同一个 10 px
步长硬卡齿，所以换 V/div、换时基之后光标仍落在刻度上。

仪器手动档一次只显示一对（TYPE = TIME 或 AMPLitude）；网页四根都画，
拖哪一根就把 TYPE 切到对应的轴。
"""
from scope import link

PX_STEP = 10                                    # 1/5 格
PX_MAX = {"x": 699, "y": 399}
DEFAULT_POS = {"ax": 150, "bx": 550, "ay": 100, "by": 300}

SCHEMA = {
    "modes": [
        {"value": "OFF", "label": "关闭"},
        {"value": "MANual", "label": "手动"},
    ],
    "displays": [
        {"value": "X", "label": "X"},
        {"value": "Y", "label": "Y"},
        {"value": "XY", "label": "X-Y"},
    ],
    "sources": [
        {"value": "CHANnel1", "label": "CH1"},
        {"value": "CHANnel2", "label": "CH2"},
    ],
}
SOURCES = ("CHANnel1", "CHANnel2")
DISPLAY_SCPI = {"X": "TIME", "Y": "AMPLitude"}   # 固件只认这两档；X-Y/XY 都报 -220
MODE_LABEL = {"OFF": "关闭", "MANual": "手动", "TRACK": "跟踪", "AUTO": "自动", "XY": "XY"}
_MODE_REV = {"OFF": "OFF", "MAN": "MANual", "MANUAL": "MANual",
             "TRAC": "TRACK", "TRACK": "TRACK", "AUTO": "AUTO", "XY": "XY"}


def _q(inst, cmd):
    """只读查询，读不到就 None —— 页面显示默认值，绝不硬发。"""
    try:
        return link.q(inst, cmd)
    except Exception:
        return None


def _int(text):
    try:
        return int(round(float(str(text).strip())))
    except (TypeError, ValueError):
        return None


def _num(text):
    """仪器回读的读数（AXValue? 等）；读不到 / 无效就 None。"""
    try:
        v = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return v if abs(v) < 1e30 and v == v else None


def _mode(text):
    raw = (text or "").strip().upper()
    return _MODE_REV.get(raw, raw or "OFF")


def _src(text):
    raw = (text or "").strip().upper()
    if raw.startswith("CHAN"):
        digits = "".join(c for c in raw if c.isdigit())
        return "CHANnel" + (digits[:1] or "1")
    return raw or "CHANnel1"


def read(inst):
    """读仪器当下的光标状态。关闭时只查一条 MODE，位置用默认值占位。"""
    mode = _mode(_q(inst, ":CURSor:MODE?"))
    out = {"mode": mode, "label": MODE_LABEL.get(mode, mode),
           "supported": mode in ("OFF", "MANual"),
           "type": "TIME", "display": "X", "source": "CHANnel1",
           "pos": dict(DEFAULT_POS)}
    if mode != "MANual":
        return out

    typ = (_q(inst, ":CURSor:MANual:TYPE?") or "").strip().upper()
    out["type"] = "AMPL" if typ.startswith("AMP") else "TIME"
    out["display"] = "Y" if out["type"] == "AMPL" else "X"
    out["source"] = _src(_q(inst, ":CURSor:MANual:SOURce?"))
    for key, cmd in (("ax", ":CURSor:MANual:CAX"), ("bx", ":CURSor:MANual:CBX"),
                     ("ay", ":CURSor:MANual:CAY"), ("by", ":CURSor:MANual:CBY")):
        v = _int(_q(inst, cmd + "?"))
        if v is not None:
            out["pos"][key] = max(0, min(PX_MAX[key[1]], v))
    # 读数直接照抄仪器（TUNit 默认秒、VUNit 默认源单位）：网页只算拖动时的跟手值
    val = {}
    for key, cmd in (("ax", ":CURSor:MANual:AXValue"), ("bx", ":CURSor:MANual:BXValue"),
                     ("ay", ":CURSor:MANual:AYValue"), ("by", ":CURSor:MANual:BYValue"),
                     ("dx", ":CURSor:MANual:XDELta"), ("ix", ":CURSor:MANual:IXDelta"),
                     ("dy", ":CURSor:MANual:YDELta")):
        v = _num(_q(inst, cmd + "?"))
        if v is not None:
            val[key] = v
    out["val"] = val
    return out


def _check(payload):
    axis = str(payload.get("axis") or "").lower()
    if axis not in ("x", "y"):
        return None, None, None, "轴不对：%s" % payload.get("axis")
    which = str(payload.get("cursor") or "A").upper()
    if which not in ("A", "B"):
        return None, None, None, "光标不对：%s" % payload.get("cursor")
    try:
        px = int(round(float(payload.get("px"))))
    except (TypeError, ValueError):
        return None, None, None, "位置不对：%s" % payload.get("px")
    return axis, which, max(0, min(PX_MAX[axis], px)), None


def apply(inst, payload):
    """切模式 / 信源，或移动一根光标。

    {"mode": "MANual"} · {"source": "CHANnel1"} ·
    {"axis":"x","cursor":"A","px":170}

    显示模式（X / Y / X-Y）不在这里：这台固件的 SCPI 不开放它
    （TYPE 写 TIME/AMPL 屏幕不变，写 XY 直接 -220），由网页自己分栏显示。
    """
    mode = payload.get("mode")
    if mode is not None:
        if mode not in ("OFF", "MANual"):
            return {"ok": False, "msg": "本页只开放关闭 / 手动：%s" % mode}
        rows = link.apply(inst, [(":CURSor:MODE", mode, "")])
        return {"ok": True, "applied": rows, "cursor_state": read(inst)}

    base = []
    if _mode(_q(inst, ":CURSor:MODE?")) != "MANual":
        base.append((":CURSor:MODE", "MANual", ""))

    disp = payload.get("display")
    if disp is not None:
        key = str(disp).upper()
        if key not in DISPLAY_SCPI:
            return {"ok": False, "msg": "显示模式不对：%s" % disp}
        base.append((":CURSor:MANual:TYPE", DISPLAY_SCPI[key], ""))
        rows = link.apply(inst, base)
        return {"ok": True, "display": key, "applied": rows,
                "cursor_state": read(inst)}

    src = payload.get("source")
    if src is not None:
        if src not in SOURCES:
            return {"ok": False, "msg": "信源不对：%s" % src}
        base.append((":CURSor:MANual:SOURce", src, ""))
        rows = link.apply(inst, base)
        return {"ok": True, "source": src, "applied": rows,
                "cursor_state": read(inst)}

    axis, which, px, err = _check(payload)
    if err:
        return {"ok": False, "msg": err}
    cmd = ":CURSor:MANual:C%s%s" % (which, "X" if axis == "x" else "Y")
    base.append((cmd, str(px), "px"))           # 像素是整数，原样发（别走科学计数法）
    rows = link.apply(inst, base)
    return {"ok": True, "axis": axis, "cursor": which, "px": px,
            "applied": rows, "cursor_state": read(inst)}


# ── DEMO ──────────────────────────────────────────────────────────────
def demo_default():
    return {"mode": "OFF", "type": "TIME", "source": "CHANnel1",
            "pos": dict(DEFAULT_POS)}


def demo_read(state):
    mode = state["mode"]
    return {"mode": mode, "label": MODE_LABEL.get(mode, mode), "supported": True,
            "type": state["type"], "display": "Y" if state["type"] == "AMPL" else "X",
            "source": state["source"], "pos": dict(state["pos"])}


def demo_apply(state, payload):
    mode = payload.get("mode")
    if mode is not None:
        if mode not in ("OFF", "MANual"):
            return {"ok": False, "msg": "本页只开放关闭 / 手动：%s" % mode}
        state["mode"] = mode
        return {"ok": True, "cursor_state": demo_read(state)}

    state["mode"] = "MANual"
    disp = payload.get("display")
    if disp is not None:
        key = str(disp).upper()
        if key not in DISPLAY_SCPI:
            return {"ok": False, "msg": "显示模式不对：%s" % disp}
        state["type"] = "AMPL" if key == "Y" else "TIME"
        return {"ok": True, "cursor_state": demo_read(state)}

    src = payload.get("source")
    if src is not None:
        if src not in SOURCES:
            return {"ok": False, "msg": "信源不对：%s" % src}
        state["source"] = src
        return {"ok": True, "cursor_state": demo_read(state)}

    axis, which, px, err = _check(payload)
    if err:
        return {"ok": False, "msg": err}
    state["pos"]["%s%s" % (which.lower(), axis)] = px
    return {"ok": True, "axis": axis, "cursor": which, "px": px,
            "cursor_state": demo_read(state)}
