"""室内定位：扫 iBeacon，取"最近的那个"，映射成展位名。

关键取舍：**不做距离估算，不做三点定位。** 展位场景只要回答"最近的信标是谁"。
RSSI 抖动 ±10dBm 是常态，做距离估算纯属给自己挖坑。

bleak / PyYAML 都是延迟导入，纯逻辑测试不需要装它们。
"""
from __future__ import annotations

import logging
import statistics
import threading
import time
import uuid as uuid_mod
from typing import Callable, Iterable, Optional

from .contract import BeaconHit

log = logging.getLogger(__name__)

APPLE_COMPANY_ID = 0x004C
IBEACON_TYPE = 0x02
IBEACON_LEN = 0x15
IBEACON_MIN_BYTES = 23          # 2 (type+len) + 16 uuid + 2 major + 2 minor + 1 txpower

DEFAULT_RSSI_THRESHOLD = -75    # 低于此强度一律忽略；现场用 tools/beacon_calibrate.py 标定
DEFAULT_WINDOW = 5              # RSSI 滑动窗口长度
SCAN_RETRY_BACKOFF_S = 3.0


def parse_ibeacon(manufacturer_data: dict) -> Optional[tuple[str, int, int, int]]:
    """从 bleak 的 ``advertisement_data.manufacturer_data`` 里解出 iBeacon。

    标准广播：company_id=0x004C，载荷 ``02 15 <16B UUID> <2B major> <2B minor> <1B txpower>``。
    不是 iBeacon（Eddystone、别家私有协议）→ 返回 None，**绝不抛异常**。
    """
    if not manufacturer_data:
        return None
    data = manufacturer_data.get(APPLE_COMPANY_ID)
    if not data or len(data) < IBEACON_MIN_BYTES:
        return None
    if data[0] != IBEACON_TYPE or data[1] != IBEACON_LEN:
        return None
    # 统一大写（这是 iBeacon 的通行写法，也和厂商 App 里看到的一致）；
    # BeaconRegistry 查表时会把两边都转小写，所以 yaml 里大小写随便写。
    beacon_uuid = str(uuid_mod.UUID(bytes=bytes(data[2:18]))).upper()
    major = int.from_bytes(data[18:20], "big")
    minor = int.from_bytes(data[20:22], "big")
    tx_power = int.from_bytes(data[22:23], "big", signed=True)
    return beacon_uuid, major, minor, tx_power


class RssiFilter:
    """滑动窗口中位数。比均值更能压掉偶发的深衰落，也不会被单个异常值带偏。"""

    def __init__(self, window: int = DEFAULT_WINDOW):
        self.window = max(1, window)
        self._buf: dict[str, list[int]] = {}

    def push(self, key: str, rssi: int) -> int:
        buf = self._buf.setdefault(key, [])
        buf.append(int(rssi))
        if len(buf) > self.window:
            del buf[0]
        return int(statistics.median(buf))

    def forget(self, key: str) -> None:
        self._buf.pop(key, None)


