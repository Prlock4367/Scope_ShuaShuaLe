"""
逐一核对: 现有 MCP 声称支持的测量项, 在 DS2000E 上到底有没有。

MCP 用的是 DS1000Z 的通用选择器语法 `:MEASure:ITEM <item>,<source>`;
DS2000E 改成"每条测量一个专用命令" `:MEASure:<ITEM>? <source>`。
所以这里做两件事:
  1. 测量项名字本身两边是否一致
  2. 给出 DS2000E 侧的等价命令写法

产出: <报告目录>/measure_item_compat.md（默认项目内 reports/）
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
COMMANDS = ROOT / "data" / "ds2000e_commands.json"      # 闸门校验集，项目内权威副本
# 手册原文/第三方源码：仓库不带，用 SCOPE_MANUAL_DIR 指向外部目录（默认 data/manual/）
MANUAL = Path(os.environ.get("SCOPE_MANUAL_DIR", ROOT / "data" / "manual"))
REPORTS = Path(os.environ.get("SCOPE_REPORT_DIR", ROOT / "reports"))
REF_SCOPE_PY = MANUAL / "第三方源码" / "erebusnz-rigol-mcp" / "src" / "rigol_mcp" / "scope.py"

# 这些也在 :MEASure: 命名空间下, 但属于"控制"而非"测量读数", 不算测量项
CONTROL_NOT_ITEM = {"CLEAR", "RECOVER", "SOURCE", "ADISPLAY", "AMSOURCE"}


def mcp_measure_items(scope_py: Path) -> list[str]:
    """从源码里抠出 MEASURE_ITEMS 集合。"""
    text = scope_py.read_text(encoding="utf-8")
    m = re.search(r"MEASURE_ITEMS\s*=\s*frozenset\(\{(.*?)\}\)", text, re.S)
    if not m:
        raise SystemExit("没找到 MEASURE_ITEMS 定义")
    return sorted(set(re.findall(r'"([A-Z0-9]+)"', m.group(1))))


def ds2000e_items(cmds: dict) -> dict[str, str]:
    """DS2000E 的直接测量项 -> 手册中的规范写法。"""
    out = {}
    for k in cmds:
        ku = k.upper()
        if not ku.startswith(":MEASURE:"):
            continue
        rest = k[len(":MEASure:"):]
        if ":" in rest:            # 排除 :STATistic: / :COUNter: 等下级子系统
            continue
        out[rest.upper()] = rest
    return out


def main() -> int:
    scope_py = Path(sys.argv[1]) if len(sys.argv) > 1 else REF_SCOPE_PY

    mcp_items = mcp_measure_items(scope_py)
    ds = ds2000e_items(json.loads(COMMANDS.read_text(encoding="utf-8")))

    ok, missing = [], []
    for it in mcp_items:
        (ok if it in ds else missing).append(it)

    lines = ["# 测量项兼容性逐项核对", ""]
    lines.append(f"MCP 声明支持：**{len(mcp_items)}** 项")
    lines.append(f"DS2000E 直接支持：**{len(ok)}** 项")
    lines.append(f"**DS2000E 上不存在：{len(missing)} 项**")
    lines.append("")
    lines.append(f"DS2000E 自身可用的测量项共 **{len(ds)}** 个。")
    lines.append("")

    lines.append("## 两边通用的测量项")
    lines.append("")
    lines.append("| 测量项 | DS2000E 写法 |")
    lines.append("|---|---|")
    for it in ok:
        lines.append(f"| `{it}` | `:MEASure:{ds[it]}?` |")
    lines.append("")

    if missing:
        lines.append("## ★ DS2000E 上不存在的测量项（MCP 会报错或读到空值）")
        lines.append("")
        lines.append("| 测量项 | 处理建议 |")
        lines.append("|---|---|")
        hint = {
            "VUPPER": "用 `VTOP`（顶部电平）近似",
            "VMID": "无直接等价项，可由 VTOP/VBASE 计算",
            "VLOWER": "用 `VBASe`（底部电平）近似",
            "VARIANCE": "无等价项，需自行由波形数据计算",
            "PSLEWRATE": "无等价项，需由波形数据计算",
            "NSLEWRATE": "无等价项，需由波形数据计算",
            "TVMAX": "无等价项",
            "TVMIN": "无等价项",
            "PPULSES": "无等价项，需由波形数据统计",
            "NPULSES": "无等价项，需由波形数据统计",
            "PEDGES": "无等价项，需由波形数据统计",
            "NEDGES": "无等价项，需由波形数据统计",
        }
        for it in missing:
            lines.append(f"| `{it}` | {hint.get(it, '无直接等价项')} |")
        lines.append("")

    lines.append("## DS2000E 独有、值得补进 MCP 的测量项")
    lines.append("")
    extra = sorted(set(ds) - set(mcp_items) - CONTROL_NOT_ITEM)
    lines.append("| 测量项 | 说明 |")
    lines.append("|---|---|")
    for it in extra:
        lines.append(f"| `{ds[it]}` | `:MEASure:{ds[it]}?` |")
    lines.append("")

    REPORTS.mkdir(parents=True, exist_ok=True)
    out = REPORTS / "measure_item_compat.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"MCP 测量项 {len(mcp_items)} 个 -> DS2000E 支持 {len(ok)} 个, 缺 {len(missing)} 个")
    print(f"缺失: {', '.join(missing) or '无'}")
    print(f"DS2000E 独有可补充: {', '.join(extra) or '无'}")
    print(f"报告: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
