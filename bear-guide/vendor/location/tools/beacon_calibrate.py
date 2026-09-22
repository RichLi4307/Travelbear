"""现场标定：实时打印每个信标的 RSSI，用来定 rssi_threshold 和调 TX Power。

它**故意不过滤**阈值 —— 标定的时候你要看见"太弱"的那些值，才能划线。

用法：
    python tools/beacon_calibrate.py                          # 站在展位正中
    python tools/beacon_calibrate.py --beacons location/beacons.yaml

标定步骤：
    1. 站在展位正中跑 30s，记下该信标稳定后的中位值（比如 -62）
    2. 走到展位边界（观众真正触发讲解的位置），再记一个值（比如 -78）
    3. rssi_threshold 取偏保守的中间值（比如 -72），回填 beacons.yaml
    4. 同时用厂商 App 把信标 TX Power 调小：覆盖范围小了，阈值就不用抠得那么准

按 Ctrl+C 退出。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location.ble_scan import parse_ibeacon  # noqa: E402

REFRESH_S = 1.0
WINDOW = 7


def load_registry(path: str):
    try:
        from location.ble_scan import BeaconRegistry
        return BeaconRegistry.load(path)
    except Exception as exc:
        print(f"（beacons.yaml 没读上，只打印原始 RSSI：{exc}）")
        return None


def render(samples, meta, tx_power, registry, elapsed: float) -> None:
    os.system("cls" if os.name == "nt" else "clear")
    print(f"信标标定  |  已扫描 {elapsed:5.0f}s  |  发现 {len(samples)} 个信标")
    print()
    if not samples:
        print("  没扫到 iBeacon。逐项排查：")
        print("   1. 信标开机了吗？（很多成品信标要按一下才广播）")
        print("   2. 它是 iBeacon 吗？Eddystone / 私有协议的扫不出来")
        print("   3. 笔记本蓝牙开着吗？Windows 设 > 蓝牙，或 rfkill unblock bluetooth")
        return
    print(f"  {'RSSI':>6}  {'样本':>4}  {'txPwr':>5}  展位名 / 备注")
    print("  " + "-" * 66)
    for bid in sorted(samples, key=lambda b: int(statistics.median(samples[b]))):
        buf = samples[bid]
        smoothed = int(statistics.median(buf))
        name = registry.name_for(*meta[bid]) if registry else None
        threshold = registry.threshold_for(*meta[bid]) if registry else -75
        mark = "OK 在阈值内" if smoothed >= threshold else f"太弱 < {threshold}"
        label = name or "（未映射，需要写进 beacons.yaml）"
        print(f"  {smoothed:>6}  {len(buf):>4}  {tx_power[bid]:>5}  {label}   [{mark}]")
    print()
    print("  把稳定值回填 beacons.yaml 的 rssi_threshold：")
    print("  取 展位边界值 与 展位正中值 之间偏保守的那个数。")


async def calibrate(beacons_path: str) -> None:
    from bleak import BleakScanner

    registry = load_registry(beacons_path)
    samples: dict[str, list[int]] = {}
    meta: dict[str, tuple[str, int, int]] = {}
    tx_power: dict[str, int] = {}
    start = time.monotonic()

    def on_detect(device, adv) -> None:
        parsed = parse_ibeacon(adv.manufacturer_data)
        if parsed is None:
            return
        beacon_uuid, major, minor, tx = parsed
        bid = f"{beacon_uuid}:{major}:{minor}"
        meta[bid] = (beacon_uuid, major, minor)
        tx_power[bid] = tx
        buf = samples.setdefault(bid, [])
        buf.append(int(adv.rssi))
        del buf[:-WINDOW]

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    print(f"扫描中…（映射表：{beacons_path}）按 Ctrl+C 退出")
    try:
        while True:
            await asyncio.sleep(REFRESH_S)
            render(samples, meta, tx_power, registry, time.monotonic() - start)
    finally:
        await scanner.stop()


def main() -> int:
    default_yaml = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "location", "beacons.yaml")
    ap = argparse.ArgumentParser(description="iBeacon 现场标定")
    ap.add_argument("--beacons", default=default_yaml)
    args = ap.parse_args()
    try:
        asyncio.run(calibrate(args.beacons))
    except KeyboardInterrupt:
        print("\n结束")
    except ImportError:
        print("缺少 bleak：pip install -r requirements.txt")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
