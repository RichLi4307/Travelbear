"""用高德的 POI 搜索，自动给 scenic_areas.yaml 里的点位找坐标。

为什么要这个：`tools/amap_check.py` 做的是**逆**地理编码（坐标→地名），
而填配置需要的是**正**向搜索（地名→坐标）。高德对"外白渡桥""静安公园"
这类有名有姓的地标，POI 库里的点是有人工校核过的，通常比从地图上随手抄准。

※️ 但别把它当真理：
  * 高德给的点可能是**整个多边形的质心**，不是你想让人站的那个入口
  * 冷门点位可能搜不到，或者搜到同名的别处
  * 所以跑完这个，你还是得**到现场走一遍确认** —— 但那是"验证"，
    不是"拿尺子量"，5 分钟能走完，不是两小时的测绘

默认是演练模式，只打印不写文件。确认无误再加 --write。

用法：
    export AMAP_WEB_KEY=你的key
    python tools/amap_fill_spots.py                  # 演练：只对比，不改文件
    python tools/amap_fill_spots.py --write          # 真写（会先备份 .bak）
    python tools/amap_fill_spots.py --city 上海 --keywords-suffix ""
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location.geocode import _default_http_get                    # noqa: E402
from location.geofence import (GeofenceIndex, gcj02_to_wgs84,     # noqa: E402
                               haversine_m, wgs84_to_gcj02)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AREAS_YAML = os.path.join(ROOT, "location", "scenic_areas.yaml")

PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"

MIN_INTERVAL_S = 0.55          # 高德个人 Key 的 QPS 很低，发快了会报 10021
_last_call = [0.0]

# 想让高德优先返回景区类 POI，把类型权重调高
SCENIC_HINTS = ("风景名胜", "旅游景点", "公园", "寺庙", "教堂", "文化", "遗址",
                "广场", "码头", "桥", "地铁", "博物馆", "纪念馆")


def search_keywords(name: str) -> str:
    """点位名里括号中的是"给游客听的补充说明"，不是搜索关键词。

    例如「陈毅广场（南京东路口）」要搜"陈毅广场"，带着括号搜会跑到别的地方去。
    """
    return re.split(r"[（(]", name)[0].strip() or name


def throttle() -> None:
    gap = time.monotonic() - _last_call[0]
    if gap < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - gap)
    _last_call[0] = time.monotonic()


def search_poi(key: str, keywords: str, city: str, limit: int = 8) -> list[dict]:
    params = {
        "key": key,
        "keywords": keywords,
        "city": city,
        "citylimit": "true",
        "offset": str(limit),
        "page": "1",
        "extensions": "base",
        "output": "JSON",
    }
    url = f"{PLACE_TEXT_URL}?{urlencode(params)}"

    for attempt in range(3):
        throttle()
        payload = _default_http_get(url, 8.0)
        infocode = str((payload or {}).get("infocode", ""))
        if infocode == "10021":            # 单位时间访问过频，等一下重试
            time.sleep(1.5 * (attempt + 1))
            continue
        if not isinstance(payload, dict) or payload.get("status") != "1":
            raise RuntimeError(f"高德返回异常: {payload.get('info')} [{infocode}]")
        return payload.get("pois") or []
    raise RuntimeError("高德持续返回 10021（访问过频），把 MIN_INTERVAL_S 调大再试")


def score(poi: dict, want: str) -> tuple:
    """排序用：名字完全相同 > 包含 > 类型像景点 > 高德自己的重要度。"""
    name = str(poi.get("name") or "")
    poi_type = str(poi.get("type") or "")
    exact = 0 if name == want else 1
    contains = 0 if want in name else 1
    scenic = 0 if any(hint in poi_type for hint in SCENIC_HINTS) else 1
    return (exact, contains, scenic, name)


def parse_location(poi: dict) -> tuple[float, float] | None:
    raw = str(poi.get("location") or "")
    if "," not in raw:
        return None
    lon_s, lat_s = raw.split(",", 1)
    try:
        return float(lat_s), float(lon_s)      # 注意高德是 经度,纬度
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="用高德 POI 搜索填充点位坐标")
    ap.add_argument("--key", help="不推荐：命令行传 Key 会留在 shell 历史里")
    ap.add_argument("--areas", default=AREAS_YAML)
    ap.add_argument("--city", default="上海")
    ap.add_argument("--write", action="store_true", help="真的写回 yaml（默认只演练）")
    ap.add_argument("--max-shift", type=float, default=1500.0,
                    help="新坐标和原坐标差超过这个米数就跳过（防止搜到同名的别处）")
    args = ap.parse_args()

    key = args.key or os.environ.get("AMAP_WEB_KEY", "")
    if not key:
        print("没有 Key。先设置 AMAP_WEB_KEY 环境变量。")
        return 1

    index = GeofenceIndex.load(args.areas)
    total_spots = index.spot_count

    print("高德 POI 搜索 → 点位坐标")
    print("=" * 78)
    print(f"配置: {args.areas}")
    print(f"城市: {args.city}   模式: {'写入' if args.write else '演练（不改文件）'}")
    print("=" * 78)

    results = []
    unmatched = []
    skipped = []

    for area in index.areas:
        for spot in area.spots:
            query = search_keywords(spot.name)
            try:
                pois = search_poi(key, query, args.city)
            except Exception as exc:
                print(f"  × 「{spot.name}」搜索失败：{exc}")
                unmatched.append((spot.name, "请求失败"))
                continue

            if not pois:
                print(f"  × 「{spot.name}」高德搜不到（搜索词：{query}）")
                unmatched.append((spot.name, "高德无结果"))
                continue

            best = sorted(pois, key=lambda p: score(p, query))[0]
            loc = parse_location(best)
            if loc is None:
                unmatched.append((spot.name, "返回坐标格式异常"))
                continue

            g_lat, g_lon = loc                       # 高德给的是 GCJ-02
            w_lat, w_lon = gcj02_to_wgs84(g_lat, g_lon)   # 折算成 WGS84 再比距离
            shift = haversine_m(spot.lat, spot.lon, w_lat, w_lon)

            found_name = str(best.get("name"))
            flag = "√" if found_name == spot.name else "?"
            note = ""
            if shift > args.max_shift:
                note = f"  ※ 偏移 {shift:.0f}m，可能搜到同名的别处，已跳过"
                skipped.append(spot.name)
            elif flag == "?":
                note = f"  （高德返回的是「{found_name}」）"

            print(f"\n  {flag} 点位「{spot.name}」   ({area.name})")
            if query != spot.name:
                print(f"      搜索词   : 「{query}」")
            print(f"      现在配置 : WGS84 {spot.lat:.6f}, {spot.lon:.6f}   半径 {spot.radius_m:.0f}m")
            print(f"      高德搜到 : 「{found_name}」")
            print(f"                 GCJ-02 {g_lat:.6f}, {g_lon:.6f}")
            print(f"                 折算回 WGS84 {w_lat:.6f}, {w_lon:.6f}")
            print(f"      两者相距 : {shift:.0f}m   地址: {best.get('address')}{note}")

            if shift <= args.max_shift:
                results.append({
                    "spot": spot, "name": found_name,
                    "g_lat": g_lat, "g_lon": g_lon,
                    "w_lat": w_lat, "w_lon": w_lon, "shift": shift,
                })

    print("\n" + "=" * 78)
    print(f"汇总：可更新 {len(results)} / {total_spots} 个点位"
          f"，搜不到 {len(unmatched)}，偏移过大跳过 {len(skipped)}")

    if unmatched:
        print("\n搜不到的点位（这些只能到现场实测，或者换个更完整的名字再搜）：")
        for name, why in unmatched:
            print(f"    · {name}  （{why}）")

    if not results:
        print("\n没有可写入的内容。")
        return 0

    print("\n注意：高德给的点可能是一个区域/多边形的质心，不一定是你要人站的入口。")
    print("      写进去之后，务必去现场走一遍确认（那是验证，不是测量，5 分钟的事）。")

    if not args.write:
        print("\n这是演练模式，文件没有改动。确认上面的结果合理后，加 --write 写入。")
        return 0

    backup = args.areas + ".bak"
    shutil.copyfile(args.areas, backup)
    print(f"\n已备份原文件 → {os.path.basename(backup)}")

    with open(args.areas, "r", encoding="utf-8") as fh:
        text = fh.read()

    replaced = 0
    for item in results:
        spot = item["spot"]
        old_block = (f'      - name: "{spot.name}"\n'
                     f"        lat: {spot.lat:.6f}")
        # 找到该点位那一段，只替换 lat/lon 两行
        marker = f'- name: "{spot.name}"'
        pos = text.find(marker)
        if pos < 0:
            print(f"  ! 在文件里找不到「{spot.name}」的原条目，跳过")
            continue
        end = text.find("- name:", pos + len(marker))
        if end < 0:
            end = len(text)
        block = text[pos:end]
        lines = block.splitlines(keepends=True)
        rebuilt = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("lat:"):
                indent = line[: len(line) - len(line.lstrip())]
                rebuilt.append(f"{indent}lat: {item['w_lat']:.6f}"
                               f"   # 高德POI「{item['name']}」折算，原始GCJ02 "
                               f"{item['g_lon']:.6f},{item['g_lat']:.6f}\n")
            elif stripped.startswith("lon:"):
                indent = line[: len(line) - len(line.lstrip())]
                rebuilt.append(f"{indent}lon: {item['w_lon']:.6f}\n")
            else:
                rebuilt.append(line)
        text = text[:pos] + "".join(rebuilt) + text[end:]
        replaced += 1

    with open(args.areas, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"已更新 {replaced} 个点位。备份在 {os.path.basename(backup)}")
    print("\n跑一下验证：")
    print("    python -m pytest -q tests/test_config_files.py")
    print("    python tools/simulate_route.py --assert")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
