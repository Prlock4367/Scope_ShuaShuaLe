# -*- coding: utf-8 -*-
"""SCPI 命令闸门 —— 把「只发核对过的命令」这条红线写死在代码里。

规则（命令闸门的出发点）：
    1. 只走 VXI-11（这件事由 link.py 保证）
    2. **只发 data/ds2000e_commands.json 里核对过的命令 —— 查不到就不发**
    3. 禁止红线命令（见 BLACKLIST）
    4. 改设置（B 级）前先记录原值（由 link.apply 保证）

校验集是脚本从官方编程手册抽出来的 `ds2000e_commands.json`（670 条），
所以「手册里有没有这条命令」是查表，不是猜。

三级分类
--------
    A 级  只读查询（末尾带 `?`）—— 手册查得到即可发
    B 级  写命令 —— 必须**同时**出现在 B_LEVEL 白名单里
    红线   无论手册里有没有，一律拒绝

要点：默认拒绝。闸门里的任何异常都不降级放行。
"""
import itertools
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                           # scope/ → 项目根
COMMANDS_JSON = os.path.join(ROOT, "data", "ds2000e_commands.json")

_PLACEHOLDER = "<N>"        # 手册里 <n> 这类数值占位符，统一成 <N> 便于比对
_DIGIT = re.compile(r"\d")
_PLACE = re.compile(r"<\w+>")

_MANUAL_KEYS = None
_BLACK_KEYS = None
_B_KEYS = None


class Denied(Exception):
    """命令未通过闸门。绝不降级放行。"""


# ── 红线命令 ──────────────────────────────────────────────────────────
# 绝对禁止项。
BLACKLIST_FROM_RULES = (
    ":SYSTem:PRESet",      # 恢复出厂，抹掉现场全部设置
    ":SYSTem:KEY",         # 模拟面板按键 —— 等于把面板交给远端乱按
    ":CALibrate",          # 校准，会改仪器标定数据
    ":SYSTem:UPDate",      # 固件升级
    ":SYSTem:LANGuage",    # 改语言，改错了界面就不认识
)
# 加严的只剩一条：
#   *RST        等同恢复出厂，而且一次改一堆，无法逐条记录原值
#
# :AUToscale 曾在这里，2026-09-24 用户决定放行 ——
#   它就是面板 AUTO 键的软件版，价值正是"一次调好"。
#   放行的代价（一次改一堆、无法逐条报告原值）由工具层 absorb：
#   `scope_autoset` 会在调之前先读一份完整设置快照、调完再读一份，
#   用前后对比代替"逐条记录"。命令本身移入下方 B 级白名单。
BLACKLIST_EXTRA = (
    "*RST",
)
BLACKLIST = BLACKLIST_FROM_RULES + BLACKLIST_EXTRA

