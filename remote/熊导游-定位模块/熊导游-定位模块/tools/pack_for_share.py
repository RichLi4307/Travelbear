"""打包给队友/负责人：生成一个干净的 zip，自动排除不该外发的东西。

排除清单（每一条都有理由）：
    private/          私人文件 —— 尤其是和 AI 的立项讨论记录，不该外传
    .env              真实密钥（.env.example 模板会保留）
    __pycache__/      编译缓存
    .pytest_cache/    测试缓存
    _test_tmp/        测试临时目录
    data/             本地缓存（sqlite）
    *.bak             备份文件
    *.pyc             字节码

用法：
    python tools/pack_for_share.py                    # 生成 熊导游-定位模块.zip
    python tools/pack_for_share.py --out D:\\分享.zip   # 指定输出位置
    python tools/pack_for_share.py --list             # 只看会打包哪些文件，不生成
"""
from __future__ import annotations

import argparse
import os
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOP_NAME = "熊导游-定位模块"

EXCLUDE_DIRS = {".git", "private", "__pycache__", ".pytest_cache",
                "_test_tmp", "data", "_pack", ".venv", ".idea"}
EXCLUDE_FILES = {".env"}
EXCLUDE_SUFFIXES = (".bak", ".pyc", ".pyo", ".zip")


def collect() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        # 下划线开头的目录都是临时/验证用的，一律不打包
        dirnames[:] = sorted(d for d in dirnames
                             if d not in EXCLUDE_DIRS and not d.startswith("_"))
        for name in sorted(filenames):
            if name in EXCLUDE_FILES or name.endswith(EXCLUDE_SUFFIXES):
                continue
            files.append(pathlib.Path(dirpath) / name)
    return files


def human(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def main() -> int:
    ap = argparse.ArgumentParser(description="打包成可外发的 zip")
    ap.add_argument("--out", default=None, help="输出路径（默认仓库根目录下的 zip）")
    ap.add_argument("--list", action="store_true", help="只列出会打包的文件")
    args = ap.parse_args()

    files = collect()

    # 按顶层目录分组，方便一眼看出装了什么
    groups: dict[str, list[pathlib.Path]] = {}
    for path in files:
        rel = path.relative_to(ROOT)
        key = rel.parts[0] if len(rel.parts) > 1 else "(根目录)"
        groups.setdefault(key, []).append(rel)

    print("打包内容：")
    total_size = 0
    for key in sorted(groups):
        items = groups[key]
        size = sum(p.stat().st_size for p in items)
        total_size += size
        print(f"  {key:<16} {len(items):>3} 个文件   {human(size):>8}")
    print(f"  {'合计':<16} {len(files):>3} 个文件   {human(total_size):>8}")

    if args.list:
        print("\n明细：")
        for path in files:
            print("   ", path.relative_to(ROOT))
        return 0

    out = pathlib.Path(args.out) if args.out else ROOT / f"{TOP_NAME}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            zf.write(path, arcname=f"{TOP_NAME}/{path.relative_to(ROOT).as_posix()}")

    size = out.stat().st_size
    print(f"\n已生成：{out}")
    print(f"        压缩后 {human(size)}（原始 {human(total_size)}）")

    # 自检：确认该在的在、不该在的不在
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()

    must_have = [
        f"{TOP_NAME}/README.md",
        f"{TOP_NAME}/requirements.txt",
        f"{TOP_NAME}/location/location.py",
        f"{TOP_NAME}/location/scenic_areas.yaml",
        f"{TOP_NAME}/location/beacons.yaml",
        f"{TOP_NAME}/docs/交接说明.md",
        f"{TOP_NAME}/demo/offline_demo.py",
    ]
    missing = [n for n in must_have if n not in names]
    leaked = [n for n in names
              if "/private/" in n or n.endswith("/.env") or n.endswith(".bak")]

    print("\n自检：")
    if missing:
        print("  × 少了必需文件：")
        for n in missing:
            print("     ", n)
    else:
        print("  √ 必需文件齐全")

    if leaked:
        print("  × 有不该外发的文件混进去了：")
        for n in leaked:
            print("     ", n)
    else:
        print("  √ 没有混入 private/ 、.env、.bak")

    test_count = sum(1 for n in names if "/tests/test_location_" in n)
    print(f"  √ 含 {test_count} 个测试文件")

    return 1 if (missing or leaked) else 0


if __name__ == "__main__":
    raise SystemExit(main())
