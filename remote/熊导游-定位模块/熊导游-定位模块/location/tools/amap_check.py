"""高德 Key 体检：验证 Key 能用、类型选对、以及配置里的坐标在高德眼里是哪儿。

它做三件事：
  1. 连通性：Key 能不能调用成功；失败时把错误码翻译成人话
  2. 坐标核对：把 scenic_areas.yaml 里每个景区的坐标拿去逆地理编码，
     看高德返回的地名和你的预期是否吻合 —— 这是**独立数据源交叉验证**
  3. 坐标系实测：同一个地点，分别用 WGS84 原值和 GCJ-02 折算值去查，
     把两者的差异摆出来，让你亲眼看到那个"300~600m"到底有多大

Key 只从环境变量读，不会写进任何文件：
    PowerShell:  $env:AMAP_WEB_KEY="你的key"; python tools/amap_check.py
    Linux/Pi  :  export AMAP_WEB_KEY=你的key;  python tools/amap_check.py

用法：
    python tools/amap_check.py                 # 全部检查
    python tools/amap_check.py --quick         # 只做连通性测试
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location.geocode import (REVERSE_GEOCODE_URL, PoiResolver,  # noqa: E402
                              _default_http_get, extract_poi_name, grid_key)
from location.geofence import GeofenceIndex, gcj02_to_wgs84, wgs84_to_gcj02, haversine_m  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AREAS_YAML = os.path.join(ROOT, "location", "scenic_areas.yaml")

# 高德错误码 → 人话 + 怎么办
ERROR_HINTS = {
    "10001": ("Key 不正确或已过期", "去控制台核对 Key，或重新创建"),
    "10002": ("该 Key 没有这个服务的权限", "Key 类型要选「Web服务」，不是「Web端(JS API)」"),
    "10003": ("账号日调用量已用完", "等第二天 0:00 自动恢复；先靠本地围栏顶着"),
    "10004": ("单位时间访问过于频繁", "降低请求频率（代码里 MIN_CALL_INTERVAL_S 调大）"),
    "10005": ("IP 白名单不通过", "控制台 > 配置 里把这个出口 IP 加进白名单，或清空白名单"),
    "10009": ("Key 与绑定的平台不符", "★ 最常见：Key 类型选成了 Android/iOS/JS，要改成「Web服务」"),
    "10012": ("权限不足，服务被拒绝", "控制台里给这个 Key 勾上「逆地理编码」"),
    "10044": ("账号维度日调用量超限", "等第二天恢复，或申请提升配额"),
}


def mask(key: str) -> str:
    if len(key) <= 12:
        return "*" * len(key)
    return f"{key[:6]}…{key[-4:]}  (长度 {len(key)})"


def raw_query(key: str, lat: float, lon: float) -> dict:
    params = {
        "key": key,
        "location": f"{lon:.6f},{lat:.6f}",
        "extensions": "all",
        "radius": "200",
        "output": "JSON",
    }
    url = f"{REVERSE_GEOCODE_URL}?{urlencode(params)}"
    return _default_http_get(url, 8.0)


def describe(payload: dict) -> str:
    """把响应压成一行可读结论。"""
    if not isinstance(payload, dict):
        return "响应不是 JSON"
    if payload.get("status") != "1":
        code = str(payload.get("infocode", "?"))
        info = payload.get("info", "?")
        hint, action = ERROR_HINTS.get(code, ("未知错误", "看高德文档的错误码说明"))
        return f"× 失败 [{code}] {info} — {hint}；{action}"
    regeo = payload.get("regeocode") or {}
    poi = extract_poi_name(payload) or "(认不出地名)"
    address = regeo.get("formatted_address") if isinstance(regeo, dict) else None
    return f"√ 「{poi}」  地址: {address}"


def main() -> int:
    ap = argparse.ArgumentParser(description="高德 Key 体检")
    ap.add_argument("--key", help="不推荐：直接在命令行传 Key（会留在 shell 历史里）")
    ap.add_argument("--areas", default=AREAS_YAML)
    ap.add_argument("--quick", action="store_true", help="只做连通性测试")
    args = ap.parse_args()

    key = args.key or os.environ.get("AMAP_WEB_KEY", "")
    print("高德 Key 体检")
    if not key:
        print("=" * 76)
        print("没有拿到 Key。请先设置环境变量：")
        print('  PowerShell:  $env:AMAP_WEB_KEY="你的key"')
        print("  Linux/Pi  :  export AMAP_WEB_KEY=你的key")
        print()
        print("不设也能跑 —— 本地景区围栏是主力，高德只是去陌生地方的兜底。")
        return 1

    print(f"Key: {mask(key)}")
    print("=" * 76)

    # ── 1. 连通性：挑一个准确的坐标（外滩陈毅广场附近）────────────────────
    print("\n[1/3] 连通性测试")
    probe_lat, probe_lon = 31.2395, 121.4865
    try:
        payload = raw_query(key, probe_lat, probe_lon)
    except Exception as exc:
        print(f"  × 请求失败：{type(exc).__name__}: {exc}")
        print("    网络不通？公司网络/校园网挡了？换个网络再试。")
        return 1

    line = describe(payload)
    print(f"  查询点: WGS84 {probe_lat:.4f}, {probe_lon:.4f}")
    print(f"  {line}")
    if not line.startswith("√"):
        return 1

    cache_dir = os.path.join(ROOT, "data")
    os.makedirs(cache_dir, exist_ok=True)
    captured: dict = {}

    def recording_get(url: str, timeout: float) -> dict:
        data = _default_http_get(url, timeout)
        captured["last"] = data
        return data

    resolver = PoiResolver(key, cache_path=os.path.join(cache_dir, "amap_check_cache.sqlite"),
                           http_get=recording_get, min_interval=0.6)

    if args.quick:
        print("\n（--quick：跳过坐标核对）")
        return 0

    try:
        index = GeofenceIndex.load(args.areas)
    except Exception as exc:
        print(f"\n读不了 {args.areas}：{exc}")
        return 1

    # ── 2. 坐标系差异实测 ────────────────────────────────────────────────
    print("\n[2/3] 坐标系实测：同一地点，WGS84 原值 vs GCJ-02 折算值")
    print("      我们的代码发出去的是 WGS84（GNSS 模块的原始输出）")
    print("      高德期望的是 GCJ-02。所以真实偏移量有多大，这里直接量给你看\n")
    print(f"      {'景区':<22} {'偏移量':>8}   WGS84 查到的        GCJ-02 查到的")
    print("      " + "─" * 68)

    for area in index.areas:
        g_lat, g_lon = wgs84_to_gcj02(area.lat, area.lon)
        offset = haversine_m(area.lat, area.lon, g_lat, g_lon)

        first = raw_query(key, area.lat, area.lon)
        name_wgs = extract_poi_name(first) or "-"
        second = raw_query(key, g_lat, g_lon)
        name_gcj = extract_poi_name(second) or "-"

        same = "  " if name_wgs == name_gcj else "←不同"
        label = area.name if len(area.name) <= 20 else area.name[:19] + "…"
        print(f"      {label:<22} {offset:>6.0f}m   {name_wgs:<18} {name_gcj}  {same}")

    # ── 3. 逐个点位核对 ──────────────────────────────────────────────────
    print("\n[3/3] 配置里的坐标，在高德眼里是哪儿")
    print("      （我们发 WGS84，高德按 GCJ-02 理解，所以落点会偏几百米；")
    print("        看地名对不对得上就够了，这是拿独立数据源给你交叉验证）\n")

    for area in index.areas:
        got = resolver.lookup_sync(area.lat, area.lon)
        flag = "√" if any(tok in got for tok in area.name[:2]) else "?"
        print(f"  {flag} 景区 {area.name}")
        print(f"        配置坐标 WGS84 {area.lat:.6f}, {area.lon:.6f}  半径 {area.radius_m:.0f}m")
        print(f"        高德认作: 「{got}」")
        for spot in area.spots:
            spot_got = resolver.lookup_sync(spot.lat, spot.lon)
            print(f"        · 点位「{spot.name}」 → 高德认作「{spot_got}」")
        print()

    print("=" * 76)
    print("怎么看这个结果：")
    print("  · 景区中心返回的地名和你的预期基本吻合 → 坐标没问题")
    print("  · 返回一个明显不相关的地名 → 该景区的坐标可能填错了（差了几公里）")
    print("  · 点位返回的地名对不上 → 正常。点位半径才几十米，网上抄的坐标本来就不准，")
    print("    必须用 tools/nmea_recorder.py --pick 到现场实测")
    print("  · 『WGS84 查到的』和『GCJ-02 查到的』不一致 → 说明这点偏移确实会改变判定，")
    print("    这也是为什么点位必须用实测的 WGS84，不能抄高德拾取器的值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
