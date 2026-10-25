# -*- coding: utf-8 -*-
"""示波器链路层 —— 连接、收发、原值记录、截图、项目路径。

**所有 SCPI 收发都必须走本模块**，因为闸门挂在这里：
    · 只能发 data/ds2000e_commands.json 里核对过的命令（scope/scpi.py）
    · 红线命令直接拒绝
    · 写命令必须显式落在 B 级白名单里
    · 改设置前先记录原值（apply）

铁律（命令闸门 scope/scpi.py 的出发点）
    · 只走 VXI-11（111 端口）。裸 socket 5555 单会话无容错，发错一条查询即永久卡死
    · 仪器地址取项目根 config.ini（`SCOPE_IP` 环境变量可覆盖）；仪器开着 Auto-IP，
      地址会漂（2026-10-15 实测当天 .112 → .113），config.ini 里存的是**最近一次实测值**，
      网页端连不上时还会按网卡 MAC 去 ARP 表里把仪器找回来

pyvisa 是**延迟导入**的：只跑 DEMO / 只看界面时不要求装仪器库，
clone 下来就能把界面开起来。
"""
import configparser
import datetime
import os
import time

from . import scpi

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 项目根 = scope/ 的上一级
AUTO_IP = "169.254.43.112"      # 兜底地址，仅在 config.ini 没写 ip 时用；Auto-IP 会漂，别当固定值


def _default_ip():
    """仪器地址 —— 环境变量 `SCOPE_IP` → 项目根 `config.ini` 的 `[scope] ip` → Auto-IP 兜底。

    地址是机器相关的，落在 config.ini（不进版本库）；网页 / 命令行 / MCP **三个入口
    都从这里取**，仪器换地址（比如改成静态 IP）只改那一处，不用满仓库找。
    """
    env = os.environ.get("SCOPE_IP", "").strip()
    if env:
        return env
    cp = configparser.ConfigParser()
    cp.read(os.path.join(ROOT, "config.ini"), encoding="utf-8")
    return cp.get("scope", "ip", fallback=AUTO_IP).strip() or AUTO_IP


SCOPE_IP = _default_ip()
VISA_ADDR = "TCPIP0::%s::INSTR" % SCOPE_IP
TIMEOUT_MS = 8000               # 普通查询
SHOT_TIMEOUT_MS = 180000        # 截图 1.15 MB，VXI-11 要 30-60 s
INVALID = 1e30                  # 仪器用 9.9e37 表示"读数无效"
DIV_VERT = 8.0                  # 垂直 8 格（实测）
ADC_LEVELS = 256.0              # 8 bit

TRIG_STATUS_CN = {
    "TD": "已触发（稳）", "WAIT": "等待触发", "RUN": "运行中（未触发）",
    "AUTO": "自动（无触发事件，自由扫描）", "STOP": "已停止",
}


def patch_pyvisa_vxi11():
    """绕开 pyvisa-py 的 VXI-11 连接 bug（Windows 上偶发，真实错误被盖掉）。

    pyvisa_py/protocols/rpc.py 的 `_connect()`：
        except Exception:  sock.close(); return False
        finally:           sock.setblocking(1)     ← socket 已关，这行仍执行
    结果真实错误被 WinError 10038「在一个非套接字上尝试了一个操作」顶替，
    表现为"连接时好时坏"。这里换成阻塞式 connect + 超时，只影响本进程。
    """
    try:
        from pyvisa_py.protocols import rpc
    except ImportError:
        return
    if getattr(rpc, "_wb_patched", False):
        return

    def _connect(sock, host, port, timeout=0):
        sock.settimeout(max(timeout, 2.0))
        try:
            sock.connect((host, port))
        except OSError:
            return False
        sock.setblocking(1)
        return True

    rpc._connect = _connect
    rpc._wb_patched = True


