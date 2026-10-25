# -*- coding: utf-8 -*-
"""网页端配置 —— 仪器地址 / 记录目录 / 测量项表。

机器相关的东西（仪器 IP、记录落哪）写项目根的 `config.ini`，不进版本库；
没写就取这里的默认值（例见 config.example.ini）。
"""
import configparser
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                    # web/ → 项目根

DEFAULT_IP = "169.254.43.112"                   # 兜底地址：只在 config.ini 没写 ip 时用（Auto-IP 会漂）
DEFAULT_PORT = 8760
CHANS = ["CHANnel1", "CHANnel2"]

# 测量项：key / 中文名 / SCPI 命令 / 单位 / 小数位
ITEMS = [
    ("freq",   "频率",     ":MEASure:FREQuency?", "Hz", 3),
    ("period", "周期",     ":MEASure:PERiod?",    "s",  9),
    ("vpp",    "峰峰值",   ":MEASure:VPP?",       "V",  4),
    ("vtop",   "高电平",   ":MEASure:VTOP?",      "V",  4),
    ("vbase",  "低电平",   ":MEASure:VBASe?",     "V",  4),
    ("pduty",  "正占空比", ":MEASure:PDUTy?",     "%",  2),
    ("rtime",  "上升时间", ":MEASure:RTIMe?",     "s",  9),
]


def _load():
    cp = configparser.ConfigParser()
    cp.read(os.path.join(ROOT, "config.ini"), encoding="utf-8")
    ip = cp.get("scope", "ip", fallback=DEFAULT_IP).strip() or DEFAULT_IP
    port = cp.getint("web", "port", fallback=DEFAULT_PORT)
    rec = cp.get("paths", "record_root", fallback="").strip()
    return ip, port, rec or os.path.join(ROOT, "records")


SCOPE_IP, PORT, RECORD_ROOT = _load()
