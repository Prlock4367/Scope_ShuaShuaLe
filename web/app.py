# -*- coding: utf-8 -*-
"""Scope_ShuaShuaLe 网页端入口。

    python web/app.py                 # 连真机（连不上自动降级 DEMO，界面照常看）
    python web/app.py --demo          # 强制 DEMO，不碰仪器（演示 / 没仪器时用）
    python web/app.py --port 8760     # 换端口
    python web/app.py --host 0.0.0.0  # 允许同网段别的机器打开看（默认只本机）

依赖：flask（+ 连真机才要 pyvisa / pyvisa-py；看波形统计要 numpy）。
"""
import argparse
import logging
import math
import os
import sys
from logging.handlers import RotatingFileHandler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from flask import Flask                              # noqa: E402
from flask.json.provider import DefaultJSONProvider   # noqa: E402

from web import config as cfg                        # noqa: E402


def _json_safe(obj):
    """把 nan / ±inf 换成 None —— 递归走遍 dict / list。

    仪器读数无效时就是 nan，而 Python 的 json 默认把它写成裸 `NaN`，
    **那不是合法 JSON**：浏览器 `res.json()` 直接抛错，页面静默停止刷新。
    """
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


class SafeJSON(DefaultJSONProvider):
    """默认 JSON 提供者 + 一道体检：绝不发出 NaN / Infinity。"""

    def dumps(self, obj, **kwargs):
        kwargs.setdefault("allow_nan", False)
        return super().dumps(_json_safe(obj), **kwargs)


def create_app():
    app = Flask(__name__)
    app.json = SafeJSON(app)

    from scope import link
    link.set_record_root(cfg.RECORD_ROOT)            # 记录落哪，由 config.ini 说了算
    os.makedirs(cfg.RECORD_ROOT, exist_ok=True)

    from web.routes import bp
    app.register_blueprint(bp)

    @app.after_request
    def _no_store(resp):
        """页面和 API 都不缓存。

        页面不缓存：本地工具改完要马上看到新的，别让浏览器拿旧 HTML 配新 JS。
        API 不缓存：每次启动/刷新必须读到仪器当下的真实状态，不能拿上次的 JSON。
        """
        if resp.mimetype in ("text/html", "application/json"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def _assets():
        """静态文件带 mtime 版本号：改了文件 URL 就变，浏览器不会继续用旧的。"""
        def asset_v(name):
            path = os.path.join(app.static_folder or "", name)
            try:
                return int(os.path.getmtime(path))
            except OSError:
                return 0
        return {"asset_v": asset_v}

    return app


def _setup_logging():
    log_dir = os.path.join(ROOT, "logs")
    os.makedirs(log_dir, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    rolling = RotatingFileHandler(os.path.join(log_dir, "scope.log"),
                                  maxBytes=5 * 1024 * 1024, backupCount=3,
                                  encoding="utf-8")
    rolling.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[stream, rolling])


def _rescue_stdio():
    """pythonw（无窗）启动时 stdout / stderr 是 None：print 静默丢弃、报错无踪。

    这里把它们接回 logs/web.log —— 后台跑的服务崩了才有据可查。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    log = open(os.path.join(ROOT, "logs", "web.log"), "a",
               buffering=1, encoding="utf-8", errors="replace")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def main(argv=None):
    _rescue_stdio()
    ap = argparse.ArgumentParser(description="示波器刷刷乐 · 网页控制台")
    ap.add_argument("--demo", action="store_true", help="强制 DEMO，不碰仪器")
    ap.add_argument("--port", type=int, default=cfg.PORT)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--open", action="store_true",
                    help="启动后自动打开浏览器（双击启动脚本用）")
    args = ap.parse_args(argv)

    _setup_logging()
    from web.station import STATION
    STATION.force_demo = bool(args.demo)     # --demo 是硬承诺：自愈也绝不碰仪器
    live = False if args.demo else STATION.connect()

    app = create_app()
    print("=" * 60)
    print("  示波器刷刷乐 · 网页控制台")
    print("  模式: %s" % ("真机 %s（%s）" % (STATION.ip, STATION.idn) if live
                          else "DEMO（仿真数据，不碰仪器）"))
    print("  地址: http://127.0.0.1:%d" % args.port)
    print("  记录: %s" % cfg.RECORD_ROOT)
    print("=" * 60)

    if args.open:
        # 服务要一点时间才起来，等 1.5 秒再开浏览器；flask 关闭了 reloader，
        # 这里只会触发一次，不会开两个标签页。
        import threading
        import webbrowser
        host = "127.0.0.1" if args.host in ("0.0.0.0", "127.0.0.1") else args.host
        url = "http://%s:%d/" % (host, args.port)
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
