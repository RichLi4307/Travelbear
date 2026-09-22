"""BLE 探针：扫描附近的蓝牙广播，重点看有没有 iBeacon。

三个用途：
  1. **买信标之前**先验证这台电脑的蓝牙能扫（扫得到任何设备就说明链路通）
  2. **拿到信标之后**读出它真实的 UUID / Major / Minor / TX Power，填进 beacons.yaml
  3. 用安卓手机装 Beacon Simulator 模拟发射，不花钱就能把室内链路测完

用法：
    python tools/ble_probe.py                  # 扫 10 秒，只显示有名字的设备
    python tools/ble_probe.py --seconds 20
    python tools/ble_probe.py --all            # 连无名设备也显示
    python tools/ble_probe.py --raw            # 把每个设备的原始广播字节打出来

排查提示：
  * 扫不到任何设备 → Windows 设置 > 蓝牙 确认已开启；台式机可能没有蓝牙适配器
  * 扫得到设备但扫不到你的信标 → 信标没开机，或者它不是 iBeacon（Eddystone/私有协议）
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location.ble_scan import parse_ibeacon  # noqa: E402


async def scan(seconds: float, show_all: bool, raw: bool) -> int:
    from bleak import BleakScanner

    seen: dict[str, dict] = {}

    def on_detect(device, adv) -> None:
        seen[device.address] = {
            "name": device.name or adv.local_name,
            "rssi": adv.rssi,
            "manufacturer": dict(adv.manufacturer_data or {}),
            "service_uuids": list(adv.service_uuids or []),
            "hits": seen.get(device.address, {}).get("hits", 0) + 1,
        }

    print(f"扫描 {seconds:.0f} 秒…（Ctrl+C 提前结束）\n")
    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.stop()

    if not seen:
        print("一个设备都没扫到。")
        print("  1. Windows 设置 > 蓝牙和其他设备：蓝牙开了吗？")
        print("  2. 台式机常见没有蓝牙适配器 —— 需要插一个 USB 蓝牙 4.0+ 的")
        print("  3. 驱动正常吗：设备管理器 > 蓝牙")
        return 1

    ibeacons = []
    others = []
    for address, info in seen.items():
        parsed = parse_ibeacon(info["manufacturer"])
        if parsed is not None:
            ibeacons.append((address, info, parsed))
        else:
            others.append((address, info))

    print("=" * 74)
    print(f"共扫到 {len(seen)} 个设备，其中 iBeacon {len(ibeacons)} 个")
    print("=" * 74)

    if ibeacons:
        print("\n★ iBeacon（这些才是你的模块认得出来的）\n")
        for address, info, (uuid, major, minor, tx) in ibeacons:
            print(f"  {info['rssi']:>5} dBm   {address}")
            print(f"          uuid    : {uuid}")
            print(f"          major   : {major}")
            print(f"          minor   : {minor}")
            print(f"          tx_power: {tx} dBm")
            print(f"          收到 {info['hits']} 次")
            print(f"          → 可粘进 beacons.yaml：")
            print(f"              - uuid: \"{uuid}\"")
            print(f"                major: {major}")
            print(f"                minor: {minor}")
            print(f"                name: \"填展位名\"")
            print(f"                rssi_threshold: {info['rssi'] - 10}")
            print()
    else:
        print("\n没有扫到 iBeacon。")
        print("  这不代表蓝牙坏了 —— 下面列表里有设备就说明链路是通的。")
        print("  要让模块认出来，需要：")
        print("    · 安卓手机装 Beacon Simulator，选 iBeacon 模式发射（免费，今天就能测）")
        print("    · 或者买成品 iBeacon 信标（记得要能改 UUID/Major/Minor/TX Power 的）\n")

    print("─" * 74)
    print("其他蓝牙设备（用来确认蓝牙链路正常）\n")
    shown = 0
    for address, info in sorted(others, key=lambda kv: -kv[1]["rssi"]):
        if not show_all and not info["name"]:
            continue
        shown += 1
        name = info["name"] or "(无名)"
        print(f"  {info['rssi']:>5} dBm   {name:<32} {address}   收到 {info['hits']} 次")
        if raw and info["manufacturer"]:
            for company, payload in info["manufacturer"].items():
                print(f"          厂商 0x{company:04X}: {payload.hex()}")
    if shown == 0 and others:
        print(f"  （扫到 {len(others)} 个无名设备，加 --all 可以看到）")

    print()
    print("=" * 74)
    if others or ibeacons:
        print("结论：蓝牙链路正常。室内定位这条路径今天就能测。")
        return 0
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="BLE 探针")
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--all", action="store_true", help="无名设备也显示")
    ap.add_argument("--raw", action="store_true", help="打印原始广播字节")
    args = ap.parse_args()
    try:
        return asyncio.run(scan(args.seconds, args.all, args.raw))
    except KeyboardInterrupt:
        print("\n中断")
        return 1
    except ImportError as exc:
        print(f"缺少 bleak：pip install -r requirements.txt   ({exc})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
