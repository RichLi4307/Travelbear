"""契约本身：两档质量门槛 + 对外字段冻结。

纯 stdlib，不需要装任何东西，也不需要硬件。
"""
from __future__ import annotations

import math

from location.contract import (AREA_MAX_HDOP, AREA_MIN_SATELLITES, BLE_MAX_AGE_S,
                               SPOT_MAX_HDOP, SPOT_MIN_SATELLITES, BeaconHit,
                               Fix, Location)

CONTRACT_KEYS = {"mode", "poi_name", "lat", "lon", "beacon_id", "confidence", "timestamp"}


def make_fix(**over) -> Fix:
    base = dict(lat=31.3185, lon=121.3975, quality=1, satellites=9, hdop=1.2, ts=1000.0)
    base.update(over)
    return Fix(**base)


# ------------------------------------------------------------- 景区级门槛（宽松）
def test_area_gate_accepts_modest_fix():
    assert make_fix(satellites=AREA_MIN_SATELLITES, hdop=AREA_MAX_HDOP).is_usable(1000.0)


def test_area_gate_rejects_too_few_satellites():
    assert make_fix(satellites=AREA_MIN_SATELLITES - 1).is_usable(1000.0) is False


def test_area_gate_rejects_bad_hdop():
    assert make_fix(hdop=AREA_MAX_HDOP + 1).is_usable(1000.0) is False


def test_area_gate_tolerates_unknown_hdop():
    """有些模块 GGA 里 HDOP 是空的；缺数据不等于数据不合格。"""
    assert make_fix(hdop=math.nan).is_usable(1000.0) is True


def test_gates_require_valid_quality():
    for quality in (0, 3, 9):
        assert make_fix(quality=quality).is_usable(1000.0) is False
        assert make_fix(quality=quality).is_precise(1000.0) is False
    for quality in (1, 2):                 # 1=单点 2=差分
        assert make_fix(quality=quality).is_usable(1000.0) is True
        assert make_fix(quality=quality).is_precise(1000.0) is True


def test_gates_require_fresh_fix():
    fix = make_fix()
    assert fix.is_usable(1000.0 + 29.0) is True
    assert fix.is_usable(1000.0 + 31.0) is False
    assert fix.is_precise(1000.0 + 31.0) is False


# ------------------------------------------------------------- 点位级门槛（严格）
def test_precise_gate_is_stricter_than_area_gate():
    middling = make_fix(satellites=SPOT_MIN_SATELLITES - 1, hdop=SPOT_MAX_HDOP + 1)
    assert middling.is_usable(1000.0) is True       # 够判景区
    assert middling.is_precise(1000.0) is False     # 不够判点位


def test_precise_gate_accepts_good_fix():
    assert make_fix(satellites=SPOT_MIN_SATELLITES, hdop=SPOT_MAX_HDOP).is_precise(1000.0)


# ------------------------------------------------------------------- 信标新鲜度
def test_beacon_freshness_window():
    hit = BeaconHit(uuid="U", major=1, minor=1, rssi=-60, ts=1000.0)
    assert hit.is_fresh(1000.0) is True
    assert hit.is_fresh(1000.0 + BLE_MAX_AGE_S) is True
    assert hit.is_fresh(1000.0 + BLE_MAX_AGE_S + 0.1) is False


def test_beacon_id_format():
    hit = BeaconHit(uuid="FDA50693-A4E2-4FB1-AFCF-C6EB07647825",
                    major=1, minor=2, rssi=-60, ts=1000.0)
    assert hit.beacon_id == "FDA50693-A4E2-4FB1-AFCF-C6EB07647825:1:2"


# ------------------------------------------------------------------ 契约冻结
def test_location_none_shape():
    payload = Location.none().to_dict()
    assert set(payload.keys()) == CONTRACT_KEYS
    assert payload["mode"] == "none"
    assert payload["confidence"] == 0.0
    assert payload["lat"] is None and payload["lon"] is None and payload["beacon_id"] is None


def test_location_timestamp_is_iso_like():
    stamp = Location.none().to_dict()["timestamp"]
    assert len(stamp) == 19 and stamp[4] == "-" and stamp[10] == "T"
