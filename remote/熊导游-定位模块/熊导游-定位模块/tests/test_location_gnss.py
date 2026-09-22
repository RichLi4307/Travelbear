"""NMEA 解析。用文本回放，不需要真实 GPS 模块。

需要 pynmea2：pip install -r requirements.txt
（质量门槛的测试在 test_contract.py，那个文件不需要任何依赖。）
"""
from __future__ import annotations

import pynmea2
import pytest

from location.gnss import NmeaParser, parse_line


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def nmea(body: str) -> str:
    """给 NMEA 正文补上校验和。真实模块发出来的都带校验和，所以生产路径 check=True。"""
    checksum = 0
    for ch in body:
        checksum ^= ord(ch)
    return f"${body}*{checksum:02X}"


GOOD_GGA = nmea("GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
NO_FIX_GGA = nmea("GPGGA,123520,4807.038,N,01131.000,E,0,00,,545.4,M,46.9,M,,")
RMC = nmea("GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W")


def test_parse_good_gga():
    fix = parse_line(GOOD_GGA)
    assert fix is not None
    assert fix.lat == pytest.approx(48.1173, abs=1e-6)        # 4807.038 → 48 + 7.038/60
    assert fix.lon == pytest.approx(11.5166667, abs=1e-6)     # 01131.000 → 11 + 31.000/60
    assert fix.quality == 1
    assert fix.satellites == 8
    assert fix.hdop == pytest.approx(0.9)


def test_parse_rejects_no_fix_sentence():
    """模块通电但还没定上位：quality=0。这不是错误，是刚开机/室内的常态。"""
    assert parse_line(NO_FIX_GGA) is None


def test_parse_rejects_non_gga_and_garbage():
    assert parse_line(RMC) is None          # 只用 GGA：它一句话给全坐标+星数+HDOP+质量
    assert parse_line("") is None
    assert parse_line("garbage") is None
    assert parse_line("$GPGGA,broken") is None


def test_parse_rejects_bad_checksum():
    assert parse_line("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*00") is None


def test_parser_tracks_latest_fix():
    clock = FakeClock()
    parser = NmeaParser(clock=clock)

    assert parser.feed(RMC) is False
    assert parser.latest() is None

    assert parser.feed(GOOD_GGA) is True
    assert parser.latest() is not None
    assert parser.lines_seen == 2
    assert parser.fixes_seen == 1


def test_parser_keeps_last_good_fix_when_satellites_drop():
    """室内丢星时会开始刷 quality=0 的句子；最后一条好 fix 必须留着（由时效门槛淘汰）。"""
    parser = NmeaParser(clock=FakeClock())
    parser.feed(GOOD_GGA)
    good = parser.latest()

    for _ in range(5):
        parser.feed(NO_FIX_GGA)

    assert parser.latest() == good
    assert parser.fixes_seen == 1
