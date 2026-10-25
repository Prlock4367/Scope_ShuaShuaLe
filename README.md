<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue.svg" alt="Python">
  <img src="https://img.shields.io/badge/Flask-3-blue.svg" alt="Flask">
  <img src="https://img.shields.io/badge/SCPI-VXI--11-brightgreen.svg" alt="SCPI">
  <img src="https://img.shields.io/badge/MCP-ready-purple.svg" alt="MCP">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
</p>

<h1 align="center">
  🔬 示波器刷刷乐
</h1>

<p align="center">
  <strong>Scope_ShuaShuaLe · RIGOL DS2102E 网页控制台</strong><br>
  浏览器打开就能看波形、读参数、一键记录 —— 同一套引擎还提供命令行与 MCP
</p>

## ✨ 这是什么

把一台 RIGOL DS2102E 示波器变成「打开浏览器就能用」的控制台：

- 📈 **实时波形** — 双通道同屏 + 参数读数（频率 / 周期 / 峰峰值 / 高低电平 / 占空比 / 上升时间）
- 📸 **一键记录** — 面板按 STOP 冻结，网页点一下，截图 + 双通道波形 CSV 一次落盘
- 🎛️ **光标与触发** — 四根光标线直接拖，触发电平 / 边沿 / 信源在网页上改
- 🔍 **仪器状态** — 触发状态 / 错误队列 / 通道档位时基，只读一览
- 🤖 **同一份数据** — 网页给人看，`/api/snapshot` 给程序读，**AI 和网页看的是同一个数**
- 🛡️ **命令闸门** — 所有 SCPI 命令过同一道白名单，默认拒绝，未核对过的发不出去

## 🛠️ 技术栈

| 技术 | 用途 |
|------|------|
| Python 3.9+ | 主体语言 |
| Flask 3 | 网页控制台 |
| SCPI over VXI-11 | 仪器通信（111 端口，带会话管理） |
| PyVISA + pyvisa-py | 仪器驱动（延迟导入，只看界面不需要） |
| numpy | 波形统计 / 频谱 / 包络 |
| Canvas 2D | 波形重绘（不引图表库，照仪器屏幕画） |
| MCP | 让 AI 直接调仪器的标准接口 |

## 📦 快速开始

**Windows 用户：双击根目录的 `启动.bat` 就行。** 它会自动找 Python、缺依赖自动装（走阿里云镜像）、起服务并打开浏览器。没接仪器就是 DEMO 演示模式，界面一样能点。

想手动来也可以，三步：

```bash
git clone https://github.com/Prlock4367/Scope_ShuaShuaLe.git
cd Scope_ShuaShuaLe
pip install -r requirements.txt

# 只看界面（不需要仪器，也不需要装仪器库）
python web/app.py --demo

# 连真机（连不上会自动降级，界面照常可用）
python web/app.py
```

浏览器打开 **http://127.0.0.1:8760**

> 只需要 `flask` 就能看界面。`pyvisa` 是连真机才要，代码里是延迟导入。

| 参数 | 作用 |
|---|---|
| （无参数） | 连真机；连不上自动降级 DEMO 仿真数据 |
| `--demo` | 强制 DEMO，完全不碰仪器 |
| `--open` | 启动后自动打开浏览器（`启动.bat` 用的就是它） |
| `--port N` | 换端口，默认 8760 |
| `--host 0.0.0.0` | 允许同网段别的机器访问（默认只本机） |

## 🔌 三种用法，同一套引擎

| 用法 | 入口 | 给谁用 |
|---|---|---|
| **网页控制台** | `web/app.py` | 人（日常用这个） |
| **命令行** | `cli/scopecli.py` | 人 / 脚本（批量、离线分析、频谱包络） |
| **MCP** | `python -m scope_mcp.server` | AI（Claude / Cursor / 任意 MCP 客户端） |

三者都 import 同一个 `scope/` 采集库，**所有 SCPI 收发都过同一道命令闸门**。

MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "scope": {
      "command": "<你的 python 绝对路径>",
      "args": ["-m", "scope_mcp.server"],
      "cwd": "<项目根绝对路径>"
    }
  }
}
```

默认只读。要允许改仪器设置（改档位 / 时基 / 触发），启动时加 `SCOPE_MCP_ALLOW_CONTROL=1`。

## 📁 目录

```
Scope_ShuaShuaLe/
├── scope/        采集库（唯一底座，只依赖 pyvisa + numpy）
│   ├── scpi.py     命令闸门：哪些命令能发（默认拒绝）
│   ├── link.py     链路：连接、收发、原值记录、截图、记录目录
│   ├── wave.py     分析：抓波形 + 统计 + 频谱 + 包络
│   └── shape.py    波形整形
├── web/          网页控制台（Flask）
├── cli/          命令行入口
├── scope_mcp/    MCP server（薄壳，零仪器逻辑）
├── tools/        离线脚本（从官方手册抽命令集，不连仪器）
├── data/         闸门校验集 ds2000e_commands.json（670 条）
├── config.example.ini
└── records/      默认记录目录
```

依赖单向：`web/ cli/ scope_mcp/ → scope/`。仪器逻辑只写在 `scope/` 里，上层不自己拼 SCPI 字符串。

> 动仪器前先读一遍源码里的注释：所有 SCPI 收发过 `scope/scpi.py` 的闸门（默认拒绝 + 白名单 + 红线黑名单），改设置前自动记录原值。配置示例见下方 [⚙️ 配置](#️-配置)。

## 📘 文档

- [DS2000E 网络设置 · 面板操作指南](docs/上手指南/DS2000E网络设置-面板操作指南.md) —— 用本工具前先让示波器进网络：DHCP / 自动IP / 静态IP 的选择与面板操作，含官方手册没细说的优先级坑

## ⚙️ 配置

机器相关的东西（仪器 IP、记录落哪）写项目根的 `config.ini`，**不进版本库**：

```bash
cp config.example.ini config.ini
```

```ini
[scope]
ip = 169.254.x.y           # 示波器地址（Utility → IO Setting → LAN Set 里看；Auto-IP 断电重启不变）

[web]
port = 8760

[paths]
record_root =              # 留空 = 项目内 records/
```

记录默认落 `records/<YYYYMMDD>-附件/`。网页端写 `config.ini`，命令行设环境变量 `SCOPE_RECORD_ROOT` 也能改。

**DEMO 模式落盘的文件一律带 `demo-` 前缀**，跟真机数据分得开。

## 🛡️ 安全约定

安全设计固化在 `scope/scpi.py` 的闸门实现里（默认拒绝 + 白名单 + 红线黑名单），四条写进代码里的铁律：

1. **只走 VXI-11（111 端口）** — 裸 socket 5555 单会话无容错，发错一条查询命令就可能永久卡死，只能断电恢复
2. **只发 `data/ds2000e_commands.json` 里核对过的命令** — 查不到就不发（闸门逐字比对，默认拒绝，异常不降级放行）
3. **红线命令一律拒** — 复位 / 模拟按键 / 校准 / 固件升级
4. **改设置先记原值** — 写命令须在 B 级白名单里，改完必须回读（部分值会被静默钳位且不报错）

网页端的「记录」是**只读动作**：先在面板按 STOP 冻结，网页才允许记录，记完按 RUN —— 网页不会替你按任何键。

> 远程命令不会损坏仪器软件或固件（最坏是会话卡死，断电重启即恢复）；真正的损坏风险来自人接探头（过压 / 地夹短路）。

## ❓ 常见问题

- **连不上仪器** — 网页会自动降级 DEMO，先确认 `config.ini` 的 IP；仪器开着 Auto-IP 地址会漂，网页端会按 ARP / 端口扫描自己找回来
- **波形读数全是台阶** — 输入信号太小，填充率低于 10% 时判为噪声底，调小 V/div 再看
- **提示改 7M** — AUTO 深存储下抓到逐点 0/255 交替的坏帧，切到 7M 存储深度重抓
- **改设置后读数不对** — 仪器会静默钳位，读回值才是真实生效值，别信下发值
- **记录按钮点不动** — 记录要求先 STOP 冻结，这是设计，不是 bug

## 🧪 后续计划

- ✅ 网页端波形 / 光标 / 触发 / 记录
- ✅ 命令行全量只读诊断 + 改设置（带原值记录）
- ✅ MCP server（只读 + 可选写权限）
- 🔄 网页端接入更多仪器功能

## ☕ 赞助支持

如果这个工具帮到了你，可以请作者喝杯咖啡～  
赞助全部用于项目维护，也是我更新的小动力。

👉 [前往爱发电支持 Prlock](https://ifdian.net/a/Prlock)

## 📞 反馈

- **GitHub Issues**：[提交 Bug / 需求](https://github.com/Prlock4367/Scope_ShuaShuaLe/issues)
- **GitHub**：[Prlock4367](https://github.com/Prlock4367)

## 📄 许可证

本项目采用 **MIT License** 开源，详情见 [LICENSE](LICENSE) 文件。

## ⚠️ 免责声明

本项目为个人开发的第三方工具，**与 RIGOL（普源精电）官方无关联**。
项目仅通过仪器公开的 SCPI 接口通信，**不提供任何破解、解锁选件或修改仪器授权状态的功能**，也不接受此类贡献。
RIGOL、DS2102E 等名称仅用于说明兼容性，商标权归其各自所有者。

使用本工具操作仪器前，请先了解 SCPI 命令闸门的工作方式（见上文 [🛡️ 安全约定](#️-安全约定)）。
因误操作、探头接错或仪器故障造成的损失，作者不承担责任。

<p align="center">Made with ❤️ by Prlock</p>
