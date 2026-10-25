# -*- coding: utf-8 -*-
"""权限层 —— 会话有没有资格发这条命令。

这是**第二层闸门**，与 scope/scpi.py 是叠加关系，不是重复：

    第 1 层  scope/scpi.py      「这条命令能不能发」    逐字比对 3237 种写法 · 已在
    第 2 层  scope_mcp/guard.py 「这次会话有没有资格发」  默认只读 · 本文件

分工的理由：scpi.py 管的是**单条命令的合法性**（它不知道谁在发、为什么发），
本文件管的是**会话级授权**（它不管命令长什么样，只管这次会话许不许改仪器）。

设计要点
--------
· 默认只读 —— 写权限必须由**环境变量**显式开启，模型无法给自己提权
· 拒绝**结构化返回**，不抛异常 —— 抛异常会走到 JSON-RPC error，模型看不懂；
  返回带 hint 的 dict，模型能自愈（抄 anthropic「错误当教学」）
· 拒绝发生在**函数体之前** —— 装饰器守卫，被拒时仪器根本不会被碰
· 用 asyncio.Lock 串行化 —— 仪器是单会话独占资源，并发调用必须排队
"""
import asyncio
import os
import threading
import time
from functools import wraps

# ── 写权限总开关 ──────────────────────────────────────────────────────
# 不设 / 设为 0 / false / no / off → 只读模式，所有写工具返回结构化拒绝。
_ENV_ALLOW = "SCOPE_MCP_ALLOW_CONTROL"
_TRUTHY = ("1", "true", "yes", "on")


def write_enabled():
    """写权限是否开启。每次实时读环境变量，改完重启即生效。"""
    return os.environ.get(_ENV_ALLOW, "").strip().lower() in _TRUTHY


# ── 单会话串行锁 ──────────────────────────────────────────────────────
# 仪器独占：两个客户端同时抓波形会互相踩。所有工具调用过这把锁。
# 两把锁：async 走 asyncio.Lock（SDK 事件循环），sync 走 threading.Lock
# （SDK 把同步 handler 丢进 worker 线程，asyncio 锁跨线程会炸）。
_lock = asyncio.Lock()
_thread_lock = threading.Lock()
_last_call = 0.0
_MIN_GAP = 0.05                 # 两条 SCPI 之间的最小间隔，给仪器喘口气
_stats = {"calls": 0, "denied": 0, "errors": 0, "started": time.time()}


def stats():
    """本会话的调用统计 —— 调试用，也是评测题的素材。"""
    d = dict(_stats)
    d["uptime_s"] = round(time.time() - d["started"], 1)
    d["write_enabled"] = write_enabled()
    return d


def denial(reason, hint):
    """结构化拒绝。**不是异常** —— 让模型看得懂、能自愈。"""
    _stats["denied"] += 1
    return {"ok": False, "denied": True, "reason": reason, "hint": hint}


def requires_write(fn):
    """写工具守卫：未开启写权限 → 结构化拒绝，仪器不会被碰。

    ⚠️ 必须**同时支持同步与异步**被包装函数。
    SDK v2 把同步 handler 放到 worker 线程跑，而我们的 handler 是 `def`（同步）。
    若这里写成 `async def wrapper`，同步 handler 会返回一个 coroutine 对象，
    SDK 拿到它不是 dict → 抛 UnexpectedToolError，**结构化拒绝形同虚设**。
    —— 这是真机上踩到的（只读模式下调写工具，报的是 UnexpectedToolError 而非
    我们精心写的 hint）。所以按被包装函数是否协程，走两条路。
    """
    if asyncio.iscoroutinefunction(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            if not write_enabled():
                return denial(
                    "写操作未开启（server 当前为只读模式）",
                    "如需改仪器设置，请设置环境变量 %s=1 后重启 server。" % _ENV_ALLOW)
            return await fn(*args, **kwargs)
    else:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not write_enabled():
                return denial(
                    "写操作未开启（server 当前为只读模式）",
                    "如需改仪器设置，请设置环境变量 %s=1 后重启 server。" % _ENV_ALLOW)
            return fn(*args, **kwargs)
    return wrapper


def serialized(fn):
    """单会话串行化 + 最小间隔 + 调用计数。

    **必须包在最外层**（写在 @mcp.tool() 之下、业务逻辑之外），
    这样被拒绝的调用也计入统计，且不会出现"一半在锁内一半在锁外"。

    同 requires_write：**同步/异步两条路**，否则同步 handler 会被包成 coroutine。
    """
    if asyncio.iscoroutinefunction(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            global _last_call
            async with _lock:
                gap = time.monotonic() - _last_call
                if gap < _MIN_GAP:
                    await asyncio.sleep(_MIN_GAP - gap)
                _stats["calls"] += 1
                try:
                    return await fn(*args, **kwargs)
                except Exception as exc:
                    _stats["errors"] += 1
                    raise
                finally:
                    _last_call = time.monotonic()
    else:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # 同步 handler：SDK 已在 worker 线程里跑，这里用阻塞锁串行化即可。
            # 用 threading.Lock 而非 asyncio.Lock —— asyncio 锁不能跨线程用。
            global _last_call
            with _thread_lock:
                gap = time.monotonic() - _last_call
                if gap < _MIN_GAP:
                    time.sleep(_MIN_GAP - gap)
                _stats["calls"] += 1
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:
                    _stats["errors"] += 1
                    raise
                finally:
                    _last_call = time.monotonic()
    return wrapper
