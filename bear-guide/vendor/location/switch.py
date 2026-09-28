"""决策层：GPS / BLE / NONE 三态切换 + 两层迟滞 + 分层位置命名。

这是**纯逻辑** —— 不碰硬件、不碰网络，所以它在硬件到货前就能写完并测通。
单人开发时，这是你最先该做完的那一块（用 mock 喂假数据）。

位置命名的优先级（室外路径）：
    1. 景区内点位   "泮池"                 ← 本地围栏，要求精度够（is_precise）
    2. 景区         "上海大学（宝山校区）"   ← 本地围栏，精度要求宽松（is_usable）
    3. 在线反解     "XX公园北门"            ← 高德兜底，只在本地一个都没命中时才问
    4. 兜底         "当前位置附近"          ← 有坐标但认不出地方

两条迟滞（都必要，缺一个演示现场就会当众翻车）：
  * gps ↔ ble：切到 BLE 后 20s 内不许切回 GPS（否则在门口来回走会疯狂切换）
  * 景区边界：走出围栏但还在 1.15 倍半径内仍算在这个景区里（否则沿边界走时景区名乱跳）
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional, Protocol

from .contract import (MODE_BLE, MODE_GPS, MODE_NONE, POI_FALLBACK, SWITCH_HOLD_S,
                       BeaconHit, Fix, Location, now_iso)
from .geofence import GeofenceIndex

log = logging.getLogger(__name__)

# confidence 分层：Agent 可以用它决定讲解口吻（越不确定越保守）
CONF_SPOT = 0.95        # 本地围栏命中了景区内点位，最可信
CONF_AREA = 0.85        # 本地围栏命中了景区
CONF_ONLINE = 0.70      # 在线反解结果
CONF_BLE = 0.70         # 室内信标映射
CONF_RAW = 0.50         # 只有坐标，认不出地方


class Geocoder(Protocol):
    def poi_name(self, lat: float, lon: float) -> str: ...


class LocationStateMachine:
    def __init__(self, geofence: Optional[GeofenceIndex] = None,
                 geocoder: Optional[Geocoder] = None,
                 clock: Callable[[], float] = time.monotonic,
                 hold_s: float = SWITCH_HOLD_S):
        self._geofence = geofence
        self._geocoder = geocoder
        self._clock = clock
        self._hold_s = hold_s
        self._mode = MODE_NONE
        self._last_switch_ts = 0.0
        self._area_name: Optional[str] = None      # 当前景区，用于边界迟滞

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def area_name(self) -> Optional[str]:
        return self._area_name

    def decide(self, fix: Optional[Fix], hit: Optional[BeaconHit]) -> Location:
        """唯一决策入口。永不抛异常。"""
        now = self._clock()
        ble_ok = bool(hit and hit.is_fresh(now) and hit.name)
        # 【跨模块修复·2026-09-28】未映射信标不算定位证据：环境里别人的
        # iBeacon（实测邻居设备 UUID 66238680… major=258 -69dBm，阈值放宽
        # 到 -85 后被收进来）会顶掉 GPS、让 LLM 拿到一串 UUID 当景点名。
        # 只有 beacons.yaml 里映射出展位名的信标才代表"人在展位"。
        gps_ok = bool(fix and fix.is_usable(now))

        # 迟滞：刚从别处切到 BLE，在 hold 窗口内不允许切回 GPS
        if gps_ok and self._mode == MODE_BLE and (now - self._last_switch_ts) < self._hold_s:
            gps_ok = False

        # 【跨模块修复·2026-09-28】BLE 最高优先（负责人拍板）：展位有新鲜信标
        # 命中时，GPS 一律不插嘴——点位级也不行。信标固定在展位上是物理事实，
        # 米级证据永远压过任何卫星推导（弱 fix 串味、漂移恰好落进 spot 圈的
        # 翻车窗口全部消除）。信标被带在身上会压死 GPS：运维场景靠管理约束
        # （信标固定在展位），不在软件里留例外。GPS 夺回主导权的唯一条件是
        # 信标命中过期（BLE_MAX_AGE_S=10s）。
        if ble_ok and gps_ok:
            gps_ok = False

        if gps_ok:
            poi_name, confidence = self._outdoor_name(fix)
            return self._emit(MODE_GPS, poi_name, fix.lat, fix.lon, None, confidence)

        if ble_ok:
            return self._emit(MODE_BLE, hit.name or hit.beacon_id,
                              None, None, hit.beacon_id, CONF_BLE)

        return self._emit(MODE_NONE, "定位不可用", None, None, None, 0.0)

    # ------------------------------------------------------------------ 内部
    def _outdoor_name(self, fix: Fix) -> tuple[str, float]:
        """本地围栏优先，没命中才去问在线反解。"""
        if self._geofence is not None and not self._geofence.is_empty():
            try:
                match = self._geofence.locate(fix.lat, fix.lon,
                                              precise=fix.is_precise(self._clock()),
                                              sticky=self._area_name)
            except Exception:
                log.warning("围栏判定异常", exc_info=True)
                match = None
            if match is not None:
                self._area_name = match.area.name
                if match.level == "spot":
                    return match.name, CONF_SPOT
                return match.name, CONF_AREA
            self._area_name = None       # 确认离开景区，清掉迟滞状态

        if self._geocoder is not None:
            try:
                name = self._geocoder.poi_name(fix.lat, fix.lon)
            except Exception:
                log.warning("在线反解异常，降级", exc_info=True)
                name = None
            if name and name != POI_FALLBACK:
                return name, CONF_ONLINE

        return POI_FALLBACK, CONF_RAW

    def _emit(self, mode: str, poi_name: str, lat: Optional[float], lon: Optional[float],
              beacon_id: Optional[str], confidence: float) -> Location:
        if mode != self._mode:
            log.info("定位模式切换：%s → %s", self._mode, mode)
            self._mode = mode
            self._last_switch_ts = self._clock()
        return Location(mode=mode, poi_name=poi_name, lat=lat, lon=lon,
                        beacon_id=beacon_id, confidence=confidence,
                        timestamp=now_iso())
