"""路线模拟器：没有硬件也能把整条定位链路跑起来看。

它不 mock 任何业务逻辑 —— 喂的是假坐标，走的是**真实的** GeofenceIndex 和
LocationStateMachine，读的是**真实的** scenic_areas.yaml。
所以你在笔记本上看到的输出，就是 GPS 到货后现场会看到的输出。

三个用途：
  1. 今天就能验证代码对不对（不用等采购到货）
  2. 改 scenic_areas.yaml 的半径/坐标后，立刻看判定会不会跳变
  3. 答辩演示素材：把"走进景区自动识别"讲清楚，比 PPT 有说服力

用法：
    python tools/simulate_route.py                 # 跑全部场景
    python tools/simulate_route.py --list          # 只看有哪些场景
    python tools/simulate_route.py --scenario 外滩  # 跑单个
    python tools/simulate_route.py --assert        # 顺便断言结果，当测试用
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location.ble_scan import BeaconRegistry          # noqa: E402
from location.contract import (BeaconHit, Fix,         # noqa: E402
                               GEOFENCE_EXIT_RATIO)
from location.geofence import GeofenceIndex            # noqa: E402
from location.switch import LocationStateMachine       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AREAS_YAML = os.path.join(ROOT, "location", "scenic_areas.yaml")
BEACONS_YAML = os.path.join(ROOT, "location", "beacons.yaml")

M_PER_DEG_LAT = 111195.08

def north_of(lat: float, lon: float, meters: float) -> tuple[float, float]:
    return lat + meters / M_PER_DEG_LAT, lon


def along(lat1, lon1, lat2, lon2, ratio: float) -> tuple[float, float]:
    return lat1 + (lat2 - lat1) * ratio, lon1 + (lon2 - lon1) * ratio


def find_area(index: GeofenceIndex, name: str):
    for area in index.areas:
        if area.name == name:
            return area
    raise KeyError(f"配置里没有景区「{name}」，现有的是 {[a.name for a in index.areas]}")


def find_spot(index: GeofenceIndex, area_name: str, spot_name: str):
    for spot in find_area(index, area_name).spots:
        if spot.name == spot_name:
            return spot
    raise KeyError(f"景区「{area_name}」里没有点位「{spot_name}」")


class SimClock:
    """可控时钟。真时钟没法测 20 秒迟滞，那会变成真的 sleep(20)。"""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def display_width(text: str) -> int:
    """中文字符占两列，用它算对齐，否则输出会参差不齐。"""
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


def pad(text: str, target: int) -> str:
    return text + " " * max(0, target - display_width(text))


# ─────────────────────────────────────────────────────────────────────────────
# 场景定义。每一步：
#   desc    这一步在干什么（会打印出来）
#   at      产生一次**新的**定位 (lat, lon)，时间戳是"现在"
#   hold    true = 不再产生新定位，沿用上一次那个（随 advance 自然变旧、过期）
#   sats/hdop/quality   定位质量
#   advance 先推进多少秒（用来测 fix 过期和迟滞）
#   beacon  (uuid, major, minor, rssi)，不给就是没有信标
#   expect  (mode, poi_name) —— 预期结果；整个键不给表示不检查
#
# `at` 和 `hold` 的区别是模拟的关键：走进室内时，GNSS 模块停止输出新句子，
# 缓存里那条旧 fix 会随时间自然过期 —— 这就是 hold 要表达的事。
#
# ★ 坐标一律**从 scenic_areas.yaml 现取**，不写死。
#   所以你改了配置里的坐标/半径，场景会自动跟着走，不会变成一堆假失败。
#   场景里认的是"名字"—— 名字才是契约，坐标只是数据。
# ─────────────────────────────────────────────────────────────────────────────
BEACON = ("FDA50693-A4E2-4FB1-AFCF-C6EB07647825", 1, 1, -60)


def build_scenarios(index: GeofenceIndex) -> dict[str, list[dict]]:
    bund = find_area(index, "外滩")
    jingan = find_area(index, "静安寺")
    bund_c = (bund.lat, bund.lon)
    jingan_c = (jingan.lat, jingan.lon)

    def spot(area_name: str, spot_name: str) -> tuple[float, float]:
        s = find_spot(index, area_name, spot_name)
        return s.lat, s.lon

    wai_bai_du = spot("外滩", "外白渡桥")
    chen_yi = spot("外滩", "陈毅广场（南京东路口）")
    shi_liu_pu = spot("外滩", "十六铺码头（延安东路口）")

    # 缓冲带宽度由配置里的半径和 GEOFENCE_EXIT_RATIO 决定，这里跟着算
    band = bund.radius_m * GEOFENCE_EXIT_RATIO
    just_outside = north_of(*bund_c, bund.radius_m + 50.0)
    way_outside = north_of(*bund_c, band + 100.0)

    return {
        "外滩漫步": [
            {"desc": "站在外白渡桥（在点位圈里）", "at": wai_bai_du,
             "expect": ("gps", "外白渡桥")},
            {"desc": "走到陈毅广场", "at": chen_yi,
             "expect": ("gps", "陈毅广场（南京东路口）")},
            {"desc": "再走到十六铺码头", "at": shi_liu_pu,
             "expect": ("gps", "十六铺码头（延安东路口）")},
            {"desc": "走到江边，但不在任何点位圈里",
             "at": north_of(*bund_c, 350.0),
             "expect": ("gps", "外滩")},
            {"desc": "沿南京东路往西走，走出景区围栏", "at": way_outside,
             "expect": ("gps", "当前位置附近")},
        ],
        "精度不够时只报景区": [
            {"desc": "站在陈毅广场正中，9 颗星", "at": chen_yi,
             "expect": ("gps", "陈毅广场（南京东路口）")},
            {"desc": "同一个位置，掉到 4 颗星（树荫下）", "at": chen_yi,
             "sats": 4, "hdop": 8.0,
             "expect": ("gps", "外滩")},
            {"desc": "只剩 2 颗星 —— 连景区都不配判了", "at": chen_yi,
             "sats": 2, "hdop": 8.0,
             "expect": ("none", "定位不可用")},
        ],
        "景区边界迟滞": [
            {"desc": "走进外滩", "at": bund_c, "expect": ("gps", "外滩")},
            {"desc": f"往外走 {bund.radius_m + 50:.0f}m"
                     f"（已出 {bund.radius_m:.0f}m 围栏，但在 1.15 倍缓冲带内）",
             "at": just_outside, "expect": ("gps", "外滩")},
            {"desc": f"再走到 {band + 100:.0f}m（超出缓冲带）",
             "at": way_outside, "expect": ("gps", "当前位置附近")},
        ],
        "地铁上从外滩到静安寺": [
            {"desc": f"第 {i + 1}/9 段，已走 {ratio * 100:.0f}%",
             "at": along(*bund_c, *jingan_c, ratio),
             "expect": expect}
            for i, (ratio, expect) in enumerate([
                (0.0, ("gps", "外滩")),
                (0.125, None),
                (0.25, ("gps", "当前位置附近")),
                (0.375, None), (0.5, None), (0.625, None), (0.75, None),
                (0.875, None),
                (1.0, ("gps", "静安寺")),
            ])
        ],
        "走进室内（GPS 丢失转蓝牙）": [
            {"desc": "在室外，GPS 正常", "at": bund_c, "expect": ("gps", "外滩")},
            {"desc": "走进展厅，GNSS 不再出句子；35 秒后旧定位过期",
             "hold": True, "advance": 35.0, "beacon": BEACON,
             "expect": ("ble", "三号展厅-青铜器展位")},
            {"desc": "走到门口，GPS 又拿到了新定位 —— 但迟滞期内不切回去",
             "at": bund_c, "advance": 10.0, "beacon": BEACON,
             "expect": ("ble", "三号展厅-青铜器展位")},
            {"desc": "又过 11 秒（累计 21s > 20s 迟滞），切回 GPS",
             "hold": True, "advance": 11.0, "beacon": BEACON,
             "expect": ("gps", "外滩")},
        ],
        "什么都没有（翻车兜底）": [
            {"desc": "没插 GPS，也没扫到信标", "expect": ("none", "定位不可用")},
        ],
    }


def evaluate_scenario(name: str, index: GeofenceIndex, registry: BeaconRegistry,
                      scenarios: dict | None = None):
    """按顺序执行场景，逐步 yield (step, Location)。

    打印和断言都基于它，所以你在屏幕上看到的和测试校验的必然是同一件事。
    """
    scenarios = scenarios if scenarios is not None else build_scenarios(index)
    clock = SimClock()
    sm = LocationStateMachine(geofence=index, geocoder=None, clock=clock)
    last_fix: Fix | None = None

    for step in scenarios[name]:
        clock.t += float(step.get("advance", 0.0))

        if step.get("at"):
            lat, lon = step["at"]
            last_fix = Fix(lat=lat, lon=lon,
                           quality=step.get("quality", 1),
                           satellites=step.get("sats", 9),
                           hdop=step.get("hdop", 0.9),
                           ts=clock.t)
        elif not step.get("hold"):
            last_fix = None      # 明确表示"没有定位"
        # hold=True 时沿用上一条 fix —— 它的时间戳不变，所以会随 advance 自然过期

        hit = None
        if step.get("beacon"):
            uuid, major, minor, rssi = step["beacon"]
            hit = BeaconHit(uuid=uuid, major=major, minor=minor, rssi=rssi,
                            ts=clock.t,
                            name=registry.name_for(uuid, major, minor))

        yield step, sm.decide(last_fix, hit)


def run_scenario(name: str, index: GeofenceIndex, registry: BeaconRegistry,
                 check: bool, scenarios: dict | None = None) -> tuple[int, int]:
    print(f"\n场景：{name}")
    print("─" * 76)
    passed = failed = 0

    for step, loc in evaluate_scenario(name, index, registry, scenarios):
        expect = step.get("expect")
        if expect is not None and check:
            if (loc.mode, loc.poi_name) == expect:
                passed += 1
            else:
                failed += 1
                print(f"  × 期望 {expect[0]}/「{expect[1]}」"
                      f"，实际 {loc.mode}/「{loc.poi_name}」  ← {step['desc']}")
                continue
            mark = "√"
        else:
            mark = " "

        label = pad(f"{loc.mode:<4}「{loc.poi_name}」", 34)
        print(f"  {mark} {label} conf={loc.confidence:.2f}   {step['desc']}")

    return passed, failed


def main() -> int:
    ap = argparse.ArgumentParser(description="定位链路路线模拟器")
    ap.add_argument("--scenario", help="场景名（不给就跑全部）")
    ap.add_argument("--list", action="store_true", help="只列出场景名")
    ap.add_argument("--assert", dest="check", action="store_true",
                    help="断言预期结果，当测试用")
    ap.add_argument("--areas", default=AREAS_YAML)
    ap.add_argument("--beacons", default=BEACONS_YAML)
    args = ap.parse_args()

    index = GeofenceIndex.load(args.areas)
    registry = BeaconRegistry.load(args.beacons)
    scenarios = build_scenarios(index)

    if args.list:
        for name in scenarios:
            print(" ", name)
        return 0

    names = [args.scenario] if args.scenario else list(scenarios)
    for name in names:
        if name not in scenarios:
            print(f"没有这个场景：{name}。用 --list 看有哪些。")
            return 1

    print("=" * 76)
    print(f"配置：{len(index)} 个景区 / {index.spot_count} 个点位  "
          f"（来自 {os.path.basename(args.areas)}）")
    print("喂假坐标，走真实判定逻辑 —— 笔记本上看到的，就是现场会看到的")
    print("=" * 76)

    total_pass = total_fail = 0
    for name in names:
        p, f = run_scenario(name, index, registry, args.check, scenarios)
        total_pass += p
        total_fail += f

    print()
    print("=" * 76)
    if args.check:
        print(f"断言结果：{total_pass} 通过，{total_fail} 失败")
        return 1 if total_fail else 0
    print("提示：加 --assert 可以拿它当测试跑（会校验每步的预期结果）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
