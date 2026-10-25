# -*- coding: utf-8 -*-
"""触发配置 —— 网页四种触发类型的 UI 状态 ⇄ SCPI。

页面只认一套短值（CH1 / POS / GT …）；SCPI 的完整写法、查询回值的缩写，
以及「脉宽/斜率的极性+条件其实是一条 WHEN」都收在这里，页面不用知道。

四种类型都是本机免选件的波形类触发（用户手册 5-7）：
    边沿 EDGE · 脉宽 PULSe · 欠幅 RUNT · 斜率 SLOPe
视频（HDTV）/超幅/第 N 边沿/超时/延迟/持续时间/USB/CAN 属选件或未装，不收。

改设置一律走 link.apply()：每条先实时读原值，写命令过 B 级白名单。
"""
import math

from scope import link

TYPE_ORDER = ("EDGE", "PULSE", "RUNT", "SLOPE")
TYPE_LABEL = {
    "EDGE": "边沿触发",
    "PULSE": "脉宽触发",
    "RUNT": "欠幅脉冲触发",
    "SLOPE": "斜率触发",
}
TYPE_SCPI = {"EDGE": "EDGE", "PULSE": "PULSe", "RUNT": "RUNT", "SLOPE": "SLOPe"}


def _opts(*pairs):
    return [{"value": v, "label": l} for v, l in pairs]


SOURCE_EDGE = _opts(("CH1", "CH1"), ("CH2", "CH2"), ("EXT", "EXT"), ("AC", "AC"))
SOURCE_CH = _opts(("CH1", "CH1"), ("CH2", "CH2"))
SOURCE_3 = _opts(("CH1", "CH1"), ("CH2", "CH2"), ("EXT", "EXT"))
POLARITY = _opts(("POS", "正脉冲"), ("NEG", "负脉冲"))
COND_3 = _opts(("GT", "大于"), ("LT", "小于"), ("IN", "区间"))
COND_RUNT = _opts(("NONE", "不限"), ("GT", "大于"), ("LT", "小于"), ("IN", "区间"))

# 页面渲染用的字段表。when：只在对应条件成立时显示。
SCHEMA = {
    "types": [{"value": t, "label": TYPE_LABEL[t]} for t in TYPE_ORDER],
    "fields": {
        "EDGE": [
            {"key": "source", "label": "源", "kind": "chips", "options": SOURCE_EDGE},
            {"key": "slope", "label": "边沿", "kind": "chips",
             "options": _opts(("POS", "上升沿"), ("NEG", "下降沿"), ("BOTH", "双沿"))},
            {"key": "level", "label": "电平", "kind": "number", "unit": "V"},
        ],
        "PULSE": [
            {"key": "source", "label": "源", "kind": "chips", "options": SOURCE_3},
            {"key": "pol", "label": "极性", "kind": "chips", "options": POLARITY},
            {"key": "cond", "label": "条件", "kind": "chips", "options": COND_3},
            {"key": "w1", "label": "宽度", "labelIn": "下限", "kind": "number",
             "unit": "s", "when": {"cond": ["GT", "IN"]}},
            {"key": "w2", "label": "宽度", "labelIn": "上限", "kind": "number",
             "unit": "s", "when": {"cond": ["LT", "IN"]}},
            {"key": "level", "label": "电平", "kind": "number", "unit": "V"},
        ],
        "SLOPE": [
            {"key": "source", "label": "源", "kind": "chips", "options": SOURCE_CH},
            {"key": "pol", "label": "极性", "kind": "chips", "options": POLARITY},
            {"key": "cond", "label": "条件", "kind": "chips", "options": COND_3},
            {"key": "t1", "label": "时间", "labelIn": "下限", "kind": "number",
             "unit": "s", "when": {"cond": ["GT", "IN"]}},
            {"key": "t2", "label": "时间", "labelIn": "上限", "kind": "number",
             "unit": "s", "when": {"cond": ["LT", "IN"]}},
            {"key": "a", "label": "上电平", "kind": "number", "unit": "V"},
            {"key": "b", "label": "下电平", "kind": "number", "unit": "V"},
        ],
        "RUNT": [
            {"key": "source", "label": "源", "kind": "chips", "options": SOURCE_CH},
            {"key": "pol", "label": "极性", "kind": "chips", "options": POLARITY},
            {"key": "cond", "label": "条件", "kind": "chips", "options": COND_RUNT},
            {"key": "w1", "label": "宽度", "labelIn": "下限", "kind": "number",
             "unit": "s", "when": {"cond": ["GT", "IN"]}},
            {"key": "w2", "label": "宽度", "labelIn": "上限", "kind": "number",
             "unit": "s", "when": {"cond": ["LT", "IN"]}},
            {"key": "a", "label": "上电平", "kind": "number", "unit": "V"},
            {"key": "b", "label": "下电平", "kind": "number", "unit": "V"},
        ],
    },
}