def connect(ip=None, timeout_ms=TIMEOUT_MS, tries=3):
    """连 VXI-11，失败重试。

    已知坑：pyvisa-py 把 open_timeout 按毫秒传，折算成秒是 0.0，
    首次连接若需 ARP 解析（>0.1 s）就会抛 VI_ERROR_RSRC_NFOUND，
    但此时端口其实是通的 —— 所以 tries 必须 > 1。
    """
    import pyvisa            # 延迟导入：DEMO / 离线路径不要求装仪器库
    patch_pyvisa_vxi11()
    addr = "TCPIP0::%s::INSTR" % (ip or SCOPE_IP)
    rm = pyvisa.ResourceManager("@py")
    last = None
    for _ in range(max(1, tries)):
        try:
            inst = rm.open_resource(addr, timeout=timeout_ms)
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            # 仪器偶尔回非 ASCII 字节，pyvisa 默认 ascii 解码会直接抛 UnicodeDecodeError
            inst.encoding = "latin-1"      # 逐字节映射，永不解码失败
            return inst
        except Exception as exc:
            last = exc
            time.sleep(0.5)
    raise last


# ── 收发（全部过闸门）────────────────────────────────────────────────
def q(inst, cmd):
    """查询（A 级）。过闸门 → 发送 → 去掉首尾空白。"""
    level = scpi.guard(cmd)
    if level != "A":
        raise scpi.Denied("q() 只发查询命令，收到：%s" % cmd)
    return inst.query(cmd).strip()


def send(inst, cmd):
    """写命令（B 级）。过闸门 → 发送 → 立即查错误队列。"""
    level = scpi.guard(cmd)
    if level != "B":
        raise scpi.Denied("send() 只发写命令，收到：%s" % cmd)
    inst.write(cmd)
    check_error(inst, cmd)


def try_query(inst, cmd):
    """查询失败不抛异常，返回 None —— 用于批量采集时别被单项打断。"""
    try:
        return q(inst, cmd)
    except Exception:
        return None


def num(inst, cmd):
    """查询取浮点；无效（9.9e37）或报错 → nan。"""
    raw = try_query(inst, cmd)
    if raw is None:
        return float("nan")
    try:
        v = float(raw)
    except ValueError:
        return float("nan")
    return float("nan") if abs(v) >= INVALID else v


def check_error(inst, label=""):
    """读错误队列；有错就抛，避免在错误状态上继续操作。"""
    e = inst.query(":SYSTem:ERRor?").strip()
    if not e.startswith("0,"):
        raise RuntimeError("%s 仪器报错: %s" % (label and label + " ", e))
    return e


def drain(inst, max_read=20):
    """把错误队列读空，返回读到的错误列表。

    连接后先跑一次。队列里躺着一条旧错误时，`send()` 的检查会把账算到
    新命令头上 —— 今天就这么被误导过一次（实际是上一条查询的 -113）。
    """
    errs = []
    for _ in range(max_read):
        try:
            e = inst.query(":SYSTem:ERRor?").strip()
        except Exception as exc:
            errs.append("读取失败 %s" % type(exc).__name__)
            break
        if e.startswith("0,"):
            break
        errs.append(e)
    return errs


# ── 改设置：先记原值 ─────────────────────────────────────────────────
def apply(inst, changes, dry=False):
    """逐条下发设置，**每条先实时读原值**。

    changes: [(命令, 新值)] 或 [(命令, 新值, 单位)]
    返回 [{'cmd','old','new','unit'}]，可直接落进记录目录。

    dry=True 只打印原值，不发任何写命令。
    """
    done = []
    print("\n[下发]（原值 → 新值）" if not dry else "\n[试算]（只读原值，不下发）")
    for item in changes:
        cmd, new = item[0], item[1]
        unit = item[2] if len(item) > 2 else ""
        # 数值统一按科学计数法发；离散值（触发类型 / 源 / 边沿…）原样发
        text = ("%.6e" % new) if isinstance(new, (int, float)) \
            and not isinstance(new, bool) else str(new)
        old = try_query(inst, cmd + "?")
        row = {"cmd": cmd, "old": old, "new": new, "unit": unit}
        print("  %-24s %-16s → %-14s %s"
              % (cmd, old if old is not None else "（读不到）", text, unit))
        if not dry:
            send(inst, "%s %s" % (cmd, text))
        done.append(row)
    return done


