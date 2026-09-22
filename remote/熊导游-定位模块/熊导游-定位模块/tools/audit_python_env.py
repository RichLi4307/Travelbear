"""体检 Python 环境：找出 site-packages 里"装过但文件不见了"的包。

为什么需要这个：某些清理/安全软件会按**体积**删除 Python 包里的文件
（尤其是那些又大又是纯数据表的模块，比如 pygments 的词法器表、
rich 的 emoji 表、cryptography 的证书包）。删掉之后报错信息往往
驴唇不对马嘴 —— 比如"pip 坏了"，其实只是 pip 自带的某个 200KB 数据文件没了。

判断依据有两个：
  1. 每个包自带的 RECORD 清单记录了它应该有哪些文件（这是权威清单）
  2. 如果某个 .py 不见了、但 __pycache__ 里还留着它的 .pyc，
     说明这个文件**曾经装好过**、后来被删了 —— 这一条能直接把锅定位到"外部删除"

用法：
    python tools/audit_python_env.py            # 体检当前环境
    python tools/audit_python_env.py --largest  # 顺带列出每个包里最大的文件，对照缺失情况
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import sysconfig


def pyc_sibling(path: str) -> str | None:
    """如果 path 是 .py，返回它对应的 __pycache__ 里的 .pyc 路径（可能不存在）。"""
    if not path.endswith(".py"):
        return None
    tag = ".cpython-{}{}.pyc".format(sys.version_info.major, sys.version_info.minor)
    return os.path.join(os.path.dirname(path), "__pycache__",
                        os.path.basename(path)[:-3] + tag)


def audit(lib: str) -> tuple[int, dict[str, list[str]], dict[str, list[str]]]:
    """返回 (条目总数, {包名: [缺失文件]}, {包名: [曾存在过证据]})"""
    checked = 0
    missing: dict[str, list[str]] = {}
    deleted_evidence: dict[str, list[str]] = {}

    for dirpath, _dirnames, filenames in os.walk(lib):
        if not dirpath.endswith(".dist-info"):
            continue
        record = os.path.join(dirpath, "RECORD")
        if not os.path.exists(record):
            continue
        pkg = os.path.basename(dirpath)[: -len(".dist-info")]
        try:
            with open(record, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
        except Exception:
            continue
        for row in rows:
            if not row or not row[0]:
                continue
            rel = row[0]
            full = os.path.join(lib, rel.replace("/", os.sep))
            checked += 1
            if os.path.exists(full):
                continue
            missing.setdefault(pkg, []).append(rel)
            sibling = pyc_sibling(full)
            if sibling and os.path.exists(sibling):
                deleted_evidence.setdefault(pkg, []).append(rel)
    return checked, missing, deleted_evidence


def largest_files(lib: str, limit: int = 6) -> dict[str, list[tuple[str, int]]]:
    """每个包最大的几个文件，用来验证"是不是专挑大文件删"。"""
    sizes: dict[str, list[tuple[str, int]]] = {}
    for dirpath, _dirnames, filenames in os.walk(lib):
        if dirpath.endswith((".dist-info", "__pycache__")):
            continue
        rel_dir = os.path.relpath(dirpath, lib)
        pkg = rel_dir.split(os.sep)[0]
        if pkg.endswith(".dist-info"):
            continue
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            sizes.setdefault(pkg, []).append((name, size))
    return {pkg: sorted(items, key=lambda kv: -kv[1])[:limit]
            for pkg, items in sizes.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description="Python 环境体检")
    ap.add_argument("--largest", action="store_true", help="对照每个包里最大的文件")
    args = ap.parse_args()

    lib = sysconfig.get_paths()["purelib"]
    print(f"解释器 : {sys.executable}")
    print(f"体检目录: {lib}")
    print("=" * 74)

    checked, missing, evidence = audit(lib)
    print(f"扫描了 {checked} 个安装条目，缺失 {sum(len(v) for v in missing.values())} 个")
    print("=" * 74)

    if not missing:
        print("\n环境完整，没有发现问题。")
        return 0

    print()
    for pkg in sorted(missing, key=lambda p: -len(missing[p])):
        files = sorted(missing[pkg])
        print(f"  [{pkg}]  缺 {len(files)} 个")
        for rel in files:
            proof = "   ← 有 .pyc 残留，说明装好过、后被外部删除" if rel in set(
                evidence.get(pkg, [])) else ""
            print(f"      - {rel}{proof}")
        print()

    if args.largest:
        print("=" * 74)
        print("对照：这些包里最大的文件是哪些")
        big = largest_files(lib)
        for pkg in sorted(missing):
            print(f"\n  [{pkg}] 最大的几个文件：")
            for name, size in big.get(pkg, []):
                print(f"      {size:>10,} bytes  {name}")

    print("=" * 74)
    print("修复建议：")
    print("  1. 先查是不是清理/安全软件干的（看你装的杀软、管家、CCleaner 的隔离区/清理记录）")
    print("     并把 Python 安装目录加进它们的白名单，否则修好还会被删：")
    print(f"       {os.path.dirname(os.path.dirname(sys.executable))}")
    print("  2. 重装受影响的包：")
    pkgs = " ".join(sorted(missing))
    print(f"       python -m pip install --force-reinstall --no-deps {pkgs}")
    print("  3. 如果是 pip 自己缺文件，用官方 wheel 兜底：")
    print("       python -m ensurepip --upgrade")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
