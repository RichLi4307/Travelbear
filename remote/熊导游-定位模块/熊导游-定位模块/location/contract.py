"""定位模块的数据契约（D1 冻结）。

对其他模块的承诺（不要私自改）：
  1. ``get_location()`` 返回的 dict **只有** ``contract.Location`` 里的 7 个 key。
  2. ``get_location()`` **永不抛异常**、**永不阻塞**（目标 < 200ms）。
  3. ``get_location()`` 之外的任何函数都是内部实现，可以随便重构。

关于两档质量门槛（这是本模块最重要的一个设计）：
  "你在哪个景区" 和 "你在景区里哪个点位" 对精度的要求完全不同。
  景区半径几百米到几公里，GPS 误差几米根本无所谓；点位半径只有几十米，就必须卡精度。
  分两档之后，树荫下/楼缝里只有 4 颗星时，模块仍然能报出景区名，
  而不是干脆掉进 ``mode="none"`` —— 演示时这个差别是致命的。
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass
from typing import Optional

# ------------------------------------------------------------------ 质量门槛
VALID_QUALITY = (1, 2)          # GGA quality: 1=单点定位 2=差分定位

AREA_MIN_SATELLITES = 4         # 景区级：宽松，够用就行
AREA_MAX_HDOP = 10.0
SPOT_MIN_SATELLITES = 6         # 点位级：严格，否则 40m 的圆圈会被误差吞掉
SPOT_MAX_HDOP = 5.0
FIX_MAX_AGE_S = 30.0            # 超过这么久没更新，旧 fix 一律作废

# ------------------------------------------------------------------ 行为参数
BLE_MAX_AGE_S = 10.0            # 信标观测的新鲜度窗口
SWITCH_HOLD_S = 20.0            # gps ↔ ble 迟滞：切过去后先稳住 20s
GEOFENCE_EXIT_RATIO = 1.15      # 景区边界迟滞：走出 15% 才算真的离开

POI_FALLBACK = "当前位置附近"
POI_TIMEOUT_S = 5.0

MODE_GPS = "gps"
MODE_BLE = "ble"
MODE_NONE = "none"


@dataclass(frozen=True)
class Fix:
    """一次有效的 GNSS 定位。gnss.py 产出 → switch.py 消费。"""

    lat: float
    lon: float          # WGS84 原始坐标，不做任何偏移
    quality: int        # GGA 第 6 字段
    satellites: int
    hdop: float         # 未知时填 math.nan
    ts: float           # time.monotonic()，不是墙上时间

    def _base_ok(self, now: Optional[float]) -> bool:
        now = time.monotonic() if now is None else now
        return self.quality in VALID_QUALITY and (now - self.ts) <= FIX_MAX_AGE_S

    def is_usable(self, now: Optional[float] = None) -> bool:
        """景区级判定：星数够、HDOP 不离谱即可。"""
        if not self._base_ok(now):
            return False
        if self.satellites < AREA_MIN_SATELLITES:
            return False
        if not math.isnan(self.hdop) and self.hdop > AREA_MAX_HDOP:
            return False
        return True

    def is_precise(self, now: Optional[float] = None) -> bool:
        """点位级判定：只有这一档才允许报"景区内具体点位"。"""
        if not self._base_ok(now):
            return False
        if self.satellites < SPOT_MIN_SATELLITES:
            return False
        if not math.isnan(self.hdop) and self.hdop > SPOT_MAX_HDOP:
            return False
        return True


@dataclass(frozen=True)
class BeaconHit:
    """一次信标观测。ble_scan.py 产出 → switch.py 消费。"""

    uuid: str
    major: int
    minor: int
    rssi: int            # 已滤波后的 RSSI
    ts: float
    name: Optional[str] = None   # beacons.yaml 映射出的展位名

    @property
    def beacon_id(self) -> str:
        return f"{self.uuid}:{self.major}:{self.minor}"

    def is_fresh(self, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        return (now - self.ts) <= BLE_MAX_AGE_S


@dataclass(frozen=True)
class Location:
    """对外统一位置对象。这就是 get_location() 返回值的来源。"""

    mode: str
    poi_name: str
    lat: Optional[float]
    lon: Optional[float]
    beacon_id: Optional[str]
    confidence: float
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def none(cls, poi_name: str = "定位不可用") -> "Location":
        return cls(MODE_NONE, poi_name, None, None, None, 0.0, now_iso())


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
