# -*- coding: utf-8 -*-
"""MCP 与采集库之间的连接适配 —— **不含任何仪器逻辑**。

只做三件事，其余全部下沉 `scope/`：
    1. 惰性连接（server 启动**不**碰仪器，第一次调工具才连）
    2. 连接复用 + 连不上时给出带修法的错误
    3. 把 `scope.link` 的异常翻成模型看得懂的话

硬约定：**MCP 里零仪器逻辑** —— 不自己拼 SCPI、不自己算波形。
"""
import os
import sys

# 让 import scope 生效（scope/ 与 scope_mcp/ 同级，都在 .. 下）
_HERE = os.path.dirname(os.path.abspath(__file__))
if os.path.dirname(_HERE) not in sys.path:
    sys.path.insert(0, os.path.dirname(_HERE))

from scope import link as _link          # noqa: E402
from scope import scpi as _scpi          # noqa: E402

from . import format as fmt              # noqa: E402

SCOPE_IP = os.environ.get("SCOPE_IP", _link.SCOPE_IP)

_inst = None
_conn_error = None


def instrument():
    """惰性连接：第一次调用才连，之后复用。

    为什么惰性（抄 Google Chrome DevTools 的 lazy start）：
        server 启动时仪器可能没开、网线没插。若启动即连，MCP 客户端会
        看到一个连不上的 server 直接放弃，而不是"工具报错但服务还在"。
    """
    global _inst, _conn_error
    if _inst is not None:
        return _inst
    try:
        inst = _link.connect(SCOPE_IP)
        # 连接后先排空错误队列 —— 免得旧错误被算到新命令头上
        stale = _link.drain(inst)
        _inst, _conn_error = inst, None
        if stale:
            _warn("连接时清掉 %d 条历史错误: %s" % (len(stale), stale[:3]))
        return inst
    except Exception as exc:
        _conn_error = "%s: %s" % (type(exc).__name__, exc)
        raise ConnectionError(_conn_error) from exc


def close():
    """释放连接（客户端断开时调用）。"""
    global _inst
    if _inst is not None:
        try:
            _inst.close()
        except Exception:
            pass
        _inst = None


def connected():
    return _inst is not None


def last_error():
    return _conn_error


def conn_hint(exc):
    """连接类错误的**修法** —— 错误必须能教模型下一步怎么做。"""
    return fmt.fail(
        "连不上示波器（%s）" % exc,
        "按此顺序排查：① 确认仪器已开机、网线插好 "
        "② 确认地址 %s 可达 ③ 等 30 秒重试 "
        "④ 远程唯一的兜底手段是断电重启（远程做不到）。"
        "当前地址可用环境变量 SCOPE_IP 覆盖。" % SCOPE_IP)


# ── 只读查询 ─────────────────────────────────────────────────────────
def q(inst, cmd):
    """单条只读查询，失败返回 None（不打断批量采集）。"""
    return _link.try_query(inst, cmd)


def q_required(inst, cmd):
    """单条只读查询，失败抛异常（用于关键字段）。"""
    return _link.q(inst, cmd)


# ── 日志：绝不写 stdout ───────────────────────────────────────────────
# stdio 模式下 stdout 就是 JSON-RPC 通道，print 一次就崩且**不报错**。
#
# ⚠️ 这里踩过一个坑，记下来免得重犯（两次都错，第二次才找对）：
#
#   错法 1：`sys.stdout = sys.stderr`
#       → 整个 MCP 通道哑掉、不报任何错，握手请求发进去毫无回音。
#         因为 SDK 在 run() 时要 `sys.stdout.buffer`，stderr 没有这个属性……
#
#   错法 2：自己写一个 _Swallower 假 stdout
#       → `AttributeError: '_Swallower' object has no attribute 'buffer'`
#         同上原因。
#
#   结论：SDK 已在 stdio 层处理，无需额外代码。
#     `mcp/server/stdio.py::stdio_server()` 原文注释：
#       "While serving, fd 0 points at the null device and fd 1 at stderr,
#        so handlers and children read EOF and their stray output misses the wire;
#        both descriptors are restored on exit."
#     即：serve 期间 **fd 1 被指向 stderr**，任何库内 print 自动落空。
#     它还会 _dup 一份私有流来跑真实通道（closefd=False，防被回收）。
#
#   所以本模块**什么都不用做** —— 只要别去动 sys.stdout。
#   唯一要做的是：自己写日志时明确走 stderr。
_real_stdout = sys.stderr          # 我们自己写日志的地方（stderr 永远安全）


def _warn(msg):
    _real_stdout.write("[scope_mcp] %s\n" % msg)
    _real_stdout.flush()


def apply_log_policy():
    """不再封 stdout —— SDK 的 stdio_server() 已接管。

    设 `SCOPE_MCP_VERBOSE=1` 时给一条自检提示（库内 print 会被 SDK 丢掉，
    所以看不到是正常的，不是 bug）。
    """
    import os
    if os.environ.get("SCOPE_MCP_VERBOSE", "").strip().lower() in ("1", "true", "yes", "on"):
        _warn("VERBOSE：库内 print 会被 SDK 的 stdio_server 丢弃（fd 1 指向 stderr），"
              "这是设计如此，不是故障。要看细节请直接调 scope/ 的库函数。")
        return False
    return True
