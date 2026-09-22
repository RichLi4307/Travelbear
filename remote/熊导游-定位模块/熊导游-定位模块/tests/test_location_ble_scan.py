"""iBeacon 解析 / RSSI 滤波 / 信标映射。

全部不依赖蓝牙硬件 —— 这是它能提前做完的原因。
"""
from __future__ import annotations

import uuid as uuid_mod

from location.ble_scan import (APPLE_COMPANY_ID, BeaconRegistry, BleScanner,
                               RssiFilter, parse_ibeacon)


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


BEACON_UUID = "FDA50693-A4E2-4FB1-AFCF-C6EB07647825"
OTHER_UUID = "E2C56DB5-DFFB-48D2-B060-D0F5A71096E0"


def make_ibeacon_payload(beacon_uuid: str = BEACON_UUID, major: int = 1,
                         minor: int = 1, tx_power: int = -59) -> bytes:
    """拼一个标准 iBeacon 广播载荷：02 15 <16B UUID> <2B major> <2B minor> <1B tx>"""
    return (bytes([0x02, 0x15])
            + uuid_mod.UUID(beacon_uuid).bytes
            + major.to_bytes(2, "big")
            + minor.to_bytes(2, "big")
            + tx_power.to_bytes(1, "big", signed=True))


def make_adv(beacon_uuid: str = BEACON_UUID, major: int = 1, minor: int = 1) -> dict:
    return {APPLE_COMPANY_ID: make_ibeacon_payload(beacon_uuid, major, minor)}


def make_registry() -> BeaconRegistry:
    return BeaconRegistry([
        {"uuid": BEACON_UUID, "major": 1, "minor": 1,
         "name": "三号展厅-青铜器展位", "rssi_threshold": -70},
        {"uuid": OTHER_UUID, "major": 2, "minor": 1, "name": "一号展厅-入口展位"},
    ], default_threshold=-75)


# ------------------------------------------------------------------ 广播包解析
def test_parse_ibeacon_happy_path():
    assert parse_ibeacon(make_adv()) == (BEACON_UUID, 1, 1, -59)


def test_parse_ibeacon_various_major_minor():
    assert parse_ibeacon(make_adv(major=2, minor=7)) == (BEACON_UUID, 2, 7, -59)


def test_parse_ibeacon_ignores_other_company():
    """别家厂商的私有广播（比如小米手环）不能把我们搞崩。"""
    assert parse_ibeacon({0x0157: make_ibeacon_payload()}) is None


def test_parse_ibeacon_ignores_eddystone_and_short_payloads():
    assert parse_ibeacon(None) is None
    assert parse_ibeacon({}) is None
    assert parse_ibeacon({APPLE_COMPANY_ID: b""}) is None
    assert parse_ibeacon({APPLE_COMPANY_ID: bytes([0x02, 0x15, 0x00])}) is None
    # Apple 的其它广播（比如 AirDrop / Handoff）type 不是 0x02
    assert parse_ibeacon({APPLE_COMPANY_ID: bytes([0x0C, 0x0E]) + bytes(30)}) is None


# --------------------------------------------------------------------- 滤波
def test_rssi_filter_takes_median():
    f = RssiFilter(window=5)
    for value in (-60, -61, -95, -59, -62):     # -95 是深衰落，必须被压掉
        smoothed = f.push("b", value)
    assert smoothed == -61


def test_rssi_filter_window_slides():
    f = RssiFilter(window=3)
    for value in (-50, -50, -50):
        f.push("b", value)
    assert f.push("b", -80) == -50              # 窗口里还剩两个 -50


def test_rssi_filter_is_per_beacon():
    f = RssiFilter(window=3)
    f.push("a", -40)
    assert f.push("b", -80) == -80


# ------------------------------------------------------------------- 映射表
def test_registry_maps_name_case_insensitively():
    reg = make_registry()
    assert reg.name_for(BEACON_UUID.lower(), 1, 1) == "三号展厅-青铜器展位"
    assert reg.name_for(BEACON_UUID.upper(), 1, 1) == "三号展厅-青铜器展位"


def test_registry_unknown_beacon_has_no_name():
    reg = make_registry()
    assert reg.name_for(BEACON_UUID, 9, 9) is None


def test_registry_per_beacon_threshold_overrides_default():
    reg = make_registry()
    assert reg.threshold_for(BEACON_UUID, 1, 1) == -70      # 单独指定
    assert reg.threshold_for(OTHER_UUID, 2, 1) == -75       # 落回默认


# -------------------------------------------------------------------- 扫描器
def test_scanner_accepts_strong_beacon():
    scanner = BleScanner(make_registry())
    hit = scanner.feed_advertisement(make_adv(), -60)
    assert hit is not None
    assert hit.name == "三号展厅-青铜器展位"
    assert hit.rssi == -60
    assert hit.beacon_id == f"{BEACON_UUID}:1:1"


def test_scanner_ignores_beacon_below_threshold():
    """太弱的观测意味着"你在别的展位"，必须丢弃，否则会隔墙触发讲解。"""
    scanner = BleScanner(make_registry())
    assert scanner.feed_advertisement(make_adv(), -85) is None
    assert scanner.nearest() is None


def test_scanner_ignores_non_ibeacon_traffic():
    scanner = BleScanner(make_registry())
    assert scanner.feed_advertisement({0x0157: b"\x01\x02\x03"}, -40) is None
    assert scanner.nearest() is None


def test_scanner_nearest_picks_strongest():
    scanner = BleScanner(make_registry())
    scanner.feed_advertisement(make_adv(BEACON_UUID, 1, 1), -70)
    scanner.feed_advertisement(make_adv(OTHER_UUID, 2, 1), -55)

    nearest = scanner.nearest()

    assert nearest is not None
    assert nearest.name == "一号展厅-入口展位"


def test_scanner_nearest_is_none_when_everything_stale():
    """走出展位 10s 后，旧观测必须过期，否则会对着空气念上一个展位的讲解。"""
    clock = FakeClock()
    scanner = BleScanner(make_registry(), clock=clock)
    scanner.feed_advertisement(make_adv(), -60)
    assert scanner.nearest() is not None

    clock.advance(11.0)
    assert scanner.nearest() is None


def test_scanner_snapshot_returns_filtered_rssi():
    scanner = BleScanner(make_registry())
    scanner.feed_advertisement(make_adv(), -60)
    assert scanner.snapshot() == {f"{BEACON_UUID}:1:1": -60}


def test_scanner_threshold_override_at_construction():
    scanner = BleScanner(make_registry(), rssi_threshold=-40)
    assert scanner.feed_advertisement(make_adv(), -60) is None
