"""切换决策 + 分层命名的验收测试 —— **这份测试就是对外行为的验收标准**。

全部用假数据，不依赖任何硬件：D2 就能全绿。
"""
from __future__ import annotations

import math
from typing import Optional

from location.contract import (MODE_BLE, MODE_GPS, MODE_NONE, POI_FALLBACK,
                               BeaconHit, Fix, Location)
from location.geofence import GeofenceIndex
from location.switch import (CONF_AREA, CONF_BLE, CONF_ONLINE, CONF_RAW, CONF_SPOT,
                             LocationStateMachine)

CONTRACT_KEYS = {"mode", "poi_name", "lat", "lon", "beacon_id", "confidence", "timestamp"}
BEACON_UUID = "FDA50693-A4E2-4FB1-AFCF-C6EB07647825"

AREA_LAT, AREA_LON = 31.3185, 121.3975
SPOT_LAT, SPOT_LON = 31.3179, 121.3961
FAR_LAT, FAR_LON = 40.0000, 116.0000

GEOFENCE_DOC = {
    "areas": [
        {"name": "上海大学（宝山校区）", "lat": AREA_LAT, "lon": AREA_LON, "radius_m": 900,
         "spots": [{"name": "泮池", "lat": SPOT_LAT, "lon": SPOT_LON, "radius_m": 45}]},
    ]
}


