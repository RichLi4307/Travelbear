"""把串口原始 NMEA 存成文件 —— 这是离线回归测试的素材来源。

拿到一份真实录制后，你在任何地方（笔记本、教室、没有 GPS 的桌子前）都能回放验证解析器，
不用再插模块等搜星。

用法：
    # 录制（笔记本调试：COM3；树莓派：/dev/ttyUSB0）
    python tools/nmea_recorder.py --port COM3 --out data/nmea_field_01.txt
    python tools/nmea_recorder.py --port /dev/ttyUSB0 --seconds 120

    # 回放（不需要任何硬件，用来验证解析器改动没把老数据弄坏）
    python tools/nmea_recorder.py --replay data/nmea_field_01.txt
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

from location.gnss import NmeaParser  # noqa: E402


def replay(path: str) -> int:
    parser = NmeaParser()
    fixes = 0
    with open(path, "r", encoding="ascii", errors="ignore") as fh:
        for line in fh:
            if parser.feed(line):
                fixes += 1
                fix = parser.latest()
                print(f"  fix #{fixes}: {fix.lat:.6f},{fix.lon:.6f} "
                      f"q={fix.quality} sats={fix.satellites} hdop={fix.hdop} "
                      f"usable={fix.is_usable()}")
    last = parser.latest()
    print(f"\n共 {parser.lines_seen} 行，{fixes} 次有效 fix（{parser.fixes_seen} 句 GGA）")
    if last is None:
        print("这份录制里没有可用定位 —— 换一份重录（录制时要把天线拿到户外/窗边）")
        return 1
    return 0


def record(port: str, baud: int, out_path: str, seconds: float, pick: bool = False) -> int:
    import serial

    parser = NmeaParser()
    collected: list[tuple[float, float]] = []
    if not pick:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    started = time.monotonic()
    shown = 0
    if pick:
        print(f"取点模式：站在目标位置别动，{seconds:.0f}s 后打印可直接粘进 scenic_areas.yaml 的坐标")
    else:
        print(f"录制 {port} @ {baud} → {out_path}（Ctrl+C 提前结束）")
    try:
        with serial.Serial(port, baud, timeout=1.0) as ser:
            out = None if pick else open(out_path, "w", encoding="ascii")
            try:
                while time.monotonic() - started < seconds:
                    raw = ser.readline()
                    if not raw:
                        continue
                    text = raw.decode("ascii", errors="ignore")
                    if out is not None:
                        out.write(text if text.endswith("\n") else text + "\n")
                        out.flush()
                    if parser.feed(text):
                        shown += 1
                        fix = parser.latest()
                        collected.append((fix.lat, fix.lon))
                        print(f"[{time.monotonic() - started:6.1f}s] "
                              f"{fix.lat:.6f},{fix.lon:.6f} q={fix.quality} "
                              f"sats={fix.satellites} hdop={fix.hdop} "
                              f"usable={fix.is_usable()} precise={fix.is_precise()}")
            finally:
                if out is not None:
                    out.close()
    except KeyboardInterrupt:
        print("\n手动结束")
    except Exception as exc:
        print(f"串口读取出错：{exc}")
        print("排查：① 端口名对不对（Windows 设备管理器 / ls /dev/ttyUSB*）"
              "② 线序 TX↔RX 是不是交叉接了 ③ 权限：sudo usermod -aG dialout $USER")
        return 1

    if pick:
        return print_pick(collected, shown)
    print(f"\n录制结束：{parser.lines_seen} 行，{shown} 次有效定位 → {out_path}")
    print("把这份文件提交进仓库，它就是解析器的回归测试素材。")
    return 0


def print_pick(collected: list[tuple[float, float]], shown: int) -> int:
    """取点：用多次定位的**中位数**，比随手读最后一帧稳得多。"""
    if len(collected) < 5:
        print(f"\n只拿到 {shown} 次定位，不够用。把天线拿到户外/窗边，多等一会儿再说。")
        return 1
    lats = sorted(p[0] for p in collected)
    lons = sorted(p[1] for p in collected)
    mid = len(collected) // 2
    lat, lon = lats[mid], lons[mid]
    spread = max(max(lats) - min(lats), max(lons) - min(lons)) * 111195.08
    print(f"\n样本 {len(collected)} 次，离散度约 {spread:.1f}m "
          f"（>15m 说明星况不好，换个开阔位置重来）\n")
    print("把下面这段粘进 location/scenic_areas.yaml：\n")
    print(f'      - name: "填点位名"')
    print(f"        lat: {lat:.6f}")
    print(f"        lon: {lon:.6f}")
    print(f"        radius_m: 40")
    print()
    print("如果是整个景区，用这段（radius_m 改成中心到最远边界的距离）：\n")
    print(f'  - name: "填景区名"')
    print(f"    lat: {lat:.6f}")
    print(f"    lon: {lon:.6f}")
    print(f"    radius_m: 900")
    print(f"    coord: wgs84")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="NMEA 录制 / 回放 / 取点")
    ap.add_argument("--port", default="/dev/ttyUSB0", help="串口设备（Windows 用 COM3）")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--out", default="data/nmea_field.txt")
    ap.add_argument("--seconds", type=float, default=120.0)
    ap.add_argument("--replay", metavar="FILE", help="回放已录制的文件，不碰硬件")
    ap.add_argument("--pick", action="store_true",
                    help="取点模式：原地站定打印 WGS84 坐标，用来填 scenic_areas.yaml")
    args = ap.parse_args()

    if args.replay:
        return replay(args.replay)
    if args.pick and args.seconds == 120.0:
        args.seconds = 20.0
    return record(args.port, args.baud, args.out, args.seconds, pick=args.pick)


if __name__ == "__main__":
    raise SystemExit(main())
