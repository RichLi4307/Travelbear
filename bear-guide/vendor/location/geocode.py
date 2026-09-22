"""室外反解：WGS84 坐标 → 人类可读的景点名（高德 Web 服务 API）。

两个关键约束：

1. **坐标系**。高德接口**期望输入 GCJ-02（火星坐标）**，而 GNSS 模块给的是 WGS84。
   所以调用前必须折算（本文件 `_request()` 里做了），否则落点在中国境内偏 300~600m。
   上海实测 ~480m —— 直接发 WGS84 会站在外滩查出"某某门诊部"。
   返回值我们只取地名、不取坐标，所以不需要再反算回 WGS84。
2. **非阻塞**。``poi_name()`` 只读 sqlite 缓存；未命中立刻返回 fallback，并在后台
   线程补抓。按键链路目标 ≤3s，不能被一次 5s 的 API 超时拖死。

顺带解决配额问题：结果按 ≈55m 网格缓存，同一景点反复按按钮只打一次 API
（高德按日限流，超了返回 10003 / 10004）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional

from .contract import POI_FALLBACK, POI_TIMEOUT_S
from .geofence import wgs84_to_gcj02

log = logging.getLogger(__name__)

REVERSE_GEOCODE_URL = "https://restapi.amap.com/v3/geocode/regeo"
GRID_DEG = 0.0005           # ≈55m 一格
CACHE_TTL_S = 7 * 86400
MIN_CALL_INTERVAL_S = 0.5   # 防止触发单位时间访问超限


def grid_key(lat: float, lon: float) -> tuple[int, int]:
    return int(round(lat / GRID_DEG)), int(round(lon / GRID_DEG))


def _default_http_get(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "ican-locator/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def extract_poi_name(payload: dict) -> Optional[str]:
    """从高德 regeo 响应里挑一个"人话"位置名。

    优先级：最近的 POI 名 > 格式化地址 > 区+街道。
    逆地理编码开 ``extensions=all`` 时会顺带返回周边 POI，所以**不用再单独调
    周边搜索接口** —— 单人开发省一次 API 调用和一份配额。
    """
    if not isinstance(payload, dict) or payload.get("status") != "1":
        return None
    regeocode = payload.get("regeocode")
    if not isinstance(regeocode, dict):
        return None

    pois = regeocode.get("pois") or []
    if isinstance(pois, list) and pois:
        def distance(poi: dict) -> float:
            try:
                return float(poi.get("distance") or 1e9)
            except (TypeError, ValueError):
                return 1e9
        best = min(pois, key=distance)
        name = str(best.get("name") or "").strip()
        if name:
            return name

    addr = regeocode.get("formatted_address")
    if isinstance(addr, str) and addr.strip():
        return addr.strip()

    comp = regeocode.get("addressComponent") or {}
    if isinstance(comp, dict):
        parts = [comp.get("district"), comp.get("township")]
        joined = "".join(str(p) for p in parts if isinstance(p, str) and p)
        if joined:
            return joined
    return None


class PoiResolver:
    """坐标 → POI 名。对外唯一方法是 ``poi_name()``，保证非阻塞。"""

    def __init__(self, api_key: str,
                 cache_path: str = "data/poi_cache.sqlite",
                 http_get: Optional[Callable[[str, float], dict]] = None,
                 timeout: float = POI_TIMEOUT_S,
                 radius: int = 200,
                 min_interval: float = MIN_CALL_INTERVAL_S):
        self.api_key = api_key
        self.cache_path = cache_path
        self.timeout = timeout
        self.radius = radius
        self.min_interval = min_interval
        self._http_get = http_get or _default_http_get
        self._lock = threading.Lock()
        self._rate_lock = threading.Lock()
        self._inflight: set[tuple[int, int]] = set()
        self._last_call_ts = 0.0
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        self._init_db()

    # ------------------------------------------------------------ 对外：非阻塞
    def poi_name(self, lat: float, lon: float) -> str:
        """**永远立刻返回。** 缓存未命中 → 返回 fallback，同时后台补抓。"""
        cached = self._cache_get(lat, lon)
        if cached:
            return cached
        self._spawn_fetch(lat, lon)
        return POI_FALLBACK

    # ------------------------------------------------------------ 同步查询
    def lookup_sync(self, lat: float, lon: float) -> str:
        """阻塞版：给后台线程和测试用，**不要**放在按键链路上。"""
        cached = self._cache_get(lat, lon)
        if cached:
            return cached
        name = self._request(lat, lon)
        if name:
            self._cache_put(grid_key(lat, lon), name)
            return name
        return POI_FALLBACK

    def wait_idle(self, timeout: float = 3.0) -> None:
        """等后台补抓结束。只给测试/脚本用。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._inflight:
                    return
            time.sleep(0.02)

    # ------------------------------------------------------------ 内部：HTTP
    def _request(self, lat: float, lon: float) -> Optional[str]:
        if not self.api_key:
            return None
        # ★ 必须先把 WGS84 折算成 GCJ-02 再发出去。
        # 高德 Web 服务接口**期望输入 GCJ-02**，而 GNSS 模块给我们的是 WGS84。
        # 直接发 WGS84 会被当成 GCJ-02 解析，落点偏 ~480m（上海实测）——
        # 结果就是站在外滩却查出"某某门诊部"。
        # 注意：折算的是**输入**；返回值我们只取地名，不取坐标，所以不需要反算回去。
        g_lat, g_lon = wgs84_to_gcj02(lat, lon)
        params = {
            "key": self.api_key,
            "location": f"{g_lon:.6f},{g_lat:.6f}",   # 高德是 经度,纬度
            "extensions": "all",
            "radius": str(self.radius),
            "output": "JSON",
        }
        url = f"{REVERSE_GEOCODE_URL}?{urllib.parse.urlencode(params)}"
        self._throttle()
        payload = self._http_get(url, self.timeout)
        if isinstance(payload, dict) and payload.get("status") != "1":
            # 10009 平台不符 / 10003 日配额超限 / 10004 访问过频，全在这里现形
            log.warning("高德返回异常：status=%s info=%s infocode=%s",
                        payload.get("status"), payload.get("info"), payload.get("infocode"))
        return extract_poi_name(payload)

    def _throttle(self) -> None:
        with self._rate_lock:
            gap = time.monotonic() - self._last_call_ts
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last_call_ts = time.monotonic()

    # ------------------------------------------------------------ 内部：后台
    def _spawn_fetch(self, lat: float, lon: float) -> None:
        key = grid_key(lat, lon)
        with self._lock:
            if key in self._inflight:
                return
            self._inflight.add(key)
        threading.Thread(target=self._fetch_worker, args=(lat, lon, key),
                         name="poi-fetch", daemon=True).start()

    def _fetch_worker(self, lat: float, lon: float, key: tuple[int, int]) -> None:
        try:
            name = self._request(lat, lon)
            if name:
                self._cache_put(key, name)
        except Exception as exc:
            # 断网 / 超时 / 配额超限：降级成 fallback，绝不往上抛
            log.warning("高德反解失败（已降级为 %s）：%s", POI_FALLBACK, exc)
        finally:
            with self._lock:
                self._inflight.discard(key)

    # ------------------------------------------------------------ 内部：缓存
    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.cache_path, timeout=2.0)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS poi ("
                         "gi INTEGER, gj INTEGER, name TEXT, ts REAL, "
                         "PRIMARY KEY (gi, gj))")
            conn.commit()
        finally:
            conn.close()

    def _cache_get(self, lat: float, lon: float) -> Optional[str]:
        gi, gj = grid_key(lat, lon)
        conn = self._connect()
        try:
            row = conn.execute("SELECT name, ts FROM poi WHERE gi=? AND gj=?",
                               (gi, gj)).fetchone()
        except sqlite3.Error as exc:
            log.warning("POI 缓存读取失败：%s", exc)
            return None
        finally:
            conn.close()
        if not row:
            return None
        name, ts = row
        if time.time() - float(ts) > CACHE_TTL_S:
            return None
        return name

    def _cache_put(self, key: tuple[int, int], name: str) -> None:
        conn = self._connect()
        try:
            conn.execute("INSERT OR REPLACE INTO poi (gi, gj, name, ts) VALUES (?,?,?,?)",
                         (key[0], key[1], name, time.time()))
            conn.commit()
        except sqlite3.Error as exc:
            log.warning("POI 缓存写入失败：%s", exc)
        finally:
            conn.close()
