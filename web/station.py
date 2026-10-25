# -*- coding: utf-8 -*-
"""仪器会话 —— 真机 / DEMO 双模，全部 SCPI 都过 `scope/` 的闸门。

一台仪器只有一条 VXI-11 会话，网页端 / 命令行 / MCP **同一时间只许一个连**；
Flask 是多线程的，所以这里用一把锁把请求串起来，谁也别抢仪器。
"""
import math
import os
import random
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)                        # 与 scopecli 共用同一个采集库

from scope import link, wave                    # noqa: E402
from web import config as cfg                   # noqa: E402
from web import cursor                          # noqa: E402
from web import trigger                         # noqa: E402

INVALID = 1e30                                  # 仪器用 9.9e37 表示"读数无效"


def _finite(x):
    return isinstance(x, (int, float)) and x == x and abs(x) < INVALID


def _try(inst, cmd):
    """只读查询，读不到就返回 None —— 闸门拒了就拒了，页面显示「—」，绝不硬发。"""
    try:
        return link.q(inst, cmd)
    except Exception:
        return None


def _num(text):
    """SCPI 回的数字串转 float —— 读不到 / 格式不对都回 None，别让画图拿到 NaN。"""
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


RIGOL_OUI = "0019af"                            # 仪器网卡 OUI（实测 00-19-af-28-70-2a）
_ARP_LINE = re.compile(r"(\d+\.\d+\.\d+\.\d+)\D+((?:[0-9a-fA-F]{1,2}[-:]){5}[0-9a-fA-F]{1,2})")


def _arp_scope_ips():
    """本机 ARP 表里属于仪器的 IPv4，按 RIGOL 的网卡 OUI 认。

    仪器开着 Auto-IP，地址会漂（2026-10-15 实测当天 .112 → .113）：config.ini 里
    钉死一个地址，漂走就永远连不上、页面静默掉进 DEMO。ARP 表是免费的实时邻居信息，
    按厂商前缀一筛就能把它认回来。
    """
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                             timeout=5, errors="replace").stdout
    except Exception:
        return []
    ips = []
    for ip, mac in _ARP_LINE.findall(out or ""):
        if "".join(c for c in mac.lower() if c.isalnum()).startswith(RIGOL_OUI) \
                and ip not in ips:
            ips.append(ip)
    return ips


def _sweep_scope_ips(timeout=0.15):
    """并行扫 169.254.43.1-254 的 111 端口（VXI-11 portmap），谁应答谁是仪器。

    端口扫描不看历史，纯按「111 开着」认 —— ARP 表里从没出现过的地址也抓得到。
    64 线程 × 0.15 s 超时，整段扫完约 0.6 s。实测两天的仪器地址都在 43 段
    （.112 / .113），漂出这段就先靠 ARP，真漂没了再扩段。
    """
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def probe(ip):
        s = socket.socket()
        s.settimeout(timeout)
        try:
            s.connect((ip, 111))
            return ip
        except OSError:
            return None
        finally:
            s.close()

    hosts = ["169.254.43.%d" % n for n in range(1, 255)]
    with ThreadPoolExecutor(max_workers=64) as pool:
        return [ip for ip in pool.map(probe, hosts) if ip]


