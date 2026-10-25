# -*- coding: utf-8 -*-
"""统一返回结构 —— **从第一天定死，改了所有下游全改**。

设计依据：
    每个测量值都带 `raw` 和 `valid` —— 参考 scopebench-mcp 的证据包格式。

本项目已有的对应物：`link.INVALID = 1e30` 判仪器返回的 9.9e37 无效哨兵，
但**没对外暴露成字段**。

为什么必须暴露：
    AI 看到 `valid: false` → 知道"这项测不了"，会跳过或换方法
    AI 看到 `9.9e37`      → 可能拿去做算术，得出荒谬结论
    ——「读数 → 可信的读数 → 结论」是三级，不能跳。

两份数据：结构化给机器，摘要给人和模型读。
"""
import math

INVALID = 1e30                  # 与 link.INVALID 保持一致


def finite(x):
    """读数是否有效。仪器用 9.9e37 表示"测不到"，link.INVALID 之上即视为无效。"""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return False
    return v == v and abs(v) < INVALID


def value(key, raw, unit="", label=""):
    """单个测量值 → 带 raw / valid 的证据条目。

    raw    仪器原始回值（SCPI 一律回字符串，可能无效）
    valid  是否可信 —— **这是给模型看的**

    三种情况要分开（前两种是真机踩出来的坑）：
      · **数值型**（档位回 "1.000000e+00"）—— 必须转成 float，
        否则模型拿到的是字符串 "1.000000e+00" 而不是 1.0，没法算。
        仪器回 9.9e37 哨兵 = 测不到 = valid:false、value:null。
      · **字符串型**（耦合 "DC"、带宽限制 "OFF"）—— 本身就是有效读数，
        没有"无效"一说。若沿用数值判定会得到 `{"value":"DC","valid":false}`
        这种自相矛盾的输出，模型会误以为读不到耦合。
      · **开关型**（显示回 "1"/"0"）—— 按数值处理，但语义是布尔。

    判定顺序很重要：**先试数值，再当字符串** —— 因为 "1" 既是有效数值
    也是有效字符串，而 "DC" 只能当字符串。
    """
    text = raw.strip() if isinstance(raw, str) else raw
    if isinstance(text, str) and not text:
        return {"key": key, "label": label, "value": None, "unit": unit,
                "raw": None, "valid": False}

    n = _as_float(text)
    if n is not None:                                  # 数值型
        ok = abs(n) < INVALID
        return {"key": key, "label": label, "value": n if ok else None,
                "unit": unit, "raw": n, "valid": ok}

    return {"key": key, "label": label, "value": text, "unit": unit,
            "raw": text, "valid": True}                # 字符串型


def _as_float(v):
    """能转成 float 就转，不能就返回 None。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def ok(**kw):
    """成功结果。永远带 ok=True，模型据此判断分支。"""
    d = {"ok": True}
    d.update(kw)
    return d


def fail(reason, hint=""):
    """失败结果。**带 hint** —— 错误必须能教模型下一步怎么做。"""
    return {"ok": False, "reason": reason, "hint": hint}


def stamp():
    """查询时刻。仪器状态类返回值**必须**带 —— 硬规则：
    「判断仪器现状只认实时读数」，历史截图不代表现在。"""
    import datetime
    return datetime.datetime.now().isoformat(timespec="seconds")


def summary(values, n=None, dt=None, span=None, csv_path=None, capture_id=None):
    """波形摘要 —— **只回标量 + 路径，原始数组绝不进上下文**。

    依据：Google API 设计的 "Reference over Value" 原则。
    反例：同行把 1400 点浮点数组直接回给模型 = 上下文爆炸。

    values 每项可以是裸数（自动判有效）或 fmt.value() 的 dict。
    """
    d = ok(capture_id=capture_id, csv_path=csv_path, points=n, dt=dt, span=span)
    for k, item in values.items():
        if isinstance(item, dict):
            d[k] = item.get("value")
            d[k + "_valid"] = bool(item.get("valid", False))
        else:
            n_ = _as_float(item)
            d[k] = n_ if (n_ is not None and abs(n_) < INVALID) else None
            d[k + "_valid"] = d[k] is not None
    d["ts"] = stamp()
    return d
