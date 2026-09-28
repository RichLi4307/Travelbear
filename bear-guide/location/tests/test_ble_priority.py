# -*- coding: utf-8 -*-
"""BLE 最高优先仲裁规则回归测试（2026-09-28 跨模块修复，负责人拍板）。

锁定 vendor/location/switch.py 的仲裁：
    BLE 展位命中（新鲜） > 一切 GPS 判定

背景：展厅内 GPS 从屋顶/窗缝漏进来的 fix 会把"某展位"覆盖成"某校区"，
讲解当众串味。负责人拍板：信标固定在展位上是物理事实，米级证据无条件
压过任何卫星推导（含 GPS 点位级——消除"漂移恰好落进 spot 圈"的翻车窗）。
GPS 夺回主导权的唯一条件是信标命中过期（BLE_MAX_AGE_S=10s）。

直跑：python location/tests/test_ble_priority.py（无需 pytest）
"""
import math
import os
import sys

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from vendor.location.contract import BeaconHit, Fix, MODE_BLE, MODE_GPS, MODE_NONE
from vendor.location.geofence import GeofenceIndex
from vendor.location.switch import LocationStateMachine

YAML = os.path.join(project_root, "vendor", "location", "scenic_areas.yaml")


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


def make_fix(clock, lat, lon, sats=9, hdop=0.9):
    return Fix(lat=lat, lon=lon, quality=1, satellites=sats, hdop=hdop,
               ts=clock())


def make_hit(clock):
    return BeaconHit(uuid="FDA50693-A4E2-4FB1-AFCF-C6EB07647825",
                     major=1, minor=1, rssi=-64, ts=clock(),
                     name="三号展厅-青铜器展位")


def area_and_spot(index):
    """从真实 yaml 取（景区内非点位坐标, 点位坐标, 景区外坐标）。"""
    bund = next(a for a in index.areas if a.name == "外滩")
    spot = next(s for a in index.areas if a.name == "外滩"
                for s in a.spots if s.name == "外白渡桥")
    # 景区中心：必在 900m 景区圈内，且距任何 spot（最近的外白渡桥 ~725m）
    # 远超 60m 点位圈 —— "景区内非点位"的代表点
    outside_lat = bund.lat + (bund.radius_m + 500.0) / 111320.0
    return (bund.lat, bund.lon), (spot.lat, spot.lon), (outside_lat, bund.lon)


def run():
    index = GeofenceIndex.load(YAML)
    area_pt, spot_pt, out_pt = area_and_spot(index)

    failures = []

    def check(desc, actual, expect):
        ok = actual == expect
        print(f"  {'✅' if ok else '❌'} {desc}：实际={actual} 期望={expect}")
        if not ok:
            failures.append(desc)

    # 1) BLE 命中 + GPS 只有景区级 → BLE 优先（本次修复的核心）
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(make_fix(clock, *area_pt), make_hit(clock))
    check("信标在场+GPS景区级 → 报展位", (loc.mode, loc.poi_name),
          (MODE_BLE, "三号展厅-青铜器展位"))

    # 2) BLE 命中 + GPS 点位级 → 仍报展位（BLE 最高优先，连 spot 级都压不过）
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(make_fix(clock, *spot_pt), make_hit(clock))
    check("信标在场+GPS点位级 → 仍报展位", (loc.mode, loc.poi_name),
          (MODE_BLE, "三号展厅-青铜器展位"))

    # 3) 走出展厅：信标过期(10s) 且 BLE hold(20s) 过期 → 落回 GPS
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    sm.decide(make_fix(clock, *area_pt), make_hit(clock))
    clock.advance(25.0)                       # 信标过期 + hold 过期
    loc = sm.decide(make_fix(clock, *area_pt), None)
    check("信标过期且hold过期 → 落回GPS景区级", (loc.mode, loc.poi_name),
          (MODE_GPS, "外滩"))

    # 4) BLE hold + 最高优先双保险：切 BLE 后 GPS 点位级来了也不切回
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    sm.decide(None, make_hit(clock))
    clock.advance(10.0)
    loc = sm.decide(make_fix(clock, *spot_pt), make_hit(clock))
    check("切BLE后10s(GPS点位级) → 仍报展位", (loc.mode,),
          (MODE_BLE,))

    # 5) 无 BLE：GPS 景区级正常；GPS 弱到不可用 → none
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(make_fix(clock, *area_pt), None)
    check("无信标+GPS景区级 → 报景区", (loc.mode, loc.poi_name),
          (MODE_GPS, "外滩"))
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(make_fix(clock, *area_pt, sats=2), None)
    check("无信标+GPS仅2星 → none", (loc.mode,), (MODE_NONE,))

    # 6) nan HDOP 回归（同事原测试意图：缺数据≠不合格，但要在无 BLE 干扰下测）
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(make_fix(clock, *area_pt, hdop=math.nan), None)
    check("HDOP为空+无信标 → 报景区", (loc.mode, loc.poi_name),
          (MODE_GPS, "外滩"))

    # 7) 景区外：BLE 在场仍报展位（串台风险低于弱GPS漂移，见 tecs 清单）
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(make_fix(clock, *out_pt), make_hit(clock))
    check("景区外+信标在场 → 仍报展位", (loc.mode,),
          (MODE_BLE,))

    # 8) 【2026-09-28 修复】未映射信标（name=None，环境里的陌生 iBeacon）
    #    不算定位证据：GPS 在场时必须落回 GPS，不能拿 UUID 串当景点名
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    ghost = BeaconHit(uuid="66238680-D3C1-474E-A7D7-1833F554A087",
                      major=258, minor=772, rssi=-69, ts=clock(), name=None)
    loc = sm.decide(make_fix(clock, *area_pt), ghost)
    check("陌生信标在场+GPS → 落回GPS景区", (loc.mode, loc.poi_name),
          (MODE_GPS, "外滩"))
    clock = FakeClock()
    sm = LocationStateMachine(geofence=index, clock=clock)
    loc = sm.decide(None, ghost)
    check("只有陌生信标 → none（不播UUID串）", (loc.mode,),
          (MODE_NONE,))

    print()
    if failures:
        print(f"BLE优先仲裁测试失败 {len(failures)} 条：")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("BLE优先仲裁测试全部通过（9 条）")
    return 0


if __name__ == "__main__":
    sys.exit(run())