class Station:
    """示波器会话（真机 / DEMO 双模）。"""

    def __init__(self, ip=None):
        self.lock = threading.Lock()
        self.inst = None
        self.mode = "demo"
        self.ip = ip or cfg.SCOPE_IP
        self.idn = "DEMO（未连真机）"
        self.link_err = None                    # 真机会话当前的问题，None = 正常
        self._last_connect_try = 0.0            # 上次尝试（重）连的时刻
        self.force_demo = False                 # --demo 强制仿真：永不碰仪器，连自愈也不许碰
        self._demo_trig = trigger.demo_default()  # DEMO 下的触发状态（内存）
        self._demo_cursor = cursor.demo_default()  # DEMO 下的光标状态（内存）
        self._demo_chans = {ch: {"display": "1", "scale": 1.0, "offset": 0.0}
                            for ch in cfg.CHANS}  # DEMO 下的通道档位/偏移

    # ── 连接 ────────────────────────────────────────────────────────
    def connect(self, force_demo=False):
        """连仪器：快候选（配置 → ARP 认回 → Auto-IP 兜底）全灭，再扫端口。

        Auto-IP 漂走时只钉一个地址的后果：连不上 → 降级 DEMO → 截图落假图，
        而页面看着"记录成功"，人只会以为记录坏了（2026-10-15 就这么踩了一次）。
        """
        if force_demo:
            return False
        if self._try_candidates(self._candidates(), first_tries=3):
            return True
        return self._try_candidates(_sweep_scope_ips())

    def _try_candidates(self, ips, first_tries=1):
        """逐个地址试到连上为止。

        第一个地址给足重试（首次连接要先 ARP 解析，见 link.connect）；其余只试
        一次，免得仪器不在时每个地址都拖满 3 轮。
        """
        for i, ip in enumerate(ips):
            try:
                inst = link.connect(ip, tries=first_tries if i == 0 else 1)
                self.idn = link.q(inst, "*IDN?").strip()
                link.drain(inst)
                self.inst = inst
                self.ip = ip
                self.mode = "live"
                self.link_err = None
                return True
            except Exception as exc:
                self.mode = "demo"
                self.idn = "DEMO（连不上 %s：%s）" % (ip, exc)
                self.link_err = str(exc)
        return False

    def _candidates(self):
        """仪器候选地址：配置地址优先，再补 ARP 表里的仪器，最后兜底 Auto-IP。"""
        out = [self.ip]
        for ip in _arp_scope_ips() + [link.AUTO_IP]:
            if ip and ip not in out:
                out.append(ip)
        return out

    # ── 会话自愈 ────────────────────────────────────────────────────
    def _maybe_reconnect(self):
        """DEMO 态下每 30 秒摸一次仪器：通了就自动转真机。

        服务先起、仪器后上电（或中途断电重启）是现场的常态 ——
        没有这一步，启动时连不上就永远是 DEMO，只能重启服务。
        30 秒一次是给仪器留喘息：连接尝试本身也是流量，别贴着仪器轮。
        force_demo（--demo）时绝不摸：强制仿真就是完全不碰仪器。
        """
        if self.force_demo or self.mode == "live" \
                or time.time() - self._last_connect_try < 30.0:
            return
        self._last_connect_try = time.time()
        self.connect()

    def _drop(self):
        """关掉当前会话；已经死掉的会话 close() 也可能抛，忽略即可。"""
        try:
            if self.inst is not None:
                self.inst.close()
        except Exception:
            pass
        self.inst = None

    def _ensure_link(self):
        """真机模式下每个操作前先确认会话还在，不在就重开一条。

        仪器虽然支持多条 VXI-11 链路，但**互相会挤**（命令行 / MCP / 别的工具
        一进来，网页这条就被踢掉），仪器重启也一样。原先没有这一步：会话一死，
        之后每查必败，而 `mode` 还停在 live，页面就把「读不到」显示成「？」
        —— 看着像"仪器没冻结"，实际是链路断了。
        """
        if self.mode != "live":
            return self.link_err
        if self.inst is not None:
            try:
                link.q(self.inst, "*IDN?")
                self.link_err = None
                return None
            except Exception as exc:
                self.link_err = "会话已断（%s）" % exc
        self._drop()
        # 跟 demo 侧同一规矩：30 秒摸一次 —— 连接尝试本身也是流量，别贴着仪器轮
        if time.time() - self._last_connect_try < 30.0:
            return self.link_err
        self._last_connect_try = time.time()
        try:
            self.inst = link.connect(self.ip)
            link.drain(self.inst)
            self.idn = link.q(self.inst, "*IDN?").strip()
            self.link_err = None
        except Exception as exc:
            self.link_err = "重连失败（%s）" % exc
            self.connect()          # 旧地址连不上就走完整找回链（ARP / 扫端口），漂走了也跟得过去
        return self.link_err

    # ── 波形快照：波形必给，测量值可选（网页只看波形，就不去点亮仪器测量区）──
    def snapshot(self, chan, with_meas=True):
        with self.lock:
            self._maybe_reconnect()
            if self.mode == "live":
                self._ensure_link()
                if self.link_err is None:
                    try:
                        return self._live(chan, with_meas)
                    except Exception as exc:
                        self.link_err = "采集失败（%s）" % exc
                # 真机读不到就如实报空，不拿仿真波形顶上 —— 一条假的方波比空屏危险得多
                return {"ok": False, "mode": "offline",
                        "ts": time.time(), "msg": self.link_err,
                        "measurements": {}, "waveform": None}
            return self._demo(chan)

    def _live(self, chan, with_meas=True):
        meas = {}
        if with_meas:
            for key, _label, cmd, _unit, _digits in cfg.ITEMS:
                try:
                    v = float(link.q(self.inst, "%s %s" % (cmd, chan)))
                except Exception:
                    v = float("nan")
                meas[key] = v if _finite(v) else float("nan")
        chans = {}
        bad = []
        for ch in cfg.CHANS:            # 双通道都取：首页同屏对比
            t0, dt, v = wave.fetch(self.inst, ch)
            if wave.screen_data_broken(v):
                bad.append(ch)
            chans[ch] = {"n": len(v), "dt": dt, "t0": t0, "v": [float(x) for x in v]}
        return {
            "ok": True, "mode": "live", "chan": chan, "ts": time.time(),
            "measurements": meas,
            "waveform": chans,
            "wave_bad": bad,            # 存储深度 AUTO 时的坏帧，页面不画、直接提示
        }

    def _demo(self, chan):
        """仿真数据：没接仪器也能把界面（和记录流程）跑通。"""
        t = time.time()
        freq = 50.0 + 3.0 * math.sin(t / 8.0)
        per = 1.0 / freq
        duty = 0.5 + 0.004 * math.sin(t / 3.0)
        hi, lo = 3.32, 0.02
        n = 900
        dt = per * 3.5 / n
        volts, clean = [], []
        for i in range(n):
            ph = (i * dt / per) % 1.0
            lvl = hi if ph < duty else lo
            volts.append(lvl + random.uniform(-0.012, 0.012))
            clean.append(lvl)               # 第二路 = 整形后的干净方波（无噪）
        return {
            "ok": True, "mode": "demo", "chan": chan, "ts": t,
            "measurements": {
                "freq": freq, "period": per, "vpp": hi - lo, "vtop": hi, "vbase": lo,
                "pduty": duty * 100, "rtime": 1.2e-6 + random.uniform(-0.05e-6, 0.05e-6),
            },
            "waveform": {
                cfg.CHANS[0]: {"n": n, "dt": dt, "t0": 0.0, "v": volts},
                cfg.CHANS[1]: {"n": n, "dt": dt, "t0": 0.0, "v": clean},
            },
        }

    # ── 仪器状态（只读）────────────────────────────────────────────
    def state(self, full=False):
        """full=False 只查触发状态 + 错误队列（记录页轮询用，最省）。"""
        with self.lock:
            self._maybe_reconnect()
            if self.mode != "live":
                return self._demo_state(full)
            self._ensure_link()
            trig = (_try(self.inst, ":TRIGger:STATus?") or "").strip()
            out = {
                "mode": "live" if self.link_err is None else "offline",
                "link_err": self.link_err,
                "idn": self.idn, "ip": self.ip,
                "trig": {"raw": trig, "cn": link.trig_status_cn(trig)},
                "err": _try(self.inst, ":SYSTem:ERRor?"),
                "record_dir": link.record_dir(create=False),
            }
            if not full:
                return out
            chans = {}
            for ch in cfg.CHANS:
                chans[ch] = {
                    "display": _try(self.inst, ":%s:DISPlay?" % ch),
                    "scale": _try(self.inst, ":%s:SCALe?" % ch),
                    "offset": _try(self.inst, ":%s:OFFSet?" % ch),
                }
            out["channels"] = chans
            out["timebase"] = _try(self.inst, ":TIMebase:MAIN:SCALe?")
            out["timebase_offset"] = _try(self.inst, ":TIMebase:MAIN:OFFSet?")
            return out

    # ── 波形页画图用的真实档位 ──────────────────────────────────────
    def wave_setup(self):
        """时基 + 每通道 scale/offset/开关 —— 纯查询，网页每 5 秒问一次。

        横向位置不查 :TIMebase:MAIN:OFFSet：每帧波形自带的 t0 已经把偏移算进去了，
        少问一条就少占一份会话。
        """
        with self.lock:
            self._maybe_reconnect()
            if self.mode != "live":
                return {"mode": "demo", "timebase": 5e-03,
                        "channels": {ch: dict(v) for ch, v in self._demo_chans.items()},
                        "trig": trigger.demo_read(self._demo_trig),
                        "cursor": cursor.demo_read(self._demo_cursor)}
            self._ensure_link()
            chans = {}
            for ch in cfg.CHANS:
                chans[ch] = {
                    "display": _try(self.inst, ":%s:DISPlay?" % ch),
                    "scale": _num(_try(self.inst, ":%s:SCALe?" % ch)),
                    "offset": _num(_try(self.inst, ":%s:OFFSet?" % ch)),
                }
            return {"mode": "live" if self.link_err is None else "offline",
                    "timebase": _num(_try(self.inst, ":TIMebase:MAIN:SCALe?")),
                    "channels": chans,
                    "trig": trigger.read(self.inst),
                    "cursor": cursor.read(self.inst)}

    def trigger_set(self, payload):
        """网页改触发：切类型或改一个字段。真机走闸门 + 先读原值，DEMO 只改内存。"""
        with self.lock:
            self._maybe_reconnect()
            if self.mode != "live":
                return trigger.demo_apply(self._demo_trig, payload)
            self._ensure_link()
            if self.link_err is not None:
                return {"ok": False, "msg": "读不到仪器：%s" % self.link_err}
            try:
                return trigger.apply(self.inst, payload)
            except Exception as exc:
                return {"ok": False, "msg": "%s: %s" % (type(exc).__name__, exc)}

    def channel_set(self, payload):
        """网页拖波形：改通道垂直偏移（B 级写，link.apply 先读原值）。"""
        chan = payload.get("chan")
        if chan not in cfg.CHANS:
            return {"ok": False, "msg": "通道不对：%s" % chan}
        try:
            off = float(payload.get("offset"))
        except (TypeError, ValueError):
            return {"ok": False, "msg": "偏移值不对：%s" % payload.get("offset")}
        with self.lock:
            self._maybe_reconnect()
            if self.mode != "live":
                old = self._demo_chans[chan]["offset"]
                self._demo_chans[chan]["offset"] = off
                return {"ok": True, "offset": off,
                        "applied": [{"cmd": ":%s:OFFSet" % chan,
                                     "old": old, "new": off, "unit": "V"}]}
            self._ensure_link()
            if self.link_err is not None:
                return {"ok": False, "msg": "读不到仪器：%s" % self.link_err}
            try:
                rows = link.apply(self.inst, [(":%s:OFFSet" % chan, off, "V")])
                return {"ok": True, "offset": off, "applied": rows}
            except Exception as exc:
                return {"ok": False, "msg": "%s: %s" % (type(exc).__name__, exc)}

    def cursor_set(self, payload):
        """网页切光标模式 / 拖光标：B 级写，link.apply 先读原值。"""
        with self.lock:
            self._maybe_reconnect()
            if self.mode != "live":
                return cursor.demo_apply(self._demo_cursor, payload)
            self._ensure_link()
            if self.link_err is not None:
                return {"ok": False, "msg": "读不到仪器：%s" % self.link_err}
            try:
                return cursor.apply(self.inst, payload)
            except Exception as exc:
                return {"ok": False, "msg": "%s: %s" % (type(exc).__name__, exc)}

    def trig(self):
        """只查触发状态（RUN / STOP）—— 比 state 少一条错误队列查询。"""
        with self.lock:
            self._maybe_reconnect()
            if self.mode != "live":
                return {"mode": "demo", "raw": "STOP", "cn": link.trig_status_cn("STOP")}
            self._ensure_link()
            raw = (_try(self.inst, ":TRIGger:STATus?") or "").strip()
            return {"mode": "live" if self.link_err is None else "offline",
                    "raw": raw, "cn": link.trig_status_cn(raw)}

    def _demo_state(self, full):
        out = {
            "mode": "demo", "link_err": None, "idn": self.idn, "ip": self.ip,
            "trig": {"raw": "STOP", "cn": link.trig_status_cn("STOP")},
            "err": '+0,"No error"',
            "record_dir": link.record_dir(create=False),
        }
        if not full:
            return out
        out["channels"] = {
            "CHANnel1": {"display": "1", "scale": "1.000000E+00", "offset": "0.000000E+00"},
            "CHANnel2": {"display": "1", "scale": "1.000000E+00", "offset": "0.000000E+00"},
        }
        out["timebase"] = "5.000000E-05"
        out["timebase_offset"] = "0.000000E+00"
        return out

    # ── 记录（只读，不改任何设置）──────────────────────────────────
    def record(self, tag):
        """记录 = 截图 + 双通道屏幕波形 CSV。

        流程约定：操作员先在面板按 STOP 冻结 → 这里才记录 → 记完按 RUN。
        不查测量项：`:MEASure:*?` 会把测量项"启用"到屏幕下方，顶掉当前显示。
        """
        with self.lock:
            if self.mode != "live":
                return self._demo_record(tag)
            self._ensure_link()
            if self.link_err is not None:
                return {"ok": False, "trig": "", "msg": "读不到仪器：%s" % self.link_err}
            trig = link.q(self.inst, ":TRIGger:STATus?").strip()
            if trig != "STOP":
                return {"ok": False, "trig": trig,
                        "msg": "仪器处于 %s（未冻结）。先在面板按 STOP 冻结画面再记录。" % trig}
            files = [link.screenshot(self.inst, tag=tag)]
            for ch in cfg.CHANS:
                files.append(self._csv(ch, tag))
            return self._done(files, trig)

    def shot(self, tag):
        """只截图（只读）。"""
        with self.lock:
            if self.mode != "live":
                return self._demo_record(tag, shot_only=True)
            self._ensure_link()
            if self.link_err is not None:
                return {"ok": False, "msg": "读不到仪器：%s" % self.link_err}
            return self._done([link.screenshot(self.inst, tag=tag)], None)

    def _csv(self, chan, tag):
        t0, dt, v = wave.fetch(self.inst, chan)
        name = "wave_%s_%s_%s.csv" % (chan, tag, link.stamp()) if tag \
            else "wave_%s_%s.csv" % (chan, link.stamp())
        return link.write_csv(os.path.join(link.record_dir(), name), t0, dt, v)

    def _done(self, files, trig):
        return {"ok": True, "trig": trig, "record_dir": link.record_dir(create=False),
                "files": [_file_info(p) for p in files]}

    def _demo_record(self, tag, shot_only=False):
        """DEMO 不碰仪器：落一份带 demo- 前缀的假记录，只为把界面流程走通。"""
        t0, dt, v = 0.0, 1e-5, self._demo(cfg.CHANS[0])["waveform"][cfg.CHANS[0]]["v"]
        files = []
        if not shot_only:
            for ch in cfg.CHANS:
                name = "demo-wave_%s_%s_%s.csv" % (ch, tag or "untagged", link.stamp())
                files.append(link.write_csv(os.path.join(link.record_dir(), name), t0, dt, v))
        png = os.path.join(link.record_dir(), "demo-scope_%s%s.png" % (
            link.stamp(), ("_" + tag) if tag else ""))
        try:
            from PIL import Image, ImageDraw, ImageFont
            try:
                big = ImageFont.load_default(size=30)
                small = ImageFont.load_default(size=18)
            except TypeError:                   # 老 pillow 的 load_default() 不收 size
                big = small = ImageFont.load_default()
            # 底色反过来（真机屏幕是深色）+ 纯英文：PIL 默认点阵字体没有中文字形，
            # 写中文只会渲染成方块，整张图看着就是"一张黑图"，容易被当成截图坏了。
            img = Image.new("RGB", (800, 480), (255, 244, 214))
            d = ImageDraw.Draw(img)
            d.text((28, 24), "DEMO - NO INSTRUMENT", fill=(160, 60, 0), font=big)
            d.text((28, 64), "placeholder image, not a scope screenshot",
                   fill=(120, 90, 60), font=small)
            d.text((28, 90), "tag: %s" % (tag or "(untagged)"),
                   fill=(120, 90, 60), font=small)
            d.line((60, 300, 740, 300), fill=(205, 170, 120), width=2)
            img.save(png)
            files.append(png)
        except ImportError:
            pass                                # 没装 pillow 就只留 CSV
        return self._done(files, "STOP")


def _file_info(path):
    try:
        st = os.stat(path)
        return {"name": os.path.basename(path), "path": path,
                "size": st.st_size, "mtime": st.st_mtime}
    except OSError:
        return {"name": os.path.basename(path), "path": path, "size": 0, "mtime": 0}


STATION = Station()
