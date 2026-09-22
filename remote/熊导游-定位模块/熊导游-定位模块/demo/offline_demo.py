"""全链路演示：把"按一下按钮"从头到尾演一遍。**没有任何停顿，瞬间跑完。**

现在采购还没到货，所以【摄像头、麦克风、扬声器、GPS、信标】都还没有。
但这个演示能说清楚一件最重要的事：

    **软件链路是通的，四个模块的接口是咬合的，现在缺的只是硬件输入。**

它分三幕：
  第一幕  真实调用 —— 真的调 get_location()，看它在没有硬件时怎么优雅降级
  第二幕  注入假 GPS —— 完整跑一遍"按键 → 定位 → 识图 → 生成文案 → 播报"
  第三幕  走进室内 —— GPS 丢失，自动降级到蓝牙信标

※️ 识图和语音这两段是**假数据**（占位），因为那两个模块还没人写。
   等 vision/ 和 tts/ 出来，把 _fake_vision() / _fake_tts() 换成真实调用即可 ——
   接口形状就是照着分工文件里的契约写的。

用法：
    python demo/offline_demo.py                    # 三幕全演
    python demo/offline_demo.py --place 静安寺      # 第二幕换个地点
    python demo/offline_demo.py --place 静安寺山门   # 也可以直接指定点位
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows 控制台默认是 GBK，编不出的字符别让整个脚本崩掉
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

from location import get_location                       # noqa: E402
from location.ble_scan import BeaconRegistry            # noqa: E402
from location.contract import BeaconHit, Fix            # noqa: E402
from location.geofence import GeofenceIndex             # noqa: E402
from location.switch import LocationStateMachine        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AREAS_YAML = os.path.join(ROOT, "location", "scenic_areas.yaml")
BEACONS_YAML = os.path.join(ROOT, "location", "beacons.yaml")

WIDTH = 78


# ════════════════════════════════════════════════════════════════════ 输出小工具
def rule(char: str = "─") -> None:
    print(char * WIDTH)


def act(number: int, title: str, subtitle: str = "") -> None:
    print()
    rule("═")
    print(f"  第 {number} 幕   {title}")
    if subtitle:
        print(f"            {subtitle}")
    rule("═")


def step(clock: float, text: str) -> None:
    print(f"  t={clock:5.2f}s   {text}")


# ══════════════════════════════════════════════════════════════ 假模块（占位）
def _fake_vision() -> dict:
    """占位：真实的 vision/vision.py 应该返回同样的结构。

    分工文件冻结的接口：def describe_scene() -> dict
    """
    return {
        "scene": "黄浦江边的万国建筑群，江对岸是陆家嘴天际线",
        "confidence": 0.88,
        "_note": "假数据（vision 模块未实现）",
    }


def _fake_tts(text: str) -> dict:
    """占位：真实的 tts/tts.py 接口是 def speak(text: str) -> None。"""
    return {
        "text": text,
        "engine": "假数据（tts 模块未实现）",
        "duration_s": round(len(text) / 5.5, 1),      # 中文大约每秒 5.5 字
    }


def _fake_llm(prompt_facts: dict) -> str:
    """占位：真实的 agent/ 会用 LLM 生成，这里用模板冒充，只为了看形状。"""
    place = prompt_facts["place"]
    scene = prompt_facts["scene"]
    return (f"您现在站在{place}。眼前是{scene}——"
            f"这些建筑从十九世纪一直站到今天，看惯了江水涨落，也看惯了来来往往的人。")


class FakeClock:
    """演示用的可控时钟：让"35 秒后定位过期"这种场景不用真的等 35 秒。"""

    def __init__(self, t: float = 0.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ══════════════════════════════════════════════════════════════════════ 三幕
def act_one() -> None:
    act(1, "真实调用：没有硬件时会怎样",
        "这不是模拟，是真的在调 get_location()")

    print("\n  现在这台笔记本上：没插 GNSS 模块，也没有蓝牙信标。\n")
    started = time.perf_counter()
    payload = get_location()          # ← 真的调用
    elapsed_ms = (time.perf_counter() - started) * 1000

    step(0.0, "按下按钮 → agent 调 location.get_location()")
    print()
    for line in json.dumps(payload, ensure_ascii=False, indent=6).splitlines():
        print(f"  {line}")
    print()
    step(elapsed_ms / 1000, f"返回耗时 {elapsed_ms:.1f} ms")
    print()
    print("  √ 没崩、没卡住、没有抛异常 —— 返回 mode=\"none\"")
    print("    这是契约里的硬要求。评委最容易记住的就是'翻车了会不会崩'，")
    print("    而不是'顺利时有多顺'。")
    print()
    print("  → 所以现在缺的不是软件，是硬件输入。")


def act_two(clock: FakeClock, place: str,
            index: GeofenceIndex, registry: BeaconRegistry) -> None:
    act(2, "注入假 GPS：完整链路跑一遍",
        f"假装你正站在「{place}」，按下按钮")

    # 先按点位名找，找不到再按景区名找
    area = index.areas[0]
    spot = None
    for candidate in index.areas:
        for s in candidate.spots:
            if s.name == place:
                area, spot = candidate, s
                break
        if spot is not None:
            break
    if spot is None:
        for candidate in index.areas:
            if candidate.name == place:
                area = candidate
                break

    lat = spot.lat if spot else area.lat
    lon = spot.lon if spot else area.lon
    where = spot.name if spot else area.name
    print(f"\n  假装 GPS 给出一组坐标：{lat:.6f}, {lon:.6f}   （{where} 附近）\n")

    sm = LocationStateMachine(geofence=index, geocoder=None, clock=clock)

    clock.t = 0.0
    step(0.00, "【用户】按下肩带上的按钮")

    fix = Fix(lat=lat, lon=lon, quality=1, satellites=9, hdop=0.9, ts=clock.t)
    loc = sm.decide(fix, None)
    clock.t = 0.02
    step(0.02, f"【定位】get_location() → 「{loc.poi_name}」  "
               f"(mode={loc.mode}, confidence={loc.confidence:.2f})")
    print("           ↑ 0.02 秒。因为它从开机起就在后台盯着卫星，"
          "不是现在才开始找。")

    clock.t = 3.0
    vision = _fake_vision()
    step(3.00, f"【识图】describe_scene() → 「{vision['scene']}」")
    print("           ↑ 这是假数据。真实模块用 picamera2 抓帧 + 云端 VLM。")

    clock.t = 3.1
    text = _fake_llm({"place": loc.poi_name, "scene": vision["scene"]})
    step(3.10, "【Agent】把位置 + 场景 + 导游人设拼成提示词，调 LLM 生成文案")
    print(f"           生成的讲解：{text}")

    clock.t = 5.0
    audio = _fake_tts(text)
    step(5.00, f"【语音】speak() → 扬声器出声，约 {audio['duration_s']} 秒")
    print("           ↑ 这是假数据。真实模块用小米 MiMo-TTS，断网回落 Piper。")

    print()
    print("  √ 整条链路的形状是通的：每个模块只暴露一个函数，")
    print("    agent 负责编排，模块之间互不阻塞。")
    print("  ※ 识图和语音两段是占位。那两个模块做出来之后，")
    print("    把 _fake_vision / _fake_tts 换成真实调用即可，接口不用改。")


def act_three(clock: FakeClock, index: GeofenceIndex, registry: BeaconRegistry) -> None:
    act(3, "走进室内：GPS 丢失，自动降级到蓝牙信标",
        "这是室内展位场景要用的路径")

    sm = LocationStateMachine(geofence=index, geocoder=None, clock=clock)
    area = index.areas[0]

    print()
    clock.t = 100.0
    fix = Fix(lat=area.lat, lon=area.lon, quality=1, satellites=9, hdop=0.9, ts=clock.t)
    loc = sm.decide(fix, None)
    step(0.0, f"在室外          → mode={loc.mode:<4} 「{loc.poi_name}」")

    # 走进展厅：GNSS 停止输出新句子，缓存里那条旧 fix 随时间自然过期
    clock.advance(35.0)
    hit = None
    if len(registry):
        beacon_key = next(iter(registry._map))
        uuid, major, minor = beacon_key
        hit = BeaconHit(uuid=uuid, major=major, minor=minor, rssi=-62,
                        ts=clock.t, name=registry.name_for(uuid, major, minor))
    loc = sm.decide(fix, hit)          # fix 还是那条旧的，但已经过期 35 秒
    step(35.0, f"走进展厅 35s 后 → mode={loc.mode:<4} 「{loc.poi_name}」")
    print("           ↑ 旧定位过期 → 自动转去扫信标，没有人工干预")

    clock.advance(10.0)
    fresh = Fix(lat=area.lat, lon=area.lon, quality=1, satellites=9, hdop=0.9, ts=clock.t)
    loc = sm.decide(fresh, hit)
    step(45.0, f"走到门口，GPS 又有了 → mode={loc.mode:<4} 「{loc.poi_name}」")
    print("           ↑ 20 秒迟滞期内不切回去 —— 否则在门口来回走会疯狂抖动")

    clock.advance(11.0)
    fresh = Fix(lat=area.lat, lon=area.lon, quality=1, satellites=9, hdop=0.9, ts=clock.t)
    loc = sm.decide(fresh, hit)
    step(56.0, f"再过 11 秒        → mode={loc.mode:<4} 「{loc.poi_name}」")
    print("           ↑ 迟滞窗口过去，切回 GPS")

    print()
    print("  √ 三种模式（gps / ble / none）在这套状态机里全部走通了：")
    print("    none 是第一幕真实调出来的，gps / ble 这两幕喂的是假数据，")
    print("    但走的是**真实的切换状态机和真实的围栏判定**。")


# ══════════════════════════════════════════════════════════════════════ 入口
def main() -> int:
    ap = argparse.ArgumentParser(description="零硬件全链路演示")
    ap.add_argument("--place", default="外滩", help="第二幕假装站在哪（景区名或点位名）")
    ap.add_argument("--acts", default="1,2,3",
                    help="演哪几幕。默认 1,2,3；当众演示时建议一幕一幕跑，"
                         "跑完一幕你讲一段，再跑下一幕")
    args = ap.parse_args()

    wanted = {"1", "2", "3"} if args.acts.strip().lower() == "all" else {
        part.strip() for part in args.acts.split(",") if part.strip()}

    # 演示要输出干净：把"串口打不开，3 秒后重连"这类日志压掉
    logging.disable(logging.WARNING)

    index = GeofenceIndex.load(AREAS_YAML)
    registry = BeaconRegistry.load(BEACONS_YAML)
    clock = FakeClock()

    print()
    rule("═")
    print("  具身智能导游伙伴 · 定位模块演示")
    print(f"  配置：{len(index)} 个景区 / {index.spot_count} 个点位"
          f" / {len(registry)} 个信标映射")
    print("  硬件状态：GNSS 模块 ×   蓝牙信标 ×   摄像头 ×   扬声器 ×")
    print("  软件状态：定位 ✅   识图（假）   语音（假）   Agent（模板）")
    rule("═")

    if "1" in wanted:
        act_one()
    if "2" in wanted:
        act_two(clock, args.place, index, registry)
    if "3" in wanted:
        act_three(clock, index, registry)

    if wanted == {"1", "2", "3"}:
        print()
        rule("═")
        print("  演示结束")
        print()
        print("  真正的下一步不是继续写代码，是把采购单下掉 ——")
        print("  GPS 到货那天，这个演示里第一幕的 mode 就会从 none 变成 gps。")
        rule("═")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
