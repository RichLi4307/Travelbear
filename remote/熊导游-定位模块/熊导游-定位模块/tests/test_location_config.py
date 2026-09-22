"""校验仓库里真实在用的配置文件。

配置文件写错是演示当天最常见的翻车方式，而且往往到现场才发现。
这个文件在 D1 就能把这些错全拦住。
"""
from __future__ import annotations

import os
import uuid as uuid_mod

from location.ble_scan import BeaconRegistry
from location.geofence import GeofenceIndex, haversine_m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AREAS_YAML = os.path.join(ROOT, "location", "scenic_areas.yaml")
BEACONS_YAML = os.path.join(ROOT, "location", "beacons.yaml")

EXPECTED_AREAS = ["外滩", "静安寺", "广富林遗址文化公园", "滴水湖", "上海大学（宝山校区）"]


def load_index() -> GeofenceIndex:
    return GeofenceIndex.load(AREAS_YAML)


# ══════════════════════════════════════════════════════ scenic_areas.yaml
def test_areas_yaml_loads_and_has_the_five_demo_areas():
    index = load_index()
    assert [a.name for a in index.areas] == EXPECTED_AREAS


def test_every_area_has_sane_radius_and_coordinates():
    for area in load_index().areas:
        assert area.radius_m > 0, f"{area.name} 半径没填"
        assert 30.6 <= area.lat <= 31.6, f"{area.name} 纬度不在上海范围"
        assert 120.8 <= area.lon <= 122.1, f"{area.name} 经度不在上海范围"


def test_area_names_are_unique():
    names = [a.name for a in load_index().areas]
    assert len(names) == len(set(names))


def test_every_spot_sits_inside_its_own_area():
    """点位跑到景区围栏外面 = 永远不可能命中，这是最容易犯的配置错误。"""
    for area in load_index().areas:
        for spot in area.spots:
            distance = haversine_m(area.lat, area.lon, spot.lat, spot.lon)
            assert distance <= area.radius_m + spot.radius_m, (
                f"点位「{spot.name}」距「{area.name}」中心 {distance:.0f}m，"
                f"超出围栏半径 {area.radius_m:.0f}m")


def test_every_spot_has_positive_radius():
    for area in load_index().areas:
        for spot in area.spots:
            assert spot.radius_m > 0, f"{area.name}/{spot.name} 半径没填"


def test_spot_names_unique_within_area():
    for area in load_index().areas:
        names = [s.name for s in area.spots]
        assert len(names) == len(set(names)), f"{area.name} 有重名点位"


def test_each_area_center_resolves_to_itself():
    """站在景区正中心，必须判成这个景区 —— 这一条能抓出坐标填反、经纬度写颠倒。"""
    index = load_index()
    for area in index.areas:
        match = index.locate(area.lat, area.lon, precise=True)
        assert match is not None, f"站在「{area.name}」中心竟然没命中"
        assert match.area.name == area.name, (
            f"站在「{area.name}」中心被判成了「{match.area.name}」")


def test_area_center_is_distinct_between_areas():
    """两个景区圆心重合 = 半径小的永远赢，另一个形同虚设。"""
    areas = load_index().areas
    for i, a in enumerate(areas):
        for b in areas[i + 1:]:
            assert haversine_m(a.lat, a.lon, b.lat, b.lon) > 500, (
                f"「{a.name}」和「{b.name}」圆心离得太近")


# ══════════════════════════════════════════════════════ beacons.yaml
def test_beacons_yaml_loads():
    registry = BeaconRegistry.load(BEACONS_YAML)
    assert len(registry) >= 1
    assert registry.default_threshold < 0


def test_every_beacon_uuid_parses():
    registry = BeaconRegistry.load(BEACONS_YAML)
    for (beacon_uuid, major, minor), entry in registry._map.items():
        try:
            uuid_mod.UUID(beacon_uuid)
        except ValueError:
            raise AssertionError(f"信标 UUID 不合法：{beacon_uuid}")
        assert major >= 0 and minor >= 0
        assert entry.get("name"), f"{beacon_uuid}:{major}:{minor} 没有展位名"


def test_every_beacon_threshold_is_negative():
    registry = BeaconRegistry.load(BEACONS_YAML)
    for key in registry._map:
        assert registry.threshold_for(*key) < 0, f"{key} 的 rssi_threshold 应该是负数 dBm"
