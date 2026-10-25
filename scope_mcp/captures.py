# -*- coding: utf-8 -*-
"""抓取记录（capture handle）—— handle pattern 的落地。

    第 1 步  scope_capture(chan="CHANnel2")
            → 返回 {"capture_id": "20260924-134500-CHANnel2", "csv_path": "...", ...}
    第 2 步  scope_analyze(capture_id="20260924-134500-CHANnel2", kind="spectrum")
            → 从磁盘读回那一份数据，不重新碰仪器

为什么要 handle，为什么落磁盘
--------------------------------------------
· 协议无状态 —— 现在就用 handle，将来升级零改动
· server 重启不丢状态
· 模型看得见这个 id、能推理、能在多步之间传递
· CSV 本来就要落盘（`records/`），**文件系统就是存储**，不引数据库、不引内存字典

id 的形态：`<YYYYMMDD>-<HHMMSS>-<通道>`
    "20260924-134500-CHANnel2"
    自解释 —— 人看一眼就知道是什么时候、哪个通道。
"""
import csv
import os
import re

_ID_RE = re.compile(r"^(\d{8})-(\d{6})-(CHANnel[12])$")

# ── 会话内索引 ────────────────────────────────────────────────────────
# 只缓存"最近一次"的 id → 路径映射，**不是主要存储**。
# 主要存储是磁盘上的 CSV，重启后靠 _find_on_disk() 重新找到。
_recent = {}


def save(chan, t0, dt, volts):
    """落盘波形 → (capture_id, csv_path)。"""
    from scope import link as _link

    outdir = _link.record_dir()
    cid = "%s-%s" % (_link.stamp(), chan)
    path = os.path.join(outdir, "wave_%s.csv" % cid)
    _link.write_csv(path, t0, dt, volts)
    _recent[cid] = path
    return cid, path


def load(capture_id):
    """按 handle 读回 (t0, dt, volts)。找不到返回 None。

    查找顺序：会话索引 → 磁盘扫描。后者让 server 重启后仍能分析旧数据。
    """
    path = _recent.get(capture_id) or _find_on_disk(capture_id)
    if not path or not os.path.exists(path):
        return None
    try:
        import numpy as np
    except ImportError:
        return None

    ts, vs = [], []
    with open(path, encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)                       # 跳过表头
        for row in r:
            if len(row) < 2:
                continue
            ts.append(float(row[0]))
            vs.append(float(row[1]))
    if len(vs) < 8:
        return None
    dt = (ts[1] - ts[0]) if len(ts) > 1 else 1.0
    return ts[0], dt, np.array(vs, dtype=float)


def _find_on_disk(capture_id):
    """从 id 里的日期反推目录，扫出对应 CSV。"""
    m = _ID_RE.match(str(capture_id or ""))
    if not m:
        return None
    from scope import link as _link
    import datetime
    day = datetime.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").date()
    d = _link.record_dir(when=day, create=False)
    if not os.path.isdir(d):
        return None
    target = "wave_%s.csv" % capture_id
    for name in os.listdir(d):
        if name == target:
            return os.path.join(d, name)
    # 容错：同秒同通道可能被覆盖过，取前缀最接近的一个
    stem = "wave_%s" % capture_id
    cands = sorted(n for n in os.listdir(d) if n.startswith(stem))
    return os.path.join(d, cands[0]) if cands else None


def exists(capture_id):
    return bool(_recent.get(capture_id) or _find_on_disk(capture_id))