# ── 项目路径 ─────────────────────────────────────────────────────────
def project_root():
    """项目根 —— `scope/` 包的上一级（本文件往上两级）。

    跟着本文件走：clone 到哪、文件夹怎么改名都不用改代码，也不依赖任何标记文件。
    与 `scope/scpi.py` 的"上一级"算法保持一致。
    """
    return ROOT


_RECORD_ROOT = None


def set_record_root(path):
    """覆盖记录根目录（网页端启动时从 config.ini 读入）。"""
    global _RECORD_ROOT
    _RECORD_ROOT = path


def record_root():
    """记录根目录：set_record_root() → 环境变量 SCOPE_RECORD_ROOT → 项目内 records/。

    实测数据（截图 / CSV）默认落在仓库里，现场用时建议指到仓库外的工作目录
    （网页端写 config.ini，命令行设 SCOPE_RECORD_ROOT 即可）。
    """
    return (_RECORD_ROOT or os.environ.get("SCOPE_RECORD_ROOT")
            or os.path.join(project_root(), "records"))


def record_dir(when=None, create=True):
    """<record_root>/<YYYYMMDD>-附件/ —— 图、CSV 统一落这里。"""
    d = os.path.join(record_root(),
                     (when or datetime.date.today()).strftime("%Y%m%d") + "-附件")
    if create:
        os.makedirs(d, exist_ok=True)
    return d


def stamp():
    return datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


def write_csv(path, t0, dt, volts):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["t", "v"])
        for i, v in enumerate(volts):
            w.writerow(["%.9g" % (t0 + i * dt), "%.6g" % v])
    return path


# ── 截图 ─────────────────────────────────────────────────────────────
def screenshot(inst, outdir=None, tag=""):
    """截图 → PNG。只发 `:DISPlay:DATA?` 一条只读命令。

    2026-09-30 提速：仪器在 VXI-11 建链时报 max_recv_size=1500 字节，pyvisa-py
    便按 1500 B/次分块读，1.15MB = 768 次往返 ≈ 38 s；临时调大到 1 MB 后
    同一张图 2.2 s（实机验证，字节数一致）。读完即还原，只动客户端收块大小。
    """
    old_timeout = inst.timeout
    inst.timeout = SHOT_TIMEOUT_MS
    sess = inst.visalib.sessions[inst.session]
    old_recv = getattr(sess, "max_recv_size", None)
    if old_recv is not None and old_recv < 1024 * 1024:
        sess.max_recv_size = 1024 * 1024
    try:
        data = inst.query_binary_values(":DISPlay:DATA?", datatype="B",
                                        container=bytes)
    finally:
        if old_recv is not None:
            sess.max_recv_size = old_recv
        inst.timeout = old_timeout
    if not data.startswith(b"BM"):
        raise RuntimeError("截图数据头异常: %r" % data[:8])

    outdir = outdir or record_dir()
    base = "scope_%s%s" % (stamp(), ("_" + tag) if tag else "")
    bmp = os.path.join(outdir, base + ".bmp")
    png = os.path.join(outdir, base + ".png")
    with open(bmp, "wb") as fh:
        fh.write(data)
    try:
        from PIL import Image
        Image.open(bmp).save(png)
        os.remove(bmp)
        return png
    except ImportError:
        return bmp


# ── 读数辅助 ─────────────────────────────────────────────────────────
def lsb(scale_vdiv):
    """当前档位下 1 个量化台阶值多少伏 —— 判断读数可信度的下限。"""
    return scale_vdiv * DIV_VERT / ADC_LEVELS


def trig_status_cn(raw):
    return "%s %s" % (raw, TRIG_STATUS_CN.get(raw.upper(), ""))
