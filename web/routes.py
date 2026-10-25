# -*- coding: utf-8 -*-
"""页面路由 + JSON API（页面是给人看的，API 是给 AI / 其它程序读的同一份数据）。"""
import os
import re
import subprocess
import sys

from flask import Blueprint, jsonify, render_template, request

from scope import link
from web import config as cfg
from web import cursor
from web import trigger
from web.station import STATION, _file_info

bp = Blueprint("main", __name__)

_TAG_OK = re.compile(r"[^\w\u4e00-\u9fff.-]")           # 标签要进文件名，先洗净再落盘


def _safe_tag(raw):
    """标签只留中英文、数字、点、横线 —— 防 `../` 之类跑出记录目录。"""
    return _TAG_OK.sub("_", (raw or "").strip())[:40]


# ── 页面 ────────────────────────────────────────────────────────────
@bp.get("/")
def page_index():
    return render_template("index.html", nav="index")


@bp.get("/record")
def page_record():
    return render_template("record.html", nav="record")


@bp.get("/about")
def page_about():
    return render_template("about.html", nav="about")


# ── JSON API ───────────────────────────────────────────────────────
@bp.get("/api/config")
def api_config():
    return jsonify({
        "mode": STATION.mode, "idn": STATION.idn, "scope_ip": STATION.ip,
        "chans": cfg.CHANS,
        "items": [{"key": k, "label": l, "unit": u, "digits": d}
                  for k, l, _c, u, d in cfg.ITEMS],
        "trigger": trigger.SCHEMA,
        "cursor": cursor.SCHEMA,
        "record_dir": link.record_root(),
    })


@bp.get("/api/snapshot")
def api_snapshot():
    chan = request.args.get("chan", cfg.CHANS[0])
    if chan not in cfg.CHANS:
        chan = cfg.CHANS[0]
    return jsonify(STATION.snapshot(chan, with_meas=request.args.get("meas") != "0"))


@bp.get("/api/wave-setup")
def api_wave_setup():
    """画图用的真实档位（时基 + 每通道 scale/offset/开关），只读。"""
    return jsonify(STATION.wave_setup())


@bp.get("/api/trig")
def api_trig():
    """只查 RUN / STOP，供波形页标「已冻结」。"""
    return jsonify(STATION.trig())


@bp.post("/api/trigger")
def api_trigger():
    """改触发配置：切类型 / 改一个字段。

    请求体：{"type": "EDGE"} 或 {"type": "EDGE", "field": "level", "value": 1.5}
    """
    return jsonify(STATION.trigger_set(request.get_json(silent=True) or {}))


@bp.post("/api/channel")
def api_channel():
    """拖波形改通道垂直偏移：{"chan": "CHANnel1", "offset": -1.2}。"""
    return jsonify(STATION.channel_set(request.get_json(silent=True) or {}))


@bp.post("/api/cursor")
def api_cursor():
    """光标：切关闭 / 手动，或移动一根。

    {"mode": "MANual"} · {"axis": "x", "cursor": "A", "px": 170}
    """
    return jsonify(STATION.cursor_set(request.get_json(silent=True) or {}))


@bp.post("/api/record")
def api_record():
    tag = _safe_tag((request.get_json(silent=True) or {}).get("tag"))
    return jsonify(STATION.record(tag))


@bp.post("/api/shot")
def api_shot():
    tag = _safe_tag((request.get_json(silent=True) or {}).get("tag"))
    return jsonify(STATION.shot(tag))


@bp.get("/api/state")
def api_state():
    return jsonify(STATION.state(full=request.args.get("full") == "1"))


@bp.get("/api/files")
def api_files():
    """记录根目录下所有日期文件夹里的文件，新的在前。"""
    root = link.record_root()
    rows = []
    if os.path.isdir(root):
        for day in sorted(os.listdir(root), reverse=True):
            d = os.path.join(root, day)
            if not os.path.isdir(d):
                continue
            for name in os.listdir(d):
                if name.startswith("."):
                    continue
                row = _file_info(os.path.join(d, name))
                row["day"] = day
                rows.append(row)
            if len(rows) >= 60:
                break
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return jsonify({"root": root, "files": rows[:60]})


@bp.post("/api/open-dir")
def api_open_dir():
    """让服务端（和仪器同机）把文件夹开出来。

    客户端只说「哪一天」，路径由服务端自己拼 —— 递过来的路径一律不认（防越权翻盘）。
    """
    root = os.path.abspath(link.record_root())
    day = os.path.basename(((request.get_json(silent=True) or {}).get("day") or "").strip())
    target = os.path.join(root, day) if day else root
    if os.path.commonpath([root, os.path.abspath(target)]) != root:
        return jsonify({"ok": False, "msg": "只能打开记录目录"}), 403
    try:
        os.makedirs(target, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(target)                                # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
        return jsonify({"ok": True, "dir": target})
    except Exception as exc:
        return jsonify({"ok": False, "msg": str(exc)})