# ── SCPI 映射 ─────────────────────────────────────────────────────────
_MODE_REV = {
    "EDGE": "EDGE", "PULS": "PULSE", "PULSE": "PULSE",
    "RUNT": "RUNT", "SLOP": "SLOPE", "SLOPE": "SLOPE",
}
_EDGE_SLOPE = {"POS": "POSitive", "NEG": "NEGative", "BOTH": "RFALl"}
_EDGE_SLOPE_REV = {"POS": "POS", "NEG": "NEG", "RFAL": "BOTH"}
_SRC_SCPI = {"CH1": "CHANnel1", "CH2": "CHANnel2", "EXT": "EXT", "AC": "ACLine"}

# 脉宽 / 斜率：极性 + 条件 → 一条 WHEN
_PULSE_WHEN = {
    ("POS", "GT"): "PGReater", ("POS", "LT"): "PLESs", ("POS", "IN"): "PGLess",
    ("NEG", "GT"): "NGReater", ("NEG", "LT"): "NLESs", ("NEG", "IN"): "NGLess",
}
_PULSE_WHEN_REV = {
    "PGR": ("POS", "GT"), "PLES": ("POS", "LT"), "PGL": ("POS", "IN"),
    "NGR": ("NEG", "GT"), "NLES": ("NEG", "LT"), "NGL": ("NEG", "IN"),
    "PGREATER": ("POS", "GT"), "PLESS": ("POS", "LT"), "PGLESS": ("POS", "IN"),
    "NGREATER": ("NEG", "GT"), "NLESS": ("NEG", "LT"), "NGLESS": ("NEG", "IN"),
}
# 欠幅：条件 → WHEN（极性是独立命令）
_RUNT_WHEN = {"NONE": "NONE", "GT": "GREater", "LT": "LESS", "IN": "GLESs"}
_RUNT_WHEN_REV = {
    "NONE": "NONE", "GRE": "GT", "LESS": "LT", "GLES": "IN",
    "GREATER": "GT", "GLESS": "IN",
}


def _mode(raw):
    t = str(raw or "").strip().upper()
    return _MODE_REV.get(t, t or None)


def _src(raw):
    t = str(raw or "").strip().upper()
    if t.startswith("CHAN"):
        digits = "".join(c for c in t if c.isdigit())
        return "CH" + (digits[:1] or "1")
    if t.startswith("EXT"):
        return "EXT"
    if t.startswith("ACL"):
        return "AC"
    return None


def _num(raw):
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _slope(raw):
    return _EDGE_SLOPE_REV.get(str(raw or "").strip().upper())


def _pulse_when(raw):
    return _PULSE_WHEN_REV.get(str(raw or "").strip().upper(), (None, None))


def _runt_when(raw):
    return _RUNT_WHEN_REV.get(str(raw or "").strip().upper())


def _pol(raw):
    t = str(raw or "").strip().upper()
    return t if t in ("POS", "NEG") else None


# ── 读 ────────────────────────────────────────────────────────────────
def read(inst):
    """读当前触发配置，统一成页面短值。读不到的项为 None，不抛。"""
    q = link.try_query
    mode = _mode(q(inst, ":TRIGger:MODE?"))

    if mode == "EDGE":
        return {
            "type": "EDGE", "supported": True,
            "source": _src(q(inst, ":TRIGger:EDGe:SOURce?")),
            "slope": _slope(q(inst, ":TRIGger:EDGe:SLOPe?")),
            "level": _num(q(inst, ":TRIGger:EDGe:LEVel?")),
        }

    if mode == "PULSE":
        pol, cond = _pulse_when(q(inst, ":TRIGger:PULSe:WHEN?"))
        return {
            "type": "PULSE", "supported": True,
            "source": _src(q(inst, ":TRIGger:PULSe:SOURce?")),
            "pol": pol, "cond": cond,
            "w1": _num(q(inst, ":TRIGger:PULSe:LWIDth?")),
            "w2": _num(q(inst, ":TRIGger:PULSe:UWIDth?")),
            "level": _num(q(inst, ":TRIGger:PULSe:LEVel?")),
        }

    if mode == "SLOPE":
        pol, cond = _pulse_when(q(inst, ":TRIGger:SLOPe:WHEN?"))
        return {
            "type": "SLOPE", "supported": True,
            "source": _src(q(inst, ":TRIGger:SLOPe:SOURce?")),
            "pol": pol, "cond": cond,
            "t1": _num(q(inst, ":TRIGger:SLOPe:TLOWer?")),
            "t2": _num(q(inst, ":TRIGger:SLOPe:TUPPer?")),
            "a": _num(q(inst, ":TRIGger:SLOPe:ALEVel?")),
            "b": _num(q(inst, ":TRIGger:SLOPe:BLEVel?")),
        }

    if mode == "RUNT":
        return {
            "type": "RUNT", "supported": True,
            "source": _src(q(inst, ":TRIGger:RUNT:SOURce?")),
            "pol": _pol(q(inst, ":TRIGger:RUNT:POLarity?")),
            "cond": _runt_when(q(inst, ":TRIGger:RUNT:WHEN?")),
            "w1": _num(q(inst, ":TRIGger:RUNT:WLOWer?")),
            "w2": _num(q(inst, ":TRIGger:RUNT:WUPPer?")),
            "a": _num(q(inst, ":TRIGger:RUNT:ALEVel?")),
            "b": _num(q(inst, ":TRIGger:RUNT:BLEVel?")),
        }

    # 面板上切到了码型 / 建立保持 / 选件类型：页面如实报，不假装是四种之一
    return {"type": mode, "supported": False}


