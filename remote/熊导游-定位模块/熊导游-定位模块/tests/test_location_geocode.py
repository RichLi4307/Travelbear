"""高德反解：响应解析、缓存、非阻塞降级。不需要联网、不需要 API Key。"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from location.contract import POI_FALLBACK
from location.geocode import PoiResolver, extract_poi_name, grid_key
from location.geofence import haversine_m, wgs84_to_gcj02

_TMP_ROOT = Path(__file__).resolve().parent.parent / "_test_tmp"


@pytest.fixture
def tmp_path(request):
    """覆盖 pytest 自带的 tmp_path。

    自带版本把临时目录建在系统 temp 下，在受限环境（沙箱、只读的 CI 容器、
    某些企业管控的机器）里会直接 PermissionError，测试全变 ERROR。
    改成建在仓库内、名字由测试名决定、用完即删，任何环境都能跑。
    """
    workdir = _TMP_ROOT / request.node.name
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        yield workdir
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

AMAP_OK = {
    "status": "1", "info": "OK", "infocode": "10000",
    "regeocode": {
        "formatted_address": "上海市宝山区上大路99号",
        "addressComponent": {"district": "宝山区", "township": "大场镇"},
        "pois": [
            {"name": "上海大学(宝山校区)", "distance": "35.2"},
            {"name": "泮池", "distance": "12.0"},
        ],
    },
}


class FakeHttp:
    def __init__(self, payload, fail: bool = False):
        self.payload = payload
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, url: str, timeout: float) -> dict:
        self.calls.append(url)
        if self.fail:
            raise TimeoutError("模拟高德超时")
        return self.payload


def make_resolver(tmp_path, http=None) -> PoiResolver:
    return PoiResolver("fake-key", cache_path=str(tmp_path / "poi.sqlite"),
                       http_get=http or FakeHttp(AMAP_OK), min_interval=0.0)


# ------------------------------------------------------------------ 响应解析
def test_extract_prefers_nearest_poi():
    assert extract_poi_name(AMAP_OK) == "泮池"


def test_extract_falls_back_to_formatted_address():
    payload = {"status": "1", "regeocode": {"formatted_address": "上海市宝山区上大路99号"}}
    assert extract_poi_name(payload) == "上海市宝山区上大路99号"


def test_extract_falls_back_to_district_township():
    payload = {"status": "1", "regeocode": {
        "formatted_address": "",
        "addressComponent": {"district": "宝山区", "township": "大场镇"}}}
    assert extract_poi_name(payload) == "宝山区大场镇"


def test_extract_handles_api_error_codes():
    """status != "1" 就是出错：10009 平台不符 / 10003 日配额超限 / 10004 访问过频。"""
    for infocode in ("10003", "10004", "10009"):
        payload = {"status": "0", "info": "INVALID_USER_KEY", "infocode": infocode}
        assert extract_poi_name(payload) is None


def test_extract_tolerates_garbage():
    assert extract_poi_name({}) is None
    assert extract_poi_name({"status": "1", "regeocode": []}) is None
    assert extract_poi_name({"status": "1", "regeocode": None}) is None


# -------------------------------------------------------------------- 缓存
def test_grid_key_collapses_nearby_coordinates():
    """同一景点反复按按钮不该反复打 API —— 高德按日限流。"""
    assert grid_key(31.23040, 121.47370) == grid_key(31.23041, 121.47371)


def test_sync_lookup_hits_network_once_then_cache(tmp_path):
    http = FakeHttp(AMAP_OK)
    resolver = make_resolver(tmp_path, http)

    assert resolver.lookup_sync(31.2304, 121.4737) == "泮池"
    assert resolver.lookup_sync(31.2304, 121.4737) == "泮池"
    assert len(http.calls) == 1


def test_cache_survives_new_instance(tmp_path):
    resolver = make_resolver(tmp_path, FakeHttp(AMAP_OK))
    resolver.lookup_sync(31.2304, 121.4737)

    http2 = FakeHttp(AMAP_OK)
    resolver2 = make_resolver(tmp_path, http2)
    assert resolver2.lookup_sync(31.2304, 121.4737) == "泮池"
    assert http2.calls == []


# --------------------------------------------------------------- 非阻塞行为
def test_poi_name_never_blocks_on_cold_cache(tmp_path):
    """按键链路上第一次问到这个坐标，必须立刻拿到 fallback，不能等 5s 超时。"""
    http = FakeHttp(AMAP_OK)
    resolver = make_resolver(tmp_path, http)

    assert resolver.poi_name(31.2304, 121.4737) == POI_FALLBACK

    resolver.wait_idle()
    assert resolver.poi_name(31.2304, 121.4737) == "泮池"      # 后台已经补上了


def test_poi_name_degrades_when_network_fails(tmp_path):
    resolver = make_resolver(tmp_path, FakeHttp(AMAP_OK, fail=True))

    assert resolver.poi_name(31.2304, 121.4737) == POI_FALLBACK
    resolver.wait_idle()
    assert resolver.poi_name(31.2304, 121.4737) == POI_FALLBACK   # 仍然不抛异常


def test_lookup_sync_returns_fallback_without_api_key(tmp_path):
    resolver = PoiResolver("", cache_path=str(tmp_path / "poi.sqlite"),
                           http_get=FakeHttp(AMAP_OK), min_interval=0.0)
    assert resolver.lookup_sync(31.2304, 121.4737) == POI_FALLBACK


def test_request_url_uses_lon_lat_order(tmp_path):
    """高德是 经度,纬度 —— 写反了会定位到地球另一边，而且不报错。"""
    http = FakeHttp(AMAP_OK)
    resolver = make_resolver(tmp_path, http)
    resolver.lookup_sync(31.2304, 121.4737)
    g_lat, g_lon = wgs84_to_gcj02(31.2304, 121.4737)
    assert f"location={g_lon:.6f}%2C{g_lat:.6f}" in http.calls[0]


def test_request_converts_wgs84_to_gcj02_before_sending(tmp_path):
    """回归测试：高德接口期望 GCJ-02 输入。

    直接发 WGS84 会被当成 GCJ-02 解析，上海实测落点偏 ~480m ——
    表现为站在外滩却查出"某某门诊部"。这条测试钉住这个坑。
    """
    http = FakeHttp(AMAP_OK)
    resolver = make_resolver(tmp_path, http)
    resolver.lookup_sync(31.2304, 121.4737)

    assert "location=121.473700%2C31.230400" not in http.calls[0], (
        "把 WGS84 原样发出去了 —— 坐标系没折算")

    g_lat, g_lon = wgs84_to_gcj02(31.2304, 121.4737)
    assert haversine_m(31.2304, 121.4737, g_lat, g_lon) > 300, (
        "折算后的偏移量太小，可能没真的转换")
    assert f"location={g_lon:.6f}%2C{g_lat:.6f}" in http.calls[0]