# ── B 级白名单：本仓库实际会用到的写命令，一条不多 ────────────────────
# 每条都核过 DS2000E 编程手册（写错一条就可能卡死会话）。
B_LEVEL = (
    ":CHANnel<n>:SCALe", ":CHANnel<n>:OFFSet", ":CHANnel<n>:COUPling",
    ":CHANnel<n>:PROBe", ":CHANnel<n>:BWLimit", ":CHANnel<n>:DISPlay",
    ":CHANnel<n>:INVert", ":CHANnel<n>:UNITs",
    ":CHANnel<n>:IMPedance", ":CHANnel<n>:VERNier",
    ":TIMebase:MAIN:SCALe", ":TIMebase:MAIN:OFFSet", ":TIMebase:MODE",
    ":TIMebase:DELay:ENABle", ":TIMebase:DELay:SCALe", ":TIMebase:DELay:OFFSet",
    ":TRIGger:SWEep", ":TRIGger:MODE", ":TRIGger:COUPling",
    ":TRIGger:EDGe:SOURce", ":TRIGger:EDGe:SLOPe", ":TRIGger:EDGe:LEVel",
    # 采集控制（免选件）
    ":RUN", ":STOP", ":SINGle", ":TFORce",
    # 自动设置（2026-09-24 从红线移入）：面板 AUTO 键的软件版。
    # 一次改一堆设置，工具层用"前后快照对比"代替逐条记录原值。
    ":AUToscale",
    # 采集参数
    ":ACQuire:TYPE", ":ACQuire:AVERages", ":ACQuire:MDEPth",
    # 测量：频率计源
    ":MEASure:COUNter:SOURce",
    # 触发：其余基础类型（免选件；高级 7 种需 TRIGger 选件，本机未装）
    ":TRIGger:PULSe:SOURce", ":TRIGger:PULSe:WHEN", ":TRIGger:PULSe:UWIDth",
    ":TRIGger:PULSe:LWIDth", ":TRIGger:PULSe:LEVel",
    ":TRIGger:SLOPe:SOURce", ":TRIGger:SLOPe:WHEN", ":TRIGger:SLOPe:WINDow",
    ":TRIGger:SLOPe:TUPPer", ":TRIGger:SLOPe:TLOWer", ":TRIGger:SLOPe:ALEVel",
    ":TRIGger:SLOPe:BLEVel",
    ":TRIGger:RUNT:SOURce", ":TRIGger:RUNT:WHEN", ":TRIGger:RUNT:WUPPer",
    ":TRIGger:RUNT:WLOWer", ":TRIGger:RUNT:POLarity", ":TRIGger:RUNT:ALEVel",
    ":TRIGger:RUNT:BLEVel",
    ":TRIGger:PATTern:PATTern", ":TRIGger:PATTern:SOURce", ":TRIGger:PATTern:LEVel",
    ":TRIGger:SHOLd:SOURce", ":TRIGger:SHOLd:SLOPe", ":TRIGger:SHOLd:TYPE",
    ":TRIGger:SHOLd:STIMe", ":TRIGger:SHOLd:HTIMe", ":TRIGger:SHOLd:CSrc",
    ":TRIGger:SHOLd:DSrc", ":TRIGger:SHOLd:PATTern",
    ":TRIGger:NREJect", ":TRIGger:HOLDoff",
    # 数学运算与 FFT（手册未标 Option）
    ":CALCulate:MODE", ":CALCulate:FFT:SOURce", ":CALCulate:FFT:WINDow",
    ":CALCulate:FFT:SPLit", ":CALCulate:FFT:VSMode", ":CALCulate:FFT:HSCale",
    ":CALCulate:FFT:HSPan", ":CALCulate:FFT:HCENter", ":CALCulate:FFT:HOFFset",
    ":CALCulate:FFT:VSCale", ":CALCulate:FFT:VOFFset",
    ":CALCulate:ADD:SA", ":CALCulate:ADD:SB", ":CALCulate:ADD:VOFFset",
    ":CALCulate:ADD:VSCale", ":CALCulate:ADD:INVert",
    ":CALCulate:SUB:SA", ":CALCulate:SUB:SB", ":CALCulate:SUB:VOFFset",
    ":CALCulate:SUB:VSCale", ":CALCulate:SUB:INVert",
    ":CALCulate:MULTiply:SA", ":CALCulate:MULTiply:SB",
    ":CALCulate:MULTiply:VOFFset", ":CALCulate:MULTiply:VSCale",
    ":CALCulate:MULTiply:INVert",
    ":CALCulate:DIVision:SA", ":CALCulate:DIVision:SB",
    ":CALCulate:DIVision:VOFFset", ":CALCulate:DIVision:VSCale",
    ":CALCulate:DIVision:INVert",
    ":CALCulate:LOGic:SA", ":CALCulate:LOGic:SB", ":CALCulate:LOGic:OPERator",
    ":CALCulate:LOGic:ATHReshold", ":CALCulate:LOGic:BTHReshold",
    ":CALCulate:LOGic:VOFFset", ":CALCulate:LOGic:VSCale",
    ":CALCulate:LOGic:INVert",
    # 参考波形（易失内存；SAVe/RESet 是动作命令，无参数）
    ":REFerence:DISPlay", ":REFerence:SOURce", ":REFerence:ENABle",
    ":REFerence:SAVe", ":REFerence:RESet",
    ":REFerence:VSCale", ":REFerence:VOFFset", ":REFerence:COLor",
    # 波形取数
    ":WAVeform:SOURce", ":WAVeform:MODE", ":WAVeform:FORMat",
    ":WAVeform:STARt", ":WAVeform:STOP", ":WAVeform:POINts",
    # 游标（2026-09-24 加）：与 erebusnz 版对齐的游标能力，命令已核 DS2000E 手册
    ":CURSor:MODE",
    ":CURSor:MANual:SOURce", ":CURSor:MANual:TYPE", ":CURSor:MANual:TUNit",
    ":CURSor:MANual:CAX", ":CURSor:MANual:CBX",
    ":CURSor:MANual:CAY", ":CURSor:MANual:CBY",
    ":CURSor:TRACk:SOURce",
    ":CURSor:TRACk:CAX", ":CURSor:TRACk:CBX",
    # 系统时钟（2026-10-09 加）：对钟用（cli clock --sync），只写时刻不动采集设置
    ":SYSTem:DATE", ":SYSTem:TIME",
)