# ── 写 ────────────────────────────────────────────────────────────────
def _fail(msg):
    return {"ok": False, "msg": msg}


def _ok(inst, rows):
    return {"ok": True, "applied": rows, "config": read(inst)}


def _v(state, key):
    """取一个必需参数；读不到就明确报出来，别把 None 发下去。"""
    value = state.get(key)
    if value is None:
        raise KeyError(key)
    return value


def _clean(field, raw):
    """按字段定义校验/归一页面传来的值，不合法返回 None。"""
    if field["kind"] == "chips":
        value = str(raw or "").strip().upper()
        return value if value in [o["value"] for o in field["options"]] else None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    if field.get("unit") == "s" and value <= 0:
        return None
    return value


def _changes(typ, field, state):
    """一个页面字段 → 实际要下发的 SCPI 列表（顺序写，先条件后数值）。"""
    if typ == "EDGE":
        if field == "source":
            return [(":TRIGger:EDGe:SOURce", _SRC_SCPI[_v(state, "source")], "触发源")]
        if field == "slope":
            return [(":TRIGger:EDGe:SLOPe", _EDGE_SLOPE[_v(state, "slope")], "边沿")]
        if field == "level":
            return [(":TRIGger:EDGe:LEVel", _v(state, "level"), "触发电平")]

    if typ == "PULSE":
        if field == "source":
            return [(":TRIGger:PULSe:SOURce", _SRC_SCPI[_v(state, "source")], "触发源")]
        if field in ("pol", "cond"):
            cond = _v(state, "cond")
            out = [(":TRIGger:PULSe:WHEN", _PULSE_WHEN[(_v(state, "pol"), cond)], "触发条件")]
            if cond in ("GT", "IN"):
                out.append((":TRIGger:PULSe:LWIDth", _v(state, "w1"), "脉宽下限"))
            if cond in ("LT", "IN"):
                out.append((":TRIGger:PULSe:UWIDth", _v(state, "w2"), "脉宽上限"))
            return out
        if field == "w1" and state.get("cond") in ("GT", "IN"):
            return [(":TRIGger:PULSe:LWIDth", _v(state, "w1"), "脉宽下限")]
        if field == "w2" and state.get("cond") in ("LT", "IN"):
            return [(":TRIGger:PULSe:UWIDth", _v(state, "w2"), "脉宽上限")]
        if field == "level":
            return [(":TRIGger:PULSe:LEVel", _v(state, "level"), "触发电平")]

    if typ == "SLOPE":
        if field == "source":
            return [(":TRIGger:SLOPe:SOURce", _SRC_SCPI[_v(state, "source")], "触发源")]
        if field in ("pol", "cond"):
            cond = _v(state, "cond")
            out = [(":TRIGger:SLOPe:WHEN", _PULSE_WHEN[(_v(state, "pol"), cond)], "触发条件")]
            if cond in ("GT", "IN"):
                out.append((":TRIGger:SLOPe:TLOWer", _v(state, "t1"), "时间下限"))
            if cond in ("LT", "IN"):
                out.append((":TRIGger:SLOPe:TUPPer", _v(state, "t2"), "时间上限"))
            return out
        if field == "t1" and state.get("cond") in ("GT", "IN"):
            return [(":TRIGger:SLOPe:TLOWer", _v(state, "t1"), "时间下限")]
        if field == "t2" and state.get("cond") in ("LT", "IN"):
            return [(":TRIGger:SLOPe:TUPPer", _v(state, "t2"), "时间上限")]
        if field == "a":
            return [(":TRIGger:SLOPe:ALEVel", _v(state, "a"), "上电平")]
        if field == "b":
            return [(":TRIGger:SLOPe:BLEVel", _v(state, "b"), "下电平")]

    if typ == "RUNT":
        if field == "source":
            return [(":TRIGger:RUNT:SOURce", _SRC_SCPI[_v(state, "source")], "触发源")]
        if field == "pol":
            return [(":TRIGger:RUNT:POLarity", "POSitive" if _v(state, "pol") == "POS"
                     else "NEGative", "脉宽极性")]
        if field == "cond":
            cond = _v(state, "cond")
            out = [(":TRIGger:RUNT:WHEN", _RUNT_WHEN[cond], "触发条件")]
            if cond in ("GT", "IN"):
                out.append((":TRIGger:RUNT:WLOWer", _v(state, "w1"), "脉宽下限"))
            if cond in ("LT", "IN"):
                out.append((":TRIGger:RUNT:WUPPer", _v(state, "w2"), "脉宽上限"))
            return out
        if field == "w1" and state.get("cond") in ("GT", "IN"):
            return [(":TRIGger:RUNT:WLOWer", _v(state, "w1"), "脉宽下限")]
        if field == "w2" and state.get("cond") in ("LT", "IN"):
            return [(":TRIGger:RUNT:WUPPer", _v(state, "w2"), "脉宽上限")]
        if field == "a":
            return [(":TRIGger:RUNT:ALEVel", _v(state, "a"), "上电平")]
        if field == "b":
            return [(":TRIGger:RUNT:BLEVel", _v(state, "b"), "下电平")]

    return []


