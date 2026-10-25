# -*- coding: utf-8 -*-
"""MCP 层端到端探针 —— 发真 JSON-RPC，不连仪器也能验证协议形状。

用法:
    python ./_probe.py
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(HERE)
PYEXE = sys.executable


def main():
    env = dict(os.environ)
    env["PYTHONPATH"] = CODE_DIR
    p = subprocess.Popen(
        [PYEXE, "-m", "scope_mcp.server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", bufsize=1, cwd=CODE_DIR, env=env)

    def rpc(obj):
        p.stdin.write(json.dumps(obj) + "\n")
        p.stdin.flush()
        return p.stdout.readline().strip()

    print("=" * 64)
    print("  scope_mcp 协议探针")
    print("=" * 64)

    # 1) 握手
    init = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "probe", "version": "1"}}})
    d = json.loads(init)["result"]
    print("[1] initialize")
    print("    协议版本 : %s" % d["protocolVersion"])
    print("    server   : %s v%s" % (d["serverInfo"]["name"], d["serverInfo"]["version"]))
    print("    工具能力 : %s" % d["capabilities"].get("tools", {}))
    print("    instructions: %s" % (d.get("instructions", "")[:60].replace("\n", " ")))
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    p.stdin.flush()

    # 2) tools/list
    listing = json.loads(rpc({"jsonrpc": "2.0", "id": 2,
                              "method": "tools/list", "params": {}}))["result"]
    print()
    print("[2] tools/list —— %d 个工具" % len(listing["tools"]))
    for t in listing["tools"]:
        ann = t.get("annotations") or {}
        print("    %-16s readOnly=%-5s destructive=%-5s"
              % (t["name"], ann.get("readOnlyHint"), ann.get("destructiveHint")))
        props = t.get("inputSchema", {}).get("properties", {})
        for pname, spec in props.items():
            desc = (spec.get("description") or "")[:40]
            print("        · %-10s %-16s %s"
                  % (pname, spec.get("type", "?"), desc))

    # 3) 调一个不碰仪器的（scope_errors 会尝试连接 → 应返回结构化失败，不是崩溃）
    print()
    print("[3] tools/call scope_errors（仪器可能不在线，看错误是否结构化）")
    out = json.loads(rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "scope_errors", "arguments": {}}}))["result"]
    print("    isError : %s" % out.get("isError"))
    text = out["content"][0]["text"]
    try:
        payload = json.loads(text)
        print("    ok      : %s" % payload.get("ok"))
        for k in ("reason", "hint"):
            if k in payload:
                print("    %-8s: %s" % (k, str(payload[k])[:110]))
    except json.JSONDecodeError:
        print("    raw     : %s" % text[:160])

    # 4) 未知工具 → 应是协议层错误，不是崩溃
    print()
    print("[4] tools/call 不存在的工具（应是协议级 error）")
    print("    %s" % rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                         "params": {"name": "no_such_tool", "arguments": {}}})[:150])

    p.stdin.close()
    p.terminate()
    err = p.stderr.read().strip()
    print()
    print("[stderr] %s" % (err[:400] if err else "(空 —— stdout 未被污染)"))
    print("=" * 64)


if __name__ == "__main__":
    main()
