"""现场校准：实时打印你在哪、离每个景区/点位多远、会被判成什么。

这是把 `scenic_areas.yaml` 调准的唯一有效办法 —— 站在现场，看着屏幕改坐标和半径。

用法：
    python tools/where_am_i.py                          # 默认 /dev/ttyUSB0
    python tools/where_am_i.py --port COM3              # Windows 笔记本
    python tools/where_am_i.py --once                   # 只看一眼就退出

屏幕上会同时显示：
  * 原始 fix（坐标/星数/HDOP）和它够不够判景区、够不够判点位
  * 你到每个景区中心的距离、以及是否在半径内
  * 所属景区内每个点位的距离、以及是否命中
  * 最终 get_location() 会给出的结论（含 confidence）

调半径的操作循环：
    站在点位正中 → 记下"距离中心 xx m"
    走到你希望触发的边界 → 记下距离
    把 radius_m 设在这两个值之间偏保守的位置，改 yaml，屏幕立刻生效（每秒重读）
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location import gnss, switch                      # noqa: E402
from location.geofence import GeofenceIndex, haversine_m  # noqa: E402

DEFAULT_YAML = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                            "location", "scenic_areas.yaml")


def clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def fmt_gate(fix) -> str:
    return (f"景区级={'OK' if fix.is_usable() else '--'}  "
            f"点位级={'OK' if fix.is_precise() else '--'}")


def render(index: GeofenceIndex, reader: gnss.GnssReader, sm, port: str, elapsed: float) -> None:
    clear()
    fix = reader.latest()

    print(f"定位校准  |  {port}  |  已运行 {elapsed:5.0f}s  |  Ctrl+C 退出")
    print("=" * 74)

    if fix is None:
        print("\n  还没拿到定位。排查顺序：")
        print("   1. 天线朝向天空、别贴着金属/人体；拿到窗边或户外")
        print("   2. 冷启动要 30s~2min，耐心等 `$GPGGA` 的 quality 从 0 变 1")
        print(f"   3. 端口对不对（现在是 {port}），线序 TX↔RX 有没有交叉")
        print(f"   4. 权限：Linux 上 sudo usermod -aG dialout $USER 后重新登录")
        return

    print(f"\n当前位置   {fix.lat:.6f}, {fix.lon:.6f}")
    print(f"定位质量   quality={fix.quality}  sats={fix.satellites}  hdop={fix.hdop}")
    print(f"判定资格   {fmt_gate(fix)}")

    match = index.locate(fix.lat, fix.lon, precise=fix.is_precise())
    hit_area = match.area.name if match else None

    print("\n── 景区 " + "─" * 66)
    for area in sorted(index.areas, key=lambda a: a.distance_m(fix.lat, fix.lon)):
        distance = area.distance_m(fix.lat, fix.lon)
        inside = distance <= area.radius_m
        mark = "√ 在范围内" if inside else "  "
        here = "  ← 当前景区" if area.name == hit_area else ""
        print(f"  {mark}  {area.name:<22} 距中心 {distance:7.0f}m / 半径 {area.radius_m:5.0f}m{here}")

    if match is not None and match.area.spots:
        print(f"\n── {match.area.name} 的点位 " + "─" * max(0, 56 - len(match.area.name)))
        for spot in sorted(match.area.spots, key=lambda s: s.distance_m(fix.lat, fix.lon)):
            distance = spot.distance_m(fix.lat, fix.lon)
            inside = distance <= spot.radius_m
            mark = "√ 命中" if inside else "     "
            print(f"  {mark}  {spot.name:<22} 距中心 {distance:7.0f}m / 半径 {spot.radius_m:5.0f}m")

    print("\n── get_location() 会返回 " + "─" * 46)
    loc = sm.decide(fix, None)
    print(f"  mode={loc.mode}  poi_name=「{loc.poi_name}」  confidence={loc.confidence}")


def main() -> int:
    ap = argparse.ArgumentParser(description="定位现场校准")
    ap.add_argument("--port", default=os.environ.get("GNSS_PORT", "/dev/ttyUSB0"))
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--areas", default=DEFAULT_YAML)
    ap.add_argument("--once", action="store_true", help="只打印一次就退出")
    args = ap.parse_args()

    try:
        index = GeofenceIndex.load(args.areas)
    except Exception as exc:
        print(f"读不了 {args.areas}：{exc}")
        return 1
    if index.is_empty():
        print(f"{args.areas} 里一个景区都没有，先填「areas:」再跑")
        return 1

    sm = switch.LocationStateMachine(geofence=index)
    reader = gnss.GnssReader(args.port, args.baud)
    reader.start()

    start = time.monotonic()
    try:
        while True:
            render(index, reader, sm, args.port, time.monotonic() - start)
            if args.once:
                return 0
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\n结束")
    finally:
        reader.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