class BeaconRegistry:
    """beacons.yaml：信标 → 展位名 + 现场标定阈值。改展位只改 yaml，不动代码。"""

    def __init__(self, entries: Iterable[dict],
                 default_threshold: int = DEFAULT_RSSI_THRESHOLD):
        self.default_threshold = int(default_threshold)
        self._map: dict[tuple[str, int, int], dict] = {}
        for entry in entries or []:
            key = (str(entry["uuid"]).lower(),
                   int(entry.get("major", 0)), int(entry.get("minor", 0)))
            self._map[key] = entry

    @classmethod
    def load(cls, path: str) -> "BeaconRegistry":
        import yaml
        with open(path, "r", encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        return cls(doc.get("beacons", []),
                   doc.get("default_rssi_threshold", DEFAULT_RSSI_THRESHOLD))

    def get(self, beacon_uuid: str, major: int, minor: int) -> Optional[dict]:
        return self._map.get((str(beacon_uuid).lower(), int(major), int(minor)))

    def name_for(self, beacon_uuid: str, major: int, minor: int) -> Optional[str]:
        entry = self.get(beacon_uuid, major, minor)
        return (entry or {}).get("name")

    def threshold_for(self, beacon_uuid: str, major: int, minor: int) -> int:
        entry = self.get(beacon_uuid, major, minor)
        if entry and entry.get("rssi_threshold") is not None:
            return int(entry["rssi_threshold"])
        return self.default_threshold

    def __len__(self) -> int:
        return len(self._map)


class BleScanner:
    """后台扫描线程，维护信标缓存。``nearest()`` 非阻塞。

    没有硬件也能测：直接调 ``feed_advertisement()``，它是不碰蓝牙的纯逻辑接口。
    """

    def __init__(self, registry: BeaconRegistry,
                 rssi_threshold: Optional[int] = None,
                 window: int = DEFAULT_WINDOW,
                 scanner_factory: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.registry = registry
        self.rssi_threshold = rssi_threshold
        self._scanner_factory = scanner_factory
        self._clock = clock
        self._filter = RssiFilter(window)
        self._lock = threading.Lock()
        self._hits: dict[str, BeaconHit] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.ready = threading.Event()

    # ------------------------------------------------------------ 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ble", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ 纯逻辑接口
    def feed_advertisement(self, manufacturer_data: dict, rssi: int,
                           now: Optional[float] = None) -> Optional[BeaconHit]:
        """处理一个广播包。**这是测试用的接口，不依赖蓝牙硬件。**"""
        parsed = parse_ibeacon(manufacturer_data)
        if parsed is None:
            return None
        beacon_uuid, major, minor, _tx = parsed
        beacon_id = f"{beacon_uuid}:{major}:{minor}"

        smoothed = self._filter.push(beacon_id, rssi)
        threshold = (self.rssi_threshold
                     if self.rssi_threshold is not None
                     else self.registry.threshold_for(beacon_uuid, major, minor))
        if smoothed < threshold:
            # 太弱：地图上有这个信标，但你现在不在它的展位里
            with self._lock:
                self._hits.pop(beacon_id, None)
            return None

        hit = BeaconHit(
            uuid=beacon_uuid, major=major, minor=minor,
            rssi=smoothed,
            ts=self._clock() if now is None else now,
            name=self.registry.name_for(beacon_uuid, major, minor),
        )
        with self._lock:
            self._hits[beacon_id] = hit
        return hit

    def nearest(self) -> Optional[BeaconHit]:
        """非阻塞：只读内存缓存，取新鲜且最强的那个。"""
        now = self._clock()
        with self._lock:
            fresh = [h for h in self._hits.values() if h.is_fresh(now)]
        if not fresh:
            return None
        return max(fresh, key=lambda h: h.rssi)

    def snapshot(self) -> dict[str, int]:
        """现场标定用：当前所有信标的滤波后 RSSI。"""
        with self._lock:
            return {bid: h.rssi for bid, h in self._hits.items()}

    # ------------------------------------------------------------ 内部
    def _on_detect(self, device, adv) -> None:
        try:
            self.feed_advertisement(adv.manufacturer_data, adv.rssi)
        except Exception:
            log.debug("处理广播包失败", exc_info=True)

    def _run(self) -> None:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._scan_forever())
        except Exception:
            log.exception("BLE 扫描线程异常退出")

    async def _scan_forever(self) -> None:
        import asyncio
        while not self._stop.is_set():
            try:
                from bleak import BleakScanner
                factory = self._scanner_factory or (
                    lambda cb: BleakScanner(detection_callback=cb))
                scanner = factory(self._on_detect)
                await scanner.start()
                self.ready.set()
                log.info("BLE 扫描已启动，已加载 %d 个信标映射", len(self.registry))
                while not self._stop.is_set():
                    await asyncio.sleep(0.5)
                await scanner.stop()
            except Exception as exc:
                log.warning("BLE 扫描异常，%.1fs 后重试：%s", SCAN_RETRY_BACKOFF_S, exc)
                await asyncio.sleep(SCAN_RETRY_BACKOFF_S)
