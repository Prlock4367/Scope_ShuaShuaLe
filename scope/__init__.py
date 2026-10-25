# -*- coding: utf-8 -*-
"""scope —— RIGOL DS2102E 采集库（示波器自动化的唯一底座）。

三层，依赖方向单向：
    scpi.py   命令闸门：哪些命令能发（默认拒绝）
    link.py   链路：连接、收发、原值记录、截图、项目路径
    wave.py   分析：抓波形 + 统计 + 频谱 + 包络 + 量化（只依赖 numpy）

对外用法：
    from scope import link, wave
    inst = link.connect()
    t0, dt, v = wave.fetch(inst, "CHANnel2")
    print(wave.stat(v, dt))

命令行入口在 ../cli/scopecli.py，人用那个。
本库只依赖 pyvisa + numpy（不引 matplotlib），日后 MCP 服务可直接 import。
"""
from . import link, scpi, wave

__all__ = ["link", "scpi", "wave", "SCOPE_IP"]

SCOPE_IP = link.SCOPE_IP
