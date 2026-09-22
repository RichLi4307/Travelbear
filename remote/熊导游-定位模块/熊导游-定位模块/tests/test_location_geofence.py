"""本地围栏：距离、坐标系折算、景区/点位判定、边界迟滞。

全部纯本地计算，不需要 GPS、不需要网络。
"""
from __future__ import annotations

import math

from location.geofence import (GeofenceIndex, gcj02_to_wgs84, haversine_m,
                               wgs84_to_gcj02)

# 上海大学（宝山校区）一带的示例坐标
AREA_LAT, AREA_LON = 31.3185, 121.3975
SPOT_LAT, SPOT_LON = 31.3179, 121.3961

# R·π/180，即 1° 纬度对应的米数（R = 6371008.8）
M_PER_DEG_LAT = 111195.08

BASE_DOC = {
    "areas": [
        {"name": "上大", "lat": AREA_LAT, "lon": AREA_LON, "radius_m": 900,
         "spots": [{"name": "泮池", "lat": SPOT_LAT, "lon": SPOT_LON, "radius_m": 45}]},
        {"name": "顾村公园", "lat": 31.3772, "lon": 121.4005, "radius_m": 1200, "spots": []},
    ]
}

NESTED_DOC = {
    "areas": [
        {"name": "大景区", "lat": AREA_LAT, "lon": AREA_LON, "radius_m": 3000, "spots": []},
        {"name": "上大", "lat": AREA_LAT, "lon": AREA_LON, "radius_m": 900, "spots": []},
    ]
}


def make_index(doc: dict = BASE_DOC) -> GeofenceIndex:
    return GeofenceIndex.from_dict(doc)


def north_of(lat: float, lon: float, meters: float) -> tuple[float, float]:
    """正北方向偏移若干米。同经线上 haversine 就是 R·Δφ，所以这是精确换算，
    不是近似 —— 边界迟滞的测试要卡在 900m/1035m 这种位置，近似会误事。"""
    return lat + meters / M_PER_DEG_LAT, lon


# ---------------------------------------------------------------------- 距离
def test_haversine_one_degree_latitude():
    assert abs(haversine_m(0.0, 0.0, 1.0, 0.0) - M_PER_DEG_LAT) < 0.05


def test_haversine_zero_distance():
    assert haversine_m(AREA_LAT, AREA_LON, AREA_LAT, AREA_LON) == 0.0


def test_haversine_matches_north_of_helper():
    lat, lon = north_of(AREA_LAT, AREA_LON, 100.0)
    assert abs(haversine_m(AREA_LAT, AREA_LON, lat, lon) - 100.0) < 0.001


# ------------------------------------------------------------- 坐标系折算
def test_gcj02_roundtrip_within_one_meter():
    glat, glon = wgs84_to_gcj02(AREA_LAT, AREA_LON)
    back_lat, back_lon = gcj02_to_wgs84(glat, glon)
    assert haversine_m(AREA_LAT, AREA_LON, back_lat, back_lon) < 1.0


def test_gcj02_offset_in_china_is_hundreds_of_meters():
    """这就是不能用高德拾取器坐标直接当 WGS84 用的原因。"""
    glat, glon = wgs84_to_gcj02(AREA_LAT, AREA_LON)
    offset = haversine_m(AREA_LAT, AREA_LON, glat, glon)
    assert 100.0 < offset < 1500.0


def test_no_shift_outside_china():
    assert wgs84_to_gcj02(48.8566, 2.3522) == (48.8566, 2.3522)      # 巴黎
    assert gcj02_to_wgs84(48.8566, 2.3522) == (48.8566, 2.3522)


def test_from_dict_converts_gcj02_entries():
    glat, glon = wgs84_to_gcj02(AREA_LAT, AREA_LON)
    doc = {"areas": [{"name": "上大", "lat": glat, "lon": glon,
                      "radius_m": 900, "coord": "gcj02", "spots": []}]}
    area = make_index(doc).areas[0]
    assert haversine_m(area.lat, area.lon, AREA_LAT, AREA_LON) < 1.0


def test_from_dict_keeps_wgs84_by_default():
    area = make_index().areas[0]
    assert (area.lat, area.lon) == (AREA_LAT, AREA_LON)


# ------------------------------------------------------------------ 命中判定
def test_locate_spot_when_inside_and_precise():
    match = make_index().locate(SPOT_LAT, SPOT_LON, precise=True)
    assert match is not None
    assert match.level == "spot"
    assert match.name == "泮池"
    assert match.area.name == "上大"


def test_locate_area_when_inside_area_but_not_in_any_spot():
    lat, lon = north_of(AREA_LAT, AREA_LON, 300.0)
    match = make_index().locate(lat, lon, precise=True)
    assert match is not None
    assert match.level == "area"
    assert match.name == "上大"


def test_not_precise_never_reports_a_spot():
    """站在泮池正中间，但只有 5 颗星 —— 只能说"你在上大"，不能说"你在泮池"。"""
    match = make_index().locate(SPOT_LAT, SPOT_LON, precise=False)
    assert match is not None
    assert match.level == "area"
    assert match.name == "上大"


def test_locate_returns_none_outside_every_area():
    assert make_index().locate(40.0, 116.0) is None


def test_locate_picks_smallest_containing_area():
    """嵌套景区（大景区里套小景区）要报更具体的那个。"""
    match = make_index(NESTED_DOC).locate(AREA_LAT, AREA_LON)
    assert match is not None
    assert match.name == "上大"


def test_locate_far_area():
    match = make_index().locate(31.3772, 121.4005)
    assert match is not None
    assert match.name == "顾村公园"


def test_empty_index_never_matches():
    empty = GeofenceIndex([])
    assert empty.is_empty()
    assert empty.locate(AREA_LAT, AREA_LON) is None


# ------------------------------------------------------------------ 边界迟滞
def test_exit_radius_is_15_percent_larger():
    area = make_index().areas[0]
    assert area.exit_radius_m == 900 * 1.15


def test_sticky_keeps_area_beyond_radius():
    """沿景区边界走路时，走出围栏一点点不该让景区名消失。"""
    index = make_index()
    lat, lon = north_of(AREA_LAT, AREA_LON, 950.0)     # 半径 900，已出界；exit 1035 内

    assert index.locate(lat, lon) is None
    sticky = index.locate(lat, lon, sticky="上大")
    assert sticky is not None
    assert sticky.name == "上大"
    assert sticky.level == "area"


def test_sticky_released_beyond_exit_radius():
    index = make_index()
    lat, lon = north_of(AREA_LAT, AREA_LON, 1100.0)    # 超出 exit 1035
    assert index.locate(lat, lon, sticky="上大") is None


def test_sticky_cannot_conjure_a_match_from_far_away():
    index = make_index()
    lat, lon = north_of(AREA_LAT, AREA_LON, 5000.0)
    assert index.locate(lat, lon, sticky="上大") is None


def test_sticky_for_other_area_does_not_apply():
    index = make_index()
    lat, lon = north_of(AREA_LAT, AREA_LON, 950.0)
    assert index.locate(lat, lon, sticky="顾村公园") is None


def test_spot_wins_over_sticky_area():
    index = make_index()
    match = index.locate(SPOT_LAT, SPOT_LON, precise=True, sticky="上大")
    assert match is not None and match.level == "spot"
