"""室外定位：串口常驻读取 NMEA，维护"最新 fix"缓存。

设计要点（不要改）：
  * 后台线程常驻读串口。冷启动搜星 30s+，**绝不能**在按键时才去搜星。
  * ``latest()`` 只读内存，O(1)，不阻塞，不碰 I/O。
  * 树莓派 4B 上走 **USB 串口**（/dev/ttyUSB0），不要用 GPIO UART ——
    4B 的 GPIO 真串口默认被板载蓝牙占着，抢回来要 disable-bt，那样 BLE 扫描也没了。

pynmea2 是延迟导入的：纯逻辑（switch/ble/geocode）的测试不需要装它。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Callable, Optional

from .contract import Fix

log = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 9600
RECONNECT_BACKOFF_S = 3.0


def parse_line(line: str, clock: Callable[[], float] = time.monotonic) -> Optional[Fix]:
    """解析一行 NMEA。只认 GGA —— 它一句话同时给出坐标、星数、HDOP、定位质量。

    返回 None 表示"这行没带来可用定位"，这是正常情况（无星、丢包、别的句子），不是错误。
    """
    line = line.strip()
    if not line.startswith("$"):
        return None
    try:
        import pynmea2
        msg = pynmea2.parse(line, check=True)
    except Exception:            # ImportError / ParseError / ChecksumError 都当噪声
        return None
    if getattr(msg, "sentence_type", None) != "GGA":
        return None
    try:
        lat, lon = float(msg.latitude), float(msg.longitude)
        quality = int(msg.gps_qual or 0)
    except (TypeError, ValueError):
        return None
    if quality == 0 or (lat == 0.0 and lon == 0.0):
        return None              # 收到句子了，但还没定上位
    # pynmea2 的 GGA 里这个字段的真名是 horizontal_dil（有些版本才额外提供 hdop 属性），
    # 两个都试一下，都没有就当未知（nan），别让属性名差异把整条解析链打断。
    hdop_raw = getattr(msg, "hdop", None)
    if hdop_raw in (None, ""):
        hdop_raw = getattr(msg, "horizontal_dil", None)
    try:
        hdop = float(hdop_raw)
    except (TypeError, ValueError):
        hdop = math.nan
    return Fix(
        lat=lat, lon=lon, quality=quality,
        satellites=int(msg.num_sats or 0), hdop=hdop, ts=clock(),
    )


class NmeaParser:
    """把 NMEA 行喂进来，随时取最新 fix。纯逻辑、无串口依赖 → 可离线回放测试。"""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._latest: Optional[Fix] = None
        self.lines_seen = 0
        self.fixes_seen = 0

    def feed(self, line: str) -> bool:
        self.lines_seen += 1
        fix = parse_line(line, self._clock)
        if fix is None:
            return False
        with self._lock:
            self._latest = fix
        self.fixes_seen += 1
        return True

    def latest(self) -> Optional[Fix]:
        with self._lock:
            return self._latest


class GnssReader:
    """后台线程常驻读串口，断线自动重连。

    ``open_port`` 可注入：笔记本上用假串口（回放文件）跑集成测试时很有用。
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = DEFAULT_BAUDRATE,
                 open_port: Optional[Callable] = None):
        self.port = port
        self.baudrate = baudrate
        self._open_port = open_port or self._open_serial
        self._parser = NmeaParser()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _open_serial(port: str, baudrate: int):
        import serial                # 延迟导入：没 pyserial 也能跑纯逻辑测试
        return serial.Serial(port, baudrate, timeout=1.0)

    # ---------------------------------------------------------------- 生命周期
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gnss", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def latest(self) -> Optional[Fix]:
        return self._parser.latest()

    # ---------------------------------------------------------------- 内部
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                with self._open_port(self.port, self.baudrate) as ser:
                    log.info("GNSS 串口已打开：%s @ %d", self.port, self.baudrate)
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if raw:
                            self._parser.feed(raw.decode("ascii", errors="ignore"))
            except Exception as exc:
                # 拔线 / 权限不足 / 设备消失：退避重连，绝不杀进程
                log.warning("GNSS 串口异常，%.1fs 后重连：%s", RECONNECT_BACKOFF_S, exc)
                self._stop.wait(RECONNECT_BACKOFF_S)
