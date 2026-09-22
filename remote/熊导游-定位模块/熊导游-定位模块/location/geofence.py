"""本地围栏：判断"在哪个景区"以及"在景区里哪个点位"。

这是室外定位的**主路径**，全部是本地计算：
  * 不过河、不联网 → 毫秒级出结果，断网也能用
  * 不消耗高德配额
  * 可以完全离线测试

三层优先级（由 switch.py 组合）：
  1. 景区内点位（要求精度够，半径通常 20–60m）
  2. 景区（半径几百米到几公里，精度要求宽松）
  3. 在线反解兜底（本地一个都没命中时才去问高德）

坐标系说明（踩过的坑，写在最前面）：
  GNSS 模块输出 **WGS84**；高德/腾讯坐标拾取器给的是 **GCJ-02（火星坐标）**，
  两者在中国境内相差 **300–600m**。景区半径 800m 时这点偏差能忍，
  点位半径 40m 时就是致命的 —— 你会永远进不了那个圈。
  所以配置文件里用 `coord: gcj02` 显式声明来源，加载时自动折算成 WGS84。
  最稳的取点方式还是拿自己的 GNSS 模块实测（tools/nmea_recorder.py --pick）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .contract import GEOFENCE_EXIT_RATIO

EARTH_R_M = 6371008.8


# --------------------------------------------------------------------- 距离
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """两点球面距离（米）。点位半径只有几十米，用等距圆柱近似也行，
    但 haversine 一样快且不用分情况讨论。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2.0) ** 2)
    return 2.0 * EARTH_R_M * math.asin(math.sqrt(a))


# ------------------------------------------------------- WGS84 ↔ GCJ-02 转换
_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lat: float, lon: float) -> bool:
    return not (0.8293 <= lat <= 55.8271 and 72.004 <= lon <= 137.8347)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    if _out_of_china(lat, lon):
        return lat, lon
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = 1 - _EE * math.sin(rad_lat) ** 2
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    dlon = (dlon * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + dlat, lon + dlon


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """迭代反解，误差 < 1m。高德拾取器复制来的坐标走这里。"""
    if _out_of_china(lat, lon):
        return lat, lon
    wlat, wlon = lat, lon
    for _ in range(3):
        glat, glon = wgs84_to_gcj02(wlat, wlon)
        wlat += lat - glat
        wlon += lon - glon
    return wlat, wlon


def _normalize(lat: float, lon: float, coord: str) -> tuple[float, float]:
    if str(coord or "wgs84").lower() in ("gcj02", "gcj-02", "amap", "gaode", "火星"):
        return gcj02_to_wgs84(lat, lon)
    return lat, lon


# ------------------------------------------------------------------ 数据结构
@dataclass(frozen=True)
class Spot:
    """景区内的一个点位，比如"泮池""北门""图书馆"。"""

    name: str
    lat: float
    lon: float
    radius_m: float

    def distance_m(self, lat: float, lon: float) -> float:
        return haversine_m(self.lat, self.lon, lat, lon)

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_m(lat, lon) <= self.radius_m


@dataclass(frozen=True)
class ScenicArea:
    """一个景区围栏。`spots` 是它内部的细分点位。"""

    name: str
    lat: float
    lon: float
    radius_m: float
    spots: tuple[Spot, ...] = field(default_factory=tuple)

    @property
    def exit_radius_m(self) -> float:
        return self.radius_m * GEOFENCE_EXIT_RATIO

    def distance_m(self, lat: float, lon: float) -> float:
        return haversine_m(self.lat, self.lon, lat, lon)

    def contains(self, lat: float, lon: float) -> bool:
        return self.distance_m(lat, lon) <= self.radius_m

    def contains_with_exit_band(self, lat: float, lon: float) -> bool:
        return self.distance_m(lat, lon) <= self.exit_radius_m


@dataclass(frozen=True)
class GeofenceMatch:
    """一次命中结果。``level`` 告诉你这次结论有多可信。"""

    area: ScenicArea
    spot: Optional[Spot]
    distance_m: float
    level: str          # "spot" | "area"

    @property
    def name(self) -> str:
        return self.spot.name if self.spot is not None else self.area.name


# ---------------------------------------------------------------------- 索引
class GeofenceIndex:
    def __init__(self, areas: Iterable[ScenicArea]):
        self.areas: tuple[ScenicArea, ...] = tuple(areas)
        self._spots: tuple[tuple[ScenicArea, Spot], ...] = tuple(
            (area, spot) for area in self.areas for spot in area.spots)

    def __len__(self) -> int:
        return len(self.areas)

    def is_empty(self) -> bool:
        return not self.areas

    @property
    def spot_count(self) -> int:
        return len(self._spots)

    # ------------------------------------------------------------ 构造
    @classmethod
    def from_dict(cls, doc: dict) -> "GeofenceIndex":
        """从已解析的 yaml dict 构建。单独拆出来是为了能在没有 PyYAML 时测试。"""
        areas = []
        for raw_area in (doc or {}).get("areas", []) or []:
            area_lat, area_lon = _normalize(
                float(raw_area["lat"]), float(raw_area["lon"]),
                raw_area.get("coord", "wgs84"))
            spots = []
            for raw_spot in raw_area.get("spots", []) or []:
                spot_lat, spot_lon = _normalize(
                    float(raw_spot["lat"]), float(raw_spot["lon"]),
                    raw_spot.get("coord", raw_area.get("coord", "wgs84")))
                spots.append(Spot(name=str(raw_spot["name"]), lat=spot_lat, lon=spot_lon,
                                  radius_m=float(raw_spot.get("radius_m", 40))))
            areas.append(ScenicArea(name=str(raw_area["name"]), lat=area_lat, lon=area_lon,
                                    radius_m=float(raw_area["radius_m"]),
                                    spots=tuple(spots)))
        return cls(areas)

    @classmethod
    def load(cls, path: str) -> "GeofenceIndex":
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        return cls.from_dict(doc)

    # ------------------------------------------------------------ 判定
    def locate(self, lat: float, lon: float, *, precise: bool = True,
               sticky: Optional[str] = None) -> Optional[GeofenceMatch]:
        """判定当前位置。

        precise=False 时**不会**报点位 —— 即使你确实站在点位圈里。
        理由：精度不够时，那个圈是假的，报出来就是编。

        sticky 传上一轮命中的景区名，用来做边界迟滞：走出围栏但还在
        exit_radius（= 半径 ×1.15）以内时，仍然算在这个景区里，
        避免沿边界走路时 mode/名称疯狂抖动。
        """
        if precise:
            best: Optional[tuple[ScenicArea, Spot, float]] = None
            for area, spot in self._spots:
                distance = spot.distance_m(lat, lon)
                if distance <= spot.radius_m and (best is None or distance < best[2]):
                    best = (area, spot, distance)
            if best is not None:
                return GeofenceMatch(area=best[0], spot=best[1],
                                     distance_m=best[2], level="spot")

        best_area: Optional[tuple[ScenicArea, float]] = None
        for area in self.areas:
            distance = area.distance_m(lat, lon)
            inside = (distance <= area.radius_m
                      or (sticky is not None and sticky == area.name
                          and distance <= area.exit_radius_m))
            if not inside:
                continue
            # 嵌套景区（大景区里套小景区）取更具体的那个
            if best_area is None or area.radius_m < best_area[0].radius_m:
                best_area = (area, distance)
        if best_area is not None:
            return GeofenceMatch(area=best_area[0], spot=None,
                                 distance_m=best_area[1], level="area")
        return None