# ── 命令形式的规范化 ──────────────────────────────────────────────────
def _expand_brackets(form):
    """展开手册写法里的 `[可选段]`，返回全部等价写法。

    `:TIMebase[:MAIN]:SCALe` → `:TIMebase:MAIN:SCALe` 和 `:TIMebase:SCALe`
    """
    out = [form]
    while any("[" in f for f in out):
        nxt = []
        for f in out:
            m = re.search(r"\[([^\[\]]*)\]", f)
            if not m:
                nxt.append(f)
                continue
            nxt.append(f[:m.start()] + m.group(1) + f[m.end():])
            nxt.append(f[:m.start()] + f[m.end():])
        out = nxt
    return out


def _segment_forms(seg):
    """一段命令 → (短形, 全形)。

    手册写法里**大写字母是必填、小写可省**：`SCALe` → 短 `SCAL`／全 `SCALE`。
    纯小写段（手滑打错大小写）退化成「整段即全形」—— 仪器本身不分大小写，
    放行是对的，不是漏洞。
    """
    seg = seg.strip()
    ph = _PLACEHOLDER if ("<" in seg or _DIGIT.search(seg)) else ""
    body = _PLACE.sub("", seg)                     # 先摘掉 <n>，否则 n 会被当成字母
    short = "".join(c for c in body if c.isupper()).upper()
    full = "".join(c for c in body if c.isalpha()).upper()
    if not short:
        short = full
    return short + ph, full + ph


def path_keys(form):
    """一条命令形式 → 它全部**合法字面写法**的集合（短/全各段自由组合）。

    仅用于生成「合法写法全集」。判断用户命令**不能**用它的组合结果，
    必须用 literal_key 得到唯一写法再比对 —— 理由见 literal_key 的注释。
    """
    keys = set()
    for variant in _expand_brackets(form):
        variant = variant.strip().rstrip("?").strip()
        segs = [s for s in variant.split(":") if s.strip()]
        if not segs:
            continue
        for pick in itertools.product(*[_segment_forms(s) for s in segs]):
            keys.add(":".join(pick))
    return keys


def literal_key(cmd):
    """把命令规范成**唯一**一个字面写法 → (写法, 是否查询)。

    为什么不能像早期版本那样"按多种解释去撞集合"：
        用户写 `:MEASure:PWIDe?` 时，它的简称 `MEAS:PWID` 和手册的
        `:MEASure:PWIDth?` 简称撞上了，于是被判合法 —— 但送出去的字符串
        **只有一个**，仪器解出来是 `PWIDE`，回 -113 Undefined header。
        在裸 socket 5555 上，一条非法查询就会把会话永久卡死。
        所以闸门必须按"实际要发的那个字符串"判，不能按"它可能是谁"判。

    规范化只做三件事：去参数、逐段取字母大写、数字段记成 <N> 占位。
    """
    head, is_query = split_cmd(cmd)
    out = []
    for seg in [s for s in head.strip().split(":") if s.strip()]:
        placeholder = _PLACEHOLDER if ("<" in seg or _DIGIT.search(seg)) else ""
        body = _PLACE.sub("", seg)
        out.append("".join(c for c in body if c.isalpha()).upper() + placeholder)
    return ":".join(out), is_query


def _load_keys(forms):
    out = set()
    for f in forms:
        out |= path_keys(f)
    return out


def manual_keys():
    """手册命令集里全部合法命令头。首次调用时读 JSON，之后走缓存。"""
    global _MANUAL_KEYS
    if _MANUAL_KEYS is None:
        with open(COMMANDS_JSON, encoding="utf-8") as fh:
            data = json.load(fh)
        forms = []
        for entry in data.values():
            forms.extend(entry.get("forms") or [])
        if not forms:
            raise RuntimeError("命令集为空，检查 %s" % COMMANDS_JSON)
        _MANUAL_KEYS = _load_keys(forms)
    return _MANUAL_KEYS


def blacklist_keys():
    global _BLACK_KEYS
    if _BLACK_KEYS is None:
        _BLACK_KEYS = _load_keys(BLACKLIST)
    return _BLACK_KEYS


def b_level_keys():
    global _B_KEYS
    if _B_KEYS is None:
        _B_KEYS = _load_keys(B_LEVEL)
    return _B_KEYS


def split_cmd(cmd):
    """命令串 → (命令头, 是否查询)。参数部分丢掉，只校验命令头。"""
    c = cmd.strip()
    parts = c.split()
    head = parts[0] if parts else c
    is_query = head.endswith("?")
    return head.rstrip("?"), is_query


