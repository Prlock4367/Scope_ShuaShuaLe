"""
跨型号 SCPI 差异分析 + 现有 MCP 兼容性审计。

关键点: SCPI 允许缩写写法(:DISP == :DISPlay), 所以比对必须按
"合法写法集合" 而非字面字符串做, 否则假阳性会淹没真实差异。

产出:
    <报告目录>/gap_report.md    人读报告（默认项目内 reports/）
    <报告目录>/gap_report.json  机读数据
"""
import os
import re
import json
import sys
from pathlib import Path


def _project_root() -> Path:
    """项目根 = 含 data/ds2000e_commands.json 的目录，目录改名/搬迁后不用改脚本。"""
    for parent in Path(__file__).resolve().parents:
        if (parent / "data" / "ds2000e_commands.json").is_file():
            return parent
    raise SystemExit("找不到项目根目录（应含 data/ds2000e_commands.json）")


ROOT = _project_root()
DATA = ROOT / "data"
# 手册原文/他机型抽取结果：仓库不带，用 SCOPE_MANUAL_DIR 指向外部目录（默认 data/manual/）
MANUAL = Path(os.environ.get("SCOPE_MANUAL_DIR", DATA / "manual"))
REPORTS = Path(os.environ.get("SCOPE_REPORT_DIR", ROOT / "reports"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_scpi import CMD_RE, normalize, group_of, scpi_variants  # noqa: E402

# 源码里这些是 Python docstring 角色(:class: :meth: :func:), 不是 SCPI
SOURCE_NOISE = re.compile(r"^:(class|meth|func|mod|attr|exc|obj|ref|data|seg|cyc|third|doc)$", re.I)

# 人工核对: 这些是源码里 f-string 插值打断命令字符串产生的碎片,
# 并非真差异, 对应的真实命令在手册中存在:
#   ':TIM:OFFS' -> ':TIMebase:MAIN:OFFSet'  (f-string 里 {main} 被拆掉)
#   ':TIM:SCAL' -> ':TIMebase:MAIN:SCALe'
# 字面检索抓不到它们, 所以在此显式标注。
CURATED_FRAGMENTS = {":TIM:OFFS", ":TIM:SCAL"}


def load(name: str) -> dict:
    """机型命令集：优先项目内 data/，没有再找手册目录。"""
    for base in (DATA, MANUAL):
        p = base / f"{name}_commands.json"
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit(f"找不到 {name}_commands.json（查过 data/ 与 {MANUAL}）")


def variant_map(cmds: dict) -> dict[str, str]:
    """{合法写法: 手册中的规范命令}"""
    m = {}
    for canon in cmds:
        for v in scpi_variants(canon):
            m.setdefault(v, canon)
    return m


def scpi_in_source(src_dir: Path) -> dict[str, int]:
    """从 MCP 源码中抽出实际会发出的 SCPI 命令(只保留看起来像真命令的)。"""
    found: dict[str, int] = {}
    for py in src_dir.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        for m in CMD_RE.finditer(text):
            raw = m.group(1)
            if not any(c.isupper() for c in raw):      # :class: / :meth: 这类
                continue
            if SOURCE_NOISE.match(raw):
                continue
            k = normalize(raw)
            if len(k) < 4:
                continue
            found[k] = found.get(k, 0) + 1
    return found


def literal_in_manual(cmd: str, raw_upper: str) -> bool:
    """
    把命令按 SCPI 合法写法展开后, 在手册全文中做字面检索。
    用于区分两种"未识别":
      - 手册里其实有(只是抽取时被正文/示例代码干扰) -> 虚警
      - 手册里确实没有 -> 真差异
    """
    return any(v.upper() in raw_upper for v in scpi_variants(cmd) if len(v) > 3)


def main() -> int:
    src_arg = sys.argv[1] if len(sys.argv) > 1 else None
    e_raw = load("ds2000e")     # 目标机型
    z_raw = load("ds1000z")     # 现有 MCP 针对的机型

    e_vars = variant_map(e_raw)
    z_vars = variant_map(z_raw)
    # 按"写法集合"折算成 canonical 集合再比
    e_canon = set(e_vars.values())
    z_canon = set(z_vars.values())

    only_e = sorted(e_canon - z_canon)
    only_z = sorted(z_canon - e_canon)
    both = sorted(e_canon & z_canon)

    lines = ["# DS2000E vs DS1000Z — SCPI 命令集差异", ""]
    lines += [
        "> 比对按 SCPI 合法写法集合进行（缩写与全写等价），已剔除正文示例代码造成的假阳性。",
        "",
        "| 项 | 数量 |",
        "|---|---|",
        f"| DS2000E 命令总数 | {len(e_canon)} |",
        f"| DS1000Z 命令总数 | {len(z_canon)} |",
        f"| **两者都有（安全区）** | **{len(both)}** |",
        f"| 仅 DS2000E 有（新能力） | {len(only_e)} |",
        f"| 仅 DS1000Z 有（移植风险） | {len(only_z)} |",
        "",
    ]

    def block(title: str, items: list[str], note: str = "") -> None:
        lines.append(f"## {title} ({len(items)})")
        if note:
            lines.append(note)
        lines.append("")
        g: dict[str, list[str]] = {}
        for k in items:
            g.setdefault(group_of(k), []).append(k)
        for sub in sorted(g, key=lambda s: (-len(g[s]), s)):
            lines.append(f"**{sub}** ({len(g[sub])}): " + ", ".join(f"`{x}`" for x in g[sub]))
            lines.append("")

    block("仅 DS1000Z 有 —— 现有 MCP 若发送，DS2000E 可能不认",
          only_z, "> 移植时必须逐条实测的高风险区。\n")

    block("仅 DS2000E 有 —— 现有 MCP 完全没用到的新能力",
          only_e, "> 本项目相对现有开源实现的增量价值。\n")

    mcp_hits: dict[str, int] = {}
    if src_arg and Path(src_arg).exists():
        raw_upper = (MANUAL / "ds2000e_raw.txt").read_text(encoding="utf-8", errors="ignore").upper()
        mcp_hits = scpi_in_source(Path(src_arg))
        resolved, fake, real = {}, {}, {}
        for k, n in mcp_hits.items():
            hit = None
            for v in scpi_variants(k):
                if v in e_vars:
                    hit = e_vars[v]
                    break
            if hit:
                resolved[k] = hit
            elif k in CURATED_FRAGMENTS or literal_in_manual(k, raw_upper):
                fake[k] = n          # 手册里有, 属抽取虚警(多为 f-string 碎片)
            else:
                real[k] = n          # 手册里确实没有 -> 必须改写

        lines.append("## 现有 MCP 源码实际发送的命令 × DS2000E 支持情况")
        lines.append("")
        lines.append(f"- MCP 中出现的命令形态：**{len(mcp_hits)}** 条")
        lines.append(f"- DS2000E 能识别：**{len(resolved)}** 条")
        lines.append(f"- 手册中存在（抽取虚警，非真差异）：**{len(fake)}** 条")
        lines.append(f"- **手册中确无，必须改写：{len(real)} 条**")
        lines.append("")
        if real:
            lines.append("### ★ 必须改写的命令（真差异，已核对手册全文）")
            lines.append("")
            lines.append("| MCP 发送的写法 | 出现次数 | 说明 |")
            lines.append("|---|---|---|")
            for k in sorted(real):
                lines.append(f"| `{k}` | {real[k]} | DS2000E 手册中不存在，需改用等价命令 |")
            lines.append("")
        if fake:
            lines.append("### 虚警（手册中有，属 f-string 拆分造成的碎片）")
            lines.append("")
            lines.append(", ".join(f"`{k}`" for k in sorted(fake)))
            lines.append("")
        lines.append("### 已核实可用（写法可能缩写，语义一致）")
        lines.append("")
        for k in sorted(resolved):
            lines.append(f"- `{k}`  →  `{resolved[k]}`")
        lines.append("")

        REPORTS.mkdir(parents=True, exist_ok=True)
        (REPORTS / "gap_report.json").write_text(
            json.dumps({"only_ds2000e": only_e, "only_ds1000z": only_z, "shared": both,
                        "mcp_resolved": {k: str(v) for k, v in resolved.items()},
                        "mcp_real_gap": sorted(real),
                        "mcp_extraction_false_alarm": sorted(fake)},
                       ensure_ascii=False, indent=1), encoding="utf-8")

    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "gap_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"DS2000E={len(e_canon)}  DS1000Z={len(z_canon)}  共同={len(both)}")
    print(f"仅 DS2000E: {len(only_e)}   仅 DS1000Z: {len(only_z)}")
    if mcp_hits:
        bad = sum(1 for k in mcp_hits
                  if not any(v in e_vars for v in scpi_variants(k))
                  and k not in CURATED_FRAGMENTS
                  and not literal_in_manual(k, raw_upper))
        print(f"MCP 命令形态 {len(mcp_hits)} 条, 其中真差异 {bad} 条")
    print(f"报告: {REPORTS / 'gap_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
