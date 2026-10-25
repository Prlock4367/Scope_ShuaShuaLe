"""
SCPI 命令抽取工具（通用版）。

用法:
    python extract_scpi.py <pdf路径> [-o 输出前缀] [--dump]
"""
import re
import sys
import json
import argparse
from pathlib import Path

from pypdf import PdfReader

# SCPI 命令形态: :CHANnel<n>:SCALe / :MEASure:VPP / *IDN?
CMD_RE = re.compile(
    r"(?<![A-Za-z0-9_:*])"
    r"(\*[A-Z]{2,4}\??"
    r"|:[A-Za-z]+(?:<n>)?(?:[:\[][A-Za-z0-9<>\-\[\]]*)*)"
)

# 正文/示例代码里出现的伪命令，明确剔除
BLOCKLIST = {":CHAN", ":MATH", ":CALC"}

# 手册目录里的子系统根节点(形如 ":LAN Commands ....")，无实义，剔除
def is_subsystem_root(cmd: str) -> bool:
    return False


def extract_commands(pdf_path: Path) -> dict:
    """返回 {归一化命令: {"count": n, "forms": [...]}}"""
    reader = PdfReader(str(pdf_path))
    text_parts = []
    for page in reader.pages:
        try:
            text_parts.append(page.extract_text() or "")
        except Exception:
            pass
    text = "\n".join(text_parts)

    cmds: dict[str, dict] = {}
    for h in CMD_RE.findall(text):
        key = normalize(h)
        if len(key) < 4 or key in BLOCKLIST:
            continue
        entry = cmds.setdefault(key, {"forms": set(), "count": 0})
        entry["forms"].add(h)
        entry["count"] += 1
    return cmds, text


def normalize(cmd: str) -> str:
    """去掉 <n>、数字、方括号、问号和结尾冒号, 归一到不带序号的规范形式。"""
    c = re.sub(r"<n>", "", cmd)
    c = re.sub(r"\d+", "", c)
    c = re.sub(r"[\[\]]", "", c)
    c = c.rstrip("?:")
    return c


def _seg_forms(seg: str) -> tuple[str, str]:
    """返回某个助记符的 (短形式, 长形式)。SCPI 规则: 短形式 = 助记符中的大写字母。"""
    s = re.sub(r"[<>\d]", "", seg)
    short = "".join(ch for ch in s if ch.isupper()).upper()
    return short, s.upper()


def scpi_variants(cmd: str) -> set[str]:
    """
    返回一条命令在 SCPI 下所有合法写法的归一化集合。
    例: ':TRIGger:EDGe:SOURce' -> {':TRIG:EDG:SOUR', ':TRIGGER:EDGE:SOURCE'}
    这样短写 ':DISP' 就能正确匹配到手册里的 ':DISPlay'。
    """
    segs = normalize(cmd).split(":")
    shorts, longs = [], []
    for s in segs:
        if not s:
            shorts.append("")
            longs.append("")
            continue
        a, b = _seg_forms(s)
        shorts.append(a)
        longs.append(b)
    return {":".join(shorts), ":".join(longs)}


def group_of(cmd: str) -> str:
    parts = cmd.split(":")
    if cmd.startswith(":") and len(parts) > 1:
        return parts[1].upper()
    return "COMMON"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("-o", "--out", default=None, help="输出文件前缀")
    ap.add_argument("--dump", action="store_true", help="同时导出全书纯文本")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"找不到: {pdf}")
        return 1

    cmds, text = extract_commands(pdf)
    prefix = Path(args.out) if args.out else pdf.with_suffix("")

    if args.dump:
        Path(str(prefix) + "_raw.txt").write_text(text, encoding="utf-8")

    groups: dict[str, list[str]] = {}
    for k in sorted(cmds):
        groups.setdefault(group_of(k), []).append(k)

    lines = [f"# SCPI 命令清单 — {pdf.name}", f"# 唯一命令数: {len(cmds)}", ""]
    for sub in sorted(groups, key=lambda s: (-len(groups[s]), s)):
        lines.append(f"## {sub}  ({len(groups[sub])})")
        for k in groups[sub]:
            lines.append(f"  {k}    [{cmds[k]['count']}]")
        lines.append("")
    Path(str(prefix) + "_commands.txt").write_text("\n".join(lines), encoding="utf-8")

    Path(str(prefix) + "_commands.json").write_text(
        json.dumps({k: {"count": v["count"], "forms": sorted(v["forms"])} for k, v in cmds.items()},
                   ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    print(f"{pdf.name}: {len(cmds)} 条唯一命令 -> {prefix}_commands.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