def classify(cmd):
    """校验一条命令 → (级别 'A'/'B', 规范化写法)。不通过就抛 Denied。"""
    key, is_query = literal_key(cmd)
    if not key:
        raise Denied("空命令: %r" % cmd)

    for bk in blacklist_keys():
        if key == bk or key.startswith(bk + ":"):
            raise Denied("红线命令，禁止发送：%s（命中 %s）" % (cmd, bk))

    if key not in manual_keys():
        raise Denied(
            "手册命令集里查不到：%s\n"
            "  → 按铁律不发（写法要严格对上手冊，多一个字母就算非法）。\n"
            "  → 若要新增，先跑 ../tools/extract_scpi.py 重抽命令集。" % cmd)

    if is_query:
        return "A", key

    if key not in b_level_keys():
        raise Denied(
            "写命令不在 B 级白名单里：%s\n"
            "  → 白名单见 scope/scpi.py 的 B_LEVEL。" % cmd)
    return "B", key


def guard(cmd):
    """给 link.py 用的薄封装：只关心级别。"""
    return classify(cmd)[0]


def summary():
    """自检用：报一下校验集规模。"""
    return {
        "手册命令写法数": len(manual_keys()),
        "红线命令写法数": len(blacklist_keys()),
        "B 级白名单写法数": len(b_level_keys()),
        "命令集文件": COMMANDS_JSON,
    }


if __name__ == "__main__":
    # 直接跑本文件 = 闸门自检。期望值：A=只读放行 / B=写放行 / X=必须拒绝
    import sys
    for k, v in summary().items():
        print("  %-18s %s" % (k, v))
    print()
    CASES = [
        # 只读，放行
        ("*IDN?", "A"), (":SYSTem:ERRor?", "A"), (":MEASure:VPP? CHANnel1", "A"),
        (":MEASure:PWIDth? CHANnel1", "A"), (":MEASure:PWID? CHANnel1", "A"),
        (":WAVeform:DATA?", "A"), (":WAVeform:PREamble?", "A"),
        (":ACQuire:MDEPth?", "A"), (":TIMebase:MAIN:SCALe?", "A"),
        (":CHANnel2:PROBe?", "A"), (":TRIGger:EDGe:LEVel?", "A"),
        (":DISPlay:DATA?", "A"),
        # 写，在白名单里
        (":CHANnel2:SCALe 5", "B"), (":CHANnel2:OFFSet 5", "B"),
        (":TIMebase:MAIN:SCALe 1e-3", "B"), (":TRIGger:EDGe:LEVel 0", "B"),
        (":WAVeform:SOURce CHANnel2", "B"), (":TRIGger:SWEep AUTO", "B"),
        # 写，不在白名单
        # 注：`:ACQuire:TYPE` 原为「不在白名单」用例，2026-09-24 铺开能力时
        # 已正式加入白名单（采集模式），故改用其它仍未开放的写命令做反例。
        (":DISPlay:TYPE VECTors", "X"), (":DISPlay:BRIGhtness 50", "X"),
        (":SYSTem:BEEPer ON", "X"), (":SAVE:CSV:STARt", "X"),
        # 手册里根本没有（本项目核心发现之一）
        (":MEASure:ITEM? VPP", "X"), (":MEASure:ITEM VPP,CHANnel1", "X"),
        # 拼错的长写法 —— 早期版本会漏放，这是回归用例
        (":MEASure:PWIDe? CHANnel1", "X"), (":CHANnel2:SCALEe 5", "X"),
        # 红线
        (":SYSTem:PRESet", "X"), (":SYSTem:KEY 1", "X"), (":CALibrate:STARt", "X"),
        (":SYSTem:LANGuage ENGLish", "X"), (":SYSTem:UPDate", "X"),
        ("*RST", "X"),
        # 2026-09-24 从红线放行为 B 级（用户决定：AUTO 键要有软件版）
        (":AUToscale", "B"),
    ]
    bad = 0
    for cmd, want in CASES:
        try:
            got = guard(cmd)
            note = "放行 %s 级" % got
        except Denied as exc:
            got, note = "X", str(exc).split("\n")[0]
        ok = (got == want)
        bad += 0 if ok else 1
        print("  %-4s %-30s 期望 %s 得到 %-8s %s"
              % ("✓" if ok else "✗", cmd, want, got, note))
    print("\n%s" % ("全部符合预期" if bad == 0 else "有 %d 条不符合预期" % bad))
    sys.exit(0 if bad == 0 else 1)