def apply(inst, payload):
    """页面的一次改动 → 切类型 / 改一个字段，逐条读原值后下发。"""
    typ = str(payload.get("type") or "").strip().upper()
    if typ not in TYPE_ORDER:
        return _fail("不支持的触发类型：%s" % (typ or "（空）"))

    rows = []
    field = payload.get("field")
    if not field:                                   # 只切类型
        rows += link.apply(inst, [(":TRIGger:MODE", TYPE_SCPI[typ], "触发类型")])
        return _ok(inst, rows)

    fields = {f["key"]: f for f in SCHEMA["fields"][typ]}
    if field not in fields:
        return _fail("「%s」没有「%s」这个参数" % (TYPE_LABEL[typ], field))

    cur = read(inst)
    if cur.get("type") != typ:                      # 面板上被切走了，先切回来
        rows += link.apply(inst, [(":TRIGger:MODE", TYPE_SCPI[typ], "触发类型")])
        cur = read(inst)
        if cur.get("type") != typ:
            return _fail("切换触发类型失败，未继续下发")

    value = _clean(fields[field], payload.get("value"))
    if value is None:
        return _fail("参数值无效：%s" % payload.get("value"))

    state = dict(cur)
    state[field] = value
    try:
        changes = _changes(typ, field, state)
    except KeyError as exc:
        return _fail("读不到仪器当前参数（%s），未下发" % exc)
    if changes:
        rows += link.apply(inst, changes)
    return _ok(inst, rows)


# ── DEMO：只在内存里改，不碰仪器 ──────────────────────────────────────
DEMO_DEFAULTS = {
    "EDGE": {"source": "CH1", "slope": "POS", "level": 0.0},
    "PULSE": {"source": "CH1", "pol": "POS", "cond": "GT",
              "w1": 1e-6, "w2": 2e-6, "level": 0.0},
    "SLOPE": {"source": "CH1", "pol": "POS", "cond": "GT",
              "t1": 1e-6, "t2": 2e-6, "a": 1.0, "b": 0.0},
    "RUNT": {"source": "CH1", "pol": "POS", "cond": "NONE",
             "w1": 1e-6, "w2": 2e-6, "a": 1.0, "b": 0.0},
}


def demo_default():
    return {"type": "EDGE", "values": {t: dict(DEMO_DEFAULTS[t]) for t in TYPE_ORDER}}


def demo_read(state):
    typ = state.get("type") or "EDGE"
    out = {"type": typ, "supported": typ in TYPE_ORDER}
    out.update(state["values"].get(typ, {}))
    return out


def demo_apply(state, payload):
    typ = str(payload.get("type") or "").strip().upper()
    if typ not in TYPE_ORDER:
        return _fail("不支持的触发类型：%s" % (typ or "（空）"))

    field = payload.get("field")
    if not field:
        old = state.get("type")
        state["type"] = typ
        return {"ok": True, "config": demo_read(state),
                "applied": [{"cmd": ":TRIGger:MODE", "old": old, "new": typ}]}

    fields = {f["key"]: f for f in SCHEMA["fields"][typ]}
    if field not in fields:
        return _fail("「%s」没有「%s」这个参数" % (TYPE_LABEL[typ], field))
    value = _clean(fields[field], payload.get("value"))
    if value is None:
        return _fail("参数值无效：%s" % payload.get("value"))

    values = state["values"][typ]
    old = values.get(field)
    values[field] = value
    return {"ok": True, "config": demo_read(state),
            "applied": [{"cmd": field, "old": old, "new": value}]}