class FakeClock:
    """可控时钟。用真实 time.monotonic() 没法测 20s 迟滞，测试会变成 sleep(20)。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakeGeocoder:
    def __init__(self, name: str = "上海大学-北门"):
        self.name = name
        self.calls = 0

    def poi_name(self, lat: float, lon: float) -> str:
        self.calls += 1
        return self.name


class BoomGeocoder:
    def poi_name(self, lat: float, lon: float) -> str:
        raise RuntimeError("高德 API 超时")


def make_geofence() -> GeofenceIndex:
    return GeofenceIndex.from_dict(GEOFENCE_DOC)


def north_of(lat: float, lon: float, meters: float) -> tuple[float, float]:
    """正北方向偏移若干米（同经线上 haversine 就是 R·Δφ，精确换算）。"""
    return lat + meters / 111195.08, lon


def make_fix(clock: FakeClock, *, lat: float = AREA_LAT, lon: float = AREA_LON,
             quality: int = 1, satellites: int = 9, hdop: float = 1.2,
             ts: Optional[float] = None) -> Fix:
    return Fix(lat=lat, lon=lon, quality=quality, satellites=satellites,
               hdop=hdop, ts=clock.t if ts is None else ts)


def make_hit(clock: FakeClock, *, rssi: int = -60, ts: Optional[float] = None,
             name: Optional[str] = "三号展厅-青铜器展位") -> BeaconHit:
    return BeaconHit(uuid=BEACON_UUID, major=1, minor=1, rssi=rssi,
                     ts=clock.t if ts is None else ts, name=name)


def make_sm(clock: FakeClock, geofence=None, geocoder=None) -> LocationStateMachine:
    return LocationStateMachine(geofence=geofence, geocoder=geocoder, clock=clock)


# ------------------------------------------------------------- 分层命名：点位
def test_spot_level_when_standing_on_a_spot():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    loc = sm.decide(make_fix(clock, lat=SPOT_LAT, lon=SPOT_LON), None)
    assert loc.mode == MODE_GPS
    assert loc.poi_name == "泮池"
    assert loc.confidence == CONF_SPOT


def test_area_level_when_inside_area_but_not_on_a_spot():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    lat, lon = north_of(AREA_LAT, AREA_LON, 300.0)
    loc = sm.decide(make_fix(clock, lat=lat, lon=lon), None)
    assert loc.poi_name == "上海大学（宝山校区）"
    assert loc.confidence == CONF_AREA


def test_imprecise_fix_reports_area_not_spot():
    """站在泮池正中，但只有 5 颗星：只能说到景区，不能编出点位。"""
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    loc = sm.decide(make_fix(clock, lat=SPOT_LAT, lon=SPOT_LON, satellites=5,
                             hdop=7.0), None)
    assert loc.poi_name == "上海大学（宝山校区）"
    assert loc.confidence == CONF_AREA


# ------------------------------------------------------------- 分层命名：兜底
def test_online_geocoder_used_only_when_geofence_misses():
    clock = FakeClock()
    geocoder = FakeGeocoder("某个陌生公园")
    sm = make_sm(clock, make_geofence(), geocoder)
    loc = sm.decide(make_fix(clock, lat=FAR_LAT, lon=FAR_LON), None)
    assert loc.poi_name == "某个陌生公园"
    assert loc.confidence == CONF_ONLINE
    assert geocoder.calls == 1


def test_geofence_hit_never_calls_the_online_api():
    """本地能认出来就别花钱调 API —— 省配额、省延迟、还省得断网翻车。"""
    clock = FakeClock()
    geocoder = FakeGeocoder()
    sm = make_sm(clock, make_geofence(), geocoder)
    sm.decide(make_fix(clock, lat=SPOT_LAT, lon=SPOT_LON), None)
    assert geocoder.calls == 0


def test_raw_fallback_when_nothing_recognizes_the_place():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence(), FakeGeocoder(POI_FALLBACK))
    loc = sm.decide(make_fix(clock, lat=FAR_LAT, lon=FAR_LON), None)
    assert loc.mode == MODE_GPS
    assert loc.poi_name == POI_FALLBACK
    assert loc.confidence == CONF_RAW


def test_no_geofence_and_no_geocoder_still_returns_gps():
    clock = FakeClock()
    sm = make_sm(clock, None, None)
    loc = sm.decide(make_fix(clock), None)
    assert loc.mode == MODE_GPS
    assert loc.poi_name == POI_FALLBACK
    assert loc.confidence == CONF_RAW


def test_geocoder_exception_does_not_break_gps():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence(), BoomGeocoder())
    loc = sm.decide(make_fix(clock, lat=FAR_LAT, lon=FAR_LON), None)
    assert loc.mode == MODE_GPS
    assert loc.poi_name == POI_FALLBACK


# --------------------------------------------------------------- 景区边界迟滞
def test_area_name_survives_a_stroll_just_outside_the_fence():
    """沿景区边界走路时，景区名不该一跳一跳的。"""
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    assert sm.decide(make_fix(clock), None).poi_name == "上海大学（宝山校区）"

    lat, lon = north_of(AREA_LAT, AREA_LON, 950.0)     # 半径 900，已出界
    assert sm.decide(make_fix(clock, lat=lat, lon=lon), None).poi_name == "上海大学（宝山校区）"


def test_area_name_cleared_when_truly_gone():
    clock = FakeClock()
    geocoder = FakeGeocoder("别的地方")
    sm = make_sm(clock, make_geofence(), geocoder)
    sm.decide(make_fix(clock), None)

    lat, lon = north_of(AREA_LAT, AREA_LON, 1100.0)    # 超出 exit 1035
    loc = sm.decide(make_fix(clock, lat=lat, lon=lon), None)
    assert loc.poi_name == "别的地方"
    assert sm.area_name is None


# ------------------------------------------------------------------ 降级到 BLE
def test_falls_back_to_ble_when_satellites_too_few():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence(), FakeGeocoder())
    loc = sm.decide(make_fix(clock, satellites=2), make_hit(clock))
    assert loc.mode == MODE_BLE
    assert loc.poi_name == "三号展厅-青铜器展位"
    assert loc.beacon_id == f"{BEACON_UUID}:1:1"
    assert loc.lat is None and loc.lon is None
    assert loc.confidence == CONF_BLE


def test_falls_back_to_ble_when_quality_is_zero():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    assert sm.decide(make_fix(clock, quality=0), make_hit(clock)).mode == MODE_BLE


def test_falls_back_to_ble_when_hdop_is_absurd():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    assert sm.decide(make_fix(clock, hdop=20.0), make_hit(clock)).mode == MODE_BLE


def test_falls_back_to_ble_when_fix_is_stale():
    """走进室内 30s 后旧 fix 必须作废，否则室内会念出室外的景区。"""
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    stale = make_fix(clock, ts=clock.t - 100.0)
    assert sm.decide(stale, make_hit(clock)).mode == MODE_BLE


def test_ble_without_name_falls_back_to_beacon_id():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    loc = sm.decide(None, make_hit(clock, name=None))
    assert loc.poi_name == f"{BEACON_UUID}:1:1"


# ------------------------------------------------------------------------ none
def test_none_when_both_absent_and_never_raises():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence(), FakeGeocoder())
    loc = sm.decide(None, None)
    assert loc.mode == MODE_NONE
    assert loc.poi_name == "定位不可用"
    assert loc.confidence == 0.0
    assert loc.lat is None and loc.lon is None and loc.beacon_id is None


def test_none_with_stale_beacon():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    assert sm.decide(None, make_hit(clock, ts=clock.t - 60.0)).mode == MODE_NONE


# ----------------------------------------------------------------------- 迟滞
def test_hysteresis_holds_ble_for_20s():
    """刚切到 BLE 后 20s 内，即使 GPS 回来了也不许切回去。"""
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    assert sm.decide(None, make_hit(clock)).mode == MODE_BLE

    clock.advance(10.0)
    assert sm.decide(make_fix(clock), make_hit(clock)).mode == MODE_BLE

    clock.advance(11.0)                                  # 累计 21s > 20s
    assert sm.decide(make_fix(clock), make_hit(clock)).mode == MODE_GPS


def test_no_hysteresis_after_falling_back_to_none():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    sm.decide(None, make_hit(clock))
    clock.advance(30.0)
    assert sm.decide(None, None).mode == MODE_NONE
    clock.advance(1.0)
    assert sm.decide(make_fix(clock), None).mode == MODE_GPS


def test_nan_hdop_is_tolerated():
    """有些模块 GGA 里 HDOP 是空的；缺数据不等于数据不合格。"""
    clock = FakeClock()
    sm = make_sm(clock, make_geofence())
    assert sm.decide(make_fix(clock, hdop=math.nan), make_hit(clock)).mode == MODE_GPS


# ------------------------------------------------------------------ 契约冻结
def test_contract_keys_are_frozen():
    assert set(Location.none().to_dict().keys()) == CONTRACT_KEYS


def test_decide_returns_exact_contract_shape():
    clock = FakeClock()
    sm = make_sm(clock, make_geofence(), FakeGeocoder())
    for loc in (sm.decide(make_fix(clock), None), sm.decide(None, None)):
        assert set(loc.to_dict().keys()) == CONTRACT_KEYS
