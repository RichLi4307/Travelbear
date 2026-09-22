"""定位模块对外的唯一入口：get_location()。

契约（见 contract.py）：
  * 永不抛异常 —— 任何意外都返回 ``mode="none"``。
  * 永不阻塞 —— 只读两个内存缓存，不做任何 I/O。

内部结构：
    gnss（常驻读串口） ┐
                       ├→ switch（决策）→ Location
    ble_scan（常驻扫信标）┘
    geofence（本地围栏，同步、零延迟）
    geocode（高德，可选兜底，后台补抓）
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from . import ble_scan, gnss, switch
from .contract import Location
from .geofence import GeofenceIndex

log = logging.getLogger(__name__)

PKG_DIR = os.path.dirname(os.path.abspath(__file__))

# 环境变量优先，方便在笔记本上用假串口调，不用改代码
GNSS_PORT = os.environ.get("GNSS_PORT", "/dev/ttyUSB0")
GNSS_BAUD = int(os.environ.get("GNSS_BAUD", "9600"))
BEACONS_YAML = os.environ.get("BEACONS_YAML", os.path.join(PKG_DIR, "beacons.yaml"))
SCENIC_YAML = os.environ.get("SCENIC_AREAS_YAML", os.path.join(PKG_DIR, "scenic_areas.yaml"))
POI_CACHE = os.environ.get("POI_CACHE", os.path.join(PKG_DIR, "..", "data", "poi_cache.sqlite"))
AMAP_KEY = os.environ.get("AMAP_WEB_KEY", "")

_lock = threading.Lock()
_sm: Optional[switch.LocationStateMachine] = None
_reader: Optional[gnss.GnssReader] = None
_scanner: Optional[ble_scan.BleScanner] = None
_geofence: Optional[GeofenceIndex] = None
_started = False


def _load_geofence() -> Optional[GeofenceIndex]:
    try:
        index = GeofenceIndex.load(SCENIC_YAML)
        log.info("本地围栏已加载：%d 个景区 / %d 个点位", len(index), index.spot_count)
        return index
    except Exception as exc:
        log.warning("本地围栏未加载（室外将只剩在线反解）：%s", exc)
        return None


def start() -> None:
    """把 GNSS 读取线程和 BLE 扫描线程拉起来。可重复调用（幂等）。

    建议 Agent 主程序启动时显式调一次；按键链路里第一次调 get_location() 也会兜底拉起，
    但那时线程刚起步，头几秒大概率还是 none。
    """
    global _sm, _reader, _scanner, _geofence, _started
    with _lock:
        if _started:
            return

        _geofence = _load_geofence()

        geocoder = None
        if AMAP_KEY:
            from .geocode import PoiResolver
            geocoder = PoiResolver(AMAP_KEY, cache_path=POI_CACHE)
        else:
            log.info("未设置 AMAP_WEB_KEY：不启用在线反解（本地围栏照常工作）")

        _sm = switch.LocationStateMachine(geofence=_geofence, geocoder=geocoder)

        _reader = gnss.GnssReader(GNSS_PORT, GNSS_BAUD)
        _reader.start()

        try:
            registry = ble_scan.BeaconRegistry.load(BEACONS_YAML)
            _scanner = ble_scan.BleScanner(registry)
            _scanner.start()
        except Exception as exc:
            log.warning("BLE 扫描未启动（室内定位将不可用）：%s", exc)

        _started = True
        log.info("定位模块已启动：gnss=%s ble=%s 围栏=%s 在线反解=%s",
                 GNSS_PORT, _scanner is not None,
                 _geofence is not None, geocoder is not None)


def stop() -> None:
    """停掉后台线程。进程退出时其实无所谓（都是 daemon 线程），测试里用得上。"""
    global _started
    with _lock:
        if _reader:
            _reader.stop()
        if _scanner:
            _scanner.stop()
        _started = False


def get_location() -> dict:
    """返回统一位置对象。**永不抛异常，永不阻塞。**"""
    try:
        start()                      # 幂等，第一次调用时兜底拉起后台线程
        sm = _sm
        if sm is None:
            return Location.none("定位模块未初始化").to_dict()
        fix = _reader.latest() if _reader else None
        hit = _scanner.nearest() if _scanner else None
        return sm.decide(fix, hit).to_dict()
    except Exception:
        log.exception("get_location 异常，降级为 none")
        return Location.none("定位模块异常").to_dict()
