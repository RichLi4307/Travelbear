# -*- coding: utf-8 -*-
"""外滩 · 场景转换（讲解目标切换）情景测试

现场真实情形：
    游客拿着小熊往前走，Agent 识别出当前讲解目标并开始讲解；
    人走到下一个点位后，讲解目标就变了 —— 必须**立刻停掉上一段、开始下一段**。
    这个"停旧起新"的手感，是现场最影响体验的一环。

本脚本用外滩 8 个讲解目标模拟整条游览路线，重点量三个数：

    切换延迟      目标变更 → 新目标第一个字出声（用户能感知到的卡顿）
    打断是否干脆   stop() 返回到实际静音的时间（理想 < 100ms）
    有没有串台     快速连切后，正在播的是不是最新目标

用法：
    python test_scene_switch.py             # 默认 dryrun：不出声，但按真实时长走
    python test_scene_switch.py --speak     # 真的出声，听切换效果
    python test_scene_switch.py --only 2,3  # 只跑情景 2 和 3
    python test_scene_switch.py --reuse-cache   # 用项目持久缓存（默认用全新临时缓存）
    python test_scene_switch.py --list      # 列出所有情景

默认每次都用**全新的临时缓存目录**，这样"冷（首次到达某点位）"的切换延迟才是真的冷；
想验证预热效果就加 `--reuse-cache`。

关于后端：dryrun 只空等、不出声，时序仍按真实音频时长走，所以数字有参考价值；
但 Windows 的 winsound 是**非流式**（必须收完整句才播），
所以"切换延迟"量到的其实是**网络首字到达**，实际出声还要再等一小段。
树莓派上 aplay 是真流式，那时这两个时刻基本重合。
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:                       # noqa: BLE001
    pass

import waitan                                                    # noqa: E402
from tts import Config, TTS, split_sentences                     # noqa: E402

W = 74


# ------------------------------------------------------------------ 小工具

def load_env_file() -> None:
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


def banner(title: str) -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    print("=" * W)


def fmt_s(x) -> str:
    return "N/A" if x is None else f"{x:.2f}s"


def wrap_cn(text: str, width: int = 32):
    return [text[i:i + width] for i in range(0, len(text), width)]


def show_target(name: str, brief: bool = True) -> None:
    print(f"  目标：{name}（{waitan.short_of(name)}）")
    if brief:
        return
    for line in wrap_cn(waitan.text_of(name)):
        print("    " + line)


def wait_first_audio(engine: TTS, t_ref: float, timeout: float):
    """轮询到"新目标第一个字出声"为止，返回相对 t_ref 的秒数。"""
    while time.time() - t_ref < timeout:
        first = (engine.last_metrics or {}).get("first_audio_at")
        if first is not None:
            return time.time() - t_ref
        time.sleep(0.005)
    return None


def switch_to(engine: TTS, name: str, poll_timeout: float = 14.0) -> dict:
    """模拟一次"目标变更"：停掉当前播报 → 立刻起新目标。

    这就是现场最核心的那个动作，也是本脚本测的主指标。
    注意区分"冷/热"：
      冷 = 这个目标还没合成过，切换要真发网络请求（现场第一次走到该点位）
      热 = 命中音频缓存，切换只是本地读盘（走回头路，或演示前预热过）
    两者的延迟差一个量级，混在一起统计会给人错误印象。
    """
    text = waitan.text_of(name)
    warm = False
    try:
        first_sent = split_sentences(text)[0]
        warm = engine._cache_get(engine._cache_key(first_sent)) is not None
    except Exception:                                    # noqa: BLE001
        pass

    old = engine.last_metrics or {}
    t0 = time.time()

    engine.stop()
    stop_ms = (time.time() - t0) * 1000.0
    old_stopped = bool(old.get("stopped"))
    old_chars = old.get("chars")

    try:
        t_call = time.time()
        engine.speak(text, wait=False)
        speak_call_ms = (time.time() - t_call) * 1000.0
    except Exception as exc:                             # noqa: BLE001
        return {"target": name, "error": f"{type(exc).__name__}: {exc}"}

    latency = wait_first_audio(engine, t0, poll_timeout)
    m = engine.last_metrics or {}
    return {
        "target": name,
        "warm": warm,
        "chars": len(text),
        "stop_ms": stop_ms,
        "speak_call_ms": speak_call_ms,
        "old_stopped": old_stopped,
        "old_chars": old_chars,
        "latency": latency,
        "active_chars": m.get("chars"),
        "degraded": m.get("degraded"),
        "failed": m.get("failed"),
        "engines": dict(m.get("engines") or {}),
    }


def report_switch(idx: int, r: dict) -> None:
    if r.get("error"):
        print(f"    [{idx}] → {r['target']}  ✗ 抛异常：{r['error']}")
        return
    tag = "✓" if r["latency"] is not None else "✗ 没出声"
    temp = "热" if r.get("warm") else "冷"
    old = r.get("old_chars")
    old_note = f"  上一段 {old} 字已停={'是' if r['old_stopped'] else '否'}" if old else ""
    print(f"    [{idx}] → {r['target']:<8} {tag} [{temp}]  "
          f"切换延迟={fmt_s(r['latency'])}  stop={r['stop_ms']:.0f}ms  "
          f"speak()阻塞={r['speak_call_ms']:.0f}ms  新目标 {r['active_chars']} 字{old_note}")


# ------------------------------------------------------------------ 情景

def scenario_sequential(engine: TTS) -> dict:
    """情景 1：正常游览节奏 —— 上一段讲完，再讲下一段。"""
    banner("情景 1 · 自然更替（上一段讲完，接下一段）")
    rows = []
    for i, name in enumerate(waitan.DEMO_SEQUENCE[:2], 1):
        print()
        show_target(name)
        t0 = time.time()
        ok = engine.speak(waitan.text_of(name), wait=True)
        wall = time.time() - t0
        m = engine.last_metrics or {}
        rows.append({"name": name, "ok": ok, "wall": wall,
                     "first": m.get("first_audio_at"),
                     "degraded": m.get("degraded"),
                     "failed": m.get("failed")})
        print(f"      {'✓ 讲完' if ok else '✗ 未完成'}  {m.get('chars')} 字 / "
              f"{m.get('sentences')} 句  首字={fmt_s(m.get('first_audio_at'))}  "
              f"墙钟={wall:.2f}s  引擎={m.get('engines')}  "
              f"降级={m.get('degraded')} 失败句={m.get('failed')}")
    return {"rows": rows}


def scenario_interrupt(engine: TTS) -> dict:
    """情景 2：核心 —— 讲了一半，游客走到下一个点位，目标突变。"""
    banner("情景 2 · 中途打断（走到下一个点位，讲解目标突变）")
    chain = waitan.DEMO_SEQUENCE
    plan = [(chain[0], 4.0, chain[1]), (chain[1], 5.0, chain[2])]
    rows = []
    for idx, (from_name, at, to_name) in enumerate(plan, 1):
        print()
        print(f"  ▸ {from_name} 播到第 {at:.0f} 秒 —— 游客已走到「{to_name}」")
        engine.stop()
        engine.speak(waitan.text_of(from_name), wait=False)
        time.sleep(at)
        r = switch_to(engine, to_name)
        rows.append(r)
        report_switch(idx, r)
    return {"rows": rows}


def scenario_jitter(engine: TTS) -> dict:
    """情景 3：快速连切 —— 识别抖动 / 游客快速转身，3 秒内换 3 个目标。"""
    banner("情景 3 · 连续抖动（3 秒内连切 3 次，看会不会串台）")
    names = waitan.names()
    chain = [names[0], names[1], names[2], names[6]]
    print(f"  路线：{' → '.join(chain)}   （每次间隔 1.2 秒，都在播放中打断）")

    engine.stop()
    engine.speak(waitan.text_of(chain[0]), wait=False)
    rows = []
    for idx, name in enumerate(chain[1:], 1):
        time.sleep(1.2)
        r = switch_to(engine, name)
        rows.append(r)
        report_switch(idx, r)

    # 串台检查：现在活跃的那条流水线，必须就是最后一个目标
    final = chain[-1]
    m = engine.last_metrics or {}
    expect_chars = len(waitan.text_of(final))
    expect_sents = len(split_sentences(waitan.text_of(final)))
    got_chars = m.get("chars")
    got_sents = m.get("sentences")
    ok_chars = got_chars == expect_chars
    ok_sents = got_sents == expect_sents
    ok_engine = sum((m.get("engines") or {}).values()) <= expect_sents

    print()
    print("  ── 串台检查（活跃流水线是否就是最新目标）──")
    print(f"     最新目标      : {final}")
    print(f"     字数          : 期望 {expect_chars} / 实际 {got_chars}   "
          f"{'✓' if ok_chars else '✗ 串台了'}")
    print(f"     句数          : 期望 {expect_sents} / 实际 {got_sents}   "
          f"{'✓' if ok_sents else '✗'}")
    print(f"     引擎计数不超句数: {'✓' if ok_engine else '✗ 有堆积'}  {m.get('engines')}")

    engine.stop()
    t0 = time.time()
    while engine.is_speaking() and time.time() - t0 < 3.0:
        time.sleep(0.02)
    calm = time.time() - t0
    print(f"     连切后能停下来  : {'✓' if not engine.is_speaking() else '✗ 卡住了'} "
          f"（{calm * 1000:.0f} ms 内 is_speaking 归 False）")
    return {"rows": rows, "no_crosstalk": ok_chars and ok_sents and ok_engine,
            "calm_ms": calm * 1000}


def scenario_revisit(engine: TTS, first_latency) -> dict:
    """情景 4：走回头路 —— 切回刚才讲过的目标，缓存已热，首字应该更快。"""
    banner("情景 4 · 切回讲过的目标（走回头路，缓存能否加速）")
    name = waitan.DEMO_SEQUENCE[0]
    print(f"  切回「{name}」，它在前面的情景里已经合成过，缓存应当是热的。")
    engine.stop()
    r = switch_to(engine, name)
    report_switch(1, r)
    hit = r["engines"].get("cache", 0)
    print()
    print(f"     缓存命中句数  : {hit}  "
          f"{'✓ 走了缓存' if hit else '✗ 没命中（缓存被关了？）'}")
    if first_latency and r["latency"]:
        print(f"     首见延迟 → 复访延迟: {fmt_s(first_latency)} → {fmt_s(r['latency'])}")
    if hit and r["latency"] is not None:
        print(f"     （缓存命中的句子不发网络请求，首字几乎是本地读取速度）")
    return {"row": r, "cache_hits": hit}


def scenario_cache_poison(engine: TTS) -> dict:
    """情景 5：被打断的半句，会不会把截断的音频写进缓存？

    这条最要命：预热缓存是为了让"走回头路"秒出，一旦缓存里混进半句，
    再命中就会播出被砍断的讲解。所以必须单独验证。
    注意要**逐句**看 —— 前面几句可能已经合成完，真正被打断的是在途那一句。
    """
    banner("情景 5 · 被打断的半句会不会写坏缓存（缓存正确性的关键）")
    name = "海关大楼"
    text = waitan.text_of(name)
    sents = split_sentences(text)
    keys = [engine._cache_key(s) for s in sents]

    def clear_cache() -> None:
        try:
            for fn in os.listdir(engine.cfg.cache_dir):
                for k in keys:
                    if fn.startswith(k + "."):
                        os.remove(os.path.join(engine.cfg.cache_dir, fn))
        except OSError:
            pass

    # ① 基准：走**与流水线相同的流式接口**，量出每句的完整时长
    #    （用非流式接口当基准会和流水线有细微出入，比不出真假）
    print(f"  ① 基准时长（流式接口，与流水线同路径，共 {len(sents)} 句）")
    baseline = []
    for s in sents:
        try:
            pcm = b"".join(engine.cloud.synthesize_stream(s))
            baseline.append(len(pcm) / 2 / engine.cloud.sample_rate)
        except Exception as exc:                           # noqa: BLE001
            baseline.append(None)
            print(f"     ✗ 基准失败：{type(exc).__name__}: {exc}")

    # ② 清掉缓存，讲到 2.5 秒就打断
    clear_cache()
    engine.stop()
    engine.speak(text, wait=False)
    time.sleep(2.5)
    engine.stop()
    time.sleep(0.3)

    # ③ 逐句比对缓存里的音频时长
    print()
    print(f"  {'句':<3}{'字数':>5}{'完整':>9}{'缓存':>9}   判定")
    print("  " + "-" * 52)
    truncated = []
    cached_n = 0
    for i, (s, base, k) in enumerate(zip(sents, baseline, keys), 1):
        got = engine._cache_get(k)
        if got is None:
            print(f"  {i:<3}{len(s):>5}{fmt_s(base):>9}{'—':>9}   未缓存（没讲到，正常）")
            continue
        cached_n += 1
        sec = len(got[0]) / 2 / got[1]
        # 阈值放到 80%：同一句云端两次合成本身就有约 10% 的长度抖动，
        # 太紧会误报。真被打断的截断通常短得很明显（实测 2.08s → 0.80s）。
        if base and sec < base * 0.80:
            truncated.append((i, base, sec))
            verdict = f"✗ 被截断（短了 {(1 - sec / base) * 100:.0f}%）"
        else:
            verdict = "✓ 完整"
        print(f"  {i:<3}{len(s):>5}{fmt_s(base):>9}{fmt_s(sec):>9}   {verdict}")

    print()
    bad = bool(truncated)
    if bad:
        print(f"     结论：✗ **缓存被污染** —— 有 {len(truncated)} 句存成了截断音频。")
        print("           影响：命中缓存时这句会播出半截；走回头路也会被砍断。")
    else:
        print("     结论：✓ 被打断的句子没有写进缓存，缓存是干净的。")

    clear_cache()                     # 别污染后面的情景
    return {"poisoned": bad, "cached": cached_n, "truncated": len(truncated)}


def scenario_cloud_down(engine: TTS) -> dict:
    """情景 6：切换目标时云端正好挂了 —— 会不会卡住整条流程。"""
    banner("情景 6 · 切换目标时云端挂了（容错不能卡住流程）")
    saved_key = os.environ.get("MIMO_API_KEY", "")
    saved_cache = os.environ.get("TTS_CACHE", "1")
    os.environ["MIMO_API_KEY"] = "sk-invalid-scene-switch-probe"
    os.environ["TTS_CACHE"] = "0"          # 必须真发请求，不能命中缓存蒙混过关
    bad = TTS(Config())
    try:
        print(f"  云端可用（按 Key 非空判断）: {bad.cloud.available()}")
        print(f"  缓存: 关（强制真发请求）")
        name = "外滩观景平台"
        print()
        print(f"  ▸ 当前在讲「陆家嘴天际线」，目标变为「{name}」，但 Key 已失效")
        bad.speak(waitan.text_of("陆家嘴天际线"), wait=False)
        time.sleep(0.8)
        t0 = time.time()
        bad.stop()
        bad.speak(waitan.text_of(name), wait=True)
        wall = time.time() - t0
        m = bad.last_metrics or {}
        print(f"    切换动作总耗时（含等待）: {wall:.2f}s")
        print(f"    句数={m.get('sentences')} 失败句={m.get('failed')} "
              f"降级={m.get('degraded')} 引擎={m.get('engines')}")
        bounded = wall < 12.0
        print()
        print(f"    结论：{'✓ 快速放弃，没有卡住流程' if bounded else '✗ 卡住了，超时太久'}")
        print("          （云端失败不再降级到本地引擎，而是跳过剩余句子；"
              "连续 2 次失败即放弃）")
        return {"wall": wall, "bounded": bounded, "failed": m.get("failed")}
    finally:
        bad.close()
        os.environ["MIMO_API_KEY"] = saved_key
        os.environ["TTS_CACHE"] = saved_cache


# ------------------------------------------------------------------ 主流程

SCENARIOS = [
    (1, "自然更替（讲完再接）", scenario_sequential),
    (2, "中途打断（核心）", scenario_interrupt),
    (3, "连续抖动（防串台）", scenario_jitter),
    (4, "切回讲过的目标（缓存）", scenario_revisit),
    (5, "被打断的半句会不会写坏缓存", scenario_cache_poison),
    (6, "切换时云端挂了（容错）", scenario_cloud_down),
]


def build_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="外滩场景转换情景测试")
    ap.add_argument("--speak", action="store_true", help="真的出声（默认静音 dryrun）")
    ap.add_argument("--only", type=str, default="",
                    help="只跑指定情景，逗号分隔，如 2,3")
    ap.add_argument("--reuse-cache", action="store_true",
                    help="用项目里的持久缓存（默认每次用全新临时缓存，保证冷启动数据真实）")
    ap.add_argument("--list", action="store_true", help="列出所有情景")
    return ap.parse_args()


def main() -> int:
    args = build_args()
    if args.list:
        for n, title, _ in SCENARIOS:
            print(f"  {n}  {title}")
        return 0

    load_env_file()
    if not os.environ.get("MIMO_API_KEY"):
        print("  没找到 MIMO_API_KEY（tts/.env 里应该有）")
        return 1

    # dryrun：不出声但按真实时长走，时序数字有参考价值
    os.environ["TTS_PLAYER"] = "auto" if args.speak else "dryrun"
    os.environ.pop("TTS_FORCE_ENGINE", None)
    os.environ.setdefault("TTS_CACHE", "1")

    # 默认换一个全新的缓存目录：这样"冷（首次到达该点位）"的数据才是真的冷。
    # 用临时目录而不是删项目缓存——不破坏开发机上已有的预热结果。
    if not args.reuse_cache:
        import tempfile
        os.environ["TTS_CACHE_DIR"] = tempfile.mkdtemp(prefix="tts_scene_cache_")

    engine = TTS(Config())
    print("=" * W)
    print("  外滩 · 场景转换（讲解目标切换）情景测试")
    print("=" * W)
    print(f"  播放后端   : {engine.player.backend}"
          f"{'（静音，但按真实时长走）' if not args.speak else '（真实出声）'}")
    print(f"  流式播放   : {'是' if engine.player.streaming else '否（须收整句才播）'}")
    print(f"  音频缓存   : {'开' if engine.cfg.cache_enabled else '关'}"
          f"{'' if args.reuse_cache else '（全新临时目录）'}")
    print(f"  缓存目录   : {engine.cfg.cache_dir}")
    print(f"  云端可用   : {engine.cloud.available()}")
    print(f"  讲解目标   : {len(waitan.names())} 个")
    engine.warmup()

    want = []
    if args.only:
        for part in str(args.only).replace("，", ",").split(","):
            part = part.strip()
            if part.isdigit():
                want.append(int(part))
    picked = [s for s in SCENARIOS if not want or s[0] in want]
    if not picked:
        print(f"  没有第 {args.only} 个情景")
        engine.close()
        return 1

    out: dict = {}
    try:
        for n, title, fn in picked:
            try:
                # 情景 4 需要拿到情景 2 里"首见延迟"做对比
                if n == 4:
                    first_lat = None
                    rows = (out.get(2) or {}).get("rows") or []
                    for r in rows:
                        if r.get("target") == waitan.DEMO_SEQUENCE[0]:
                            first_lat = r.get("latency")
                    out[n] = fn(engine, first_lat)
                else:
                    out[n] = fn(engine)
            except KeyboardInterrupt:
                print("\n  （被你中断）")
                break
            except Exception as exc:                       # noqa: BLE001
                print(f"\n  ✗ 情景 {n} 抛异常：{type(exc).__name__}: {exc}")
                out[n] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        engine.stop()
        engine.close()

    # ---- 汇总 ----
    banner("汇总")
    switch_rows = [r for n in (2, 3, 4)
                   for r in (out.get(n) or {}).get("rows") or []
                   if r.get("latency") is not None]
    cold = [r["latency"] for r in switch_rows if not r.get("warm")]
    warm = [r["latency"] for r in switch_rows if r.get("warm")]
    print(f"  切换次数                        : {len(switch_rows)} 次"
          f"（冷 {len(cold)} / 热 {len(warm)}）")
    if cold:
        print(f"  切换延迟 · 冷（要真发网络请求）  : 中位 {statistics.median(cold):.2f}s  "
              f"最快 {min(cold):.2f}s  最慢 {max(cold):.2f}s")
    if warm:
        print(f"  切换延迟 · 热（命中缓存，读盘）  : 中位 {statistics.median(warm):.2f}s  "
              f"最快 {min(warm):.2f}s  最慢 {max(warm):.2f}s")
    if cold:
        print("        ↑ 冷的那行才是「第一次走到某点位」的真实手感；热的是走回头路。")
    stops = [r["stop_ms"] for n in (2, 3) for r in (out.get(n) or {}).get("rows") or []
             if "stop_ms" in r]
    if stops:
        print(f"  stop() 耗时                     : 中位 {statistics.median(stops):.0f}ms  "
              f"最大 {max(stops):.0f}ms")
    jit = out.get(3) or {}
    if "no_crosstalk" in jit:
        print(f"  快速连切是否串台                : "
              f"{'✓ 无串台' if jit['no_crosstalk'] else '✗ 有串台/堆积'}")
    if 4 in out and isinstance(out[4], dict) and "cache_hits" in out[4]:
        print(f"  复访缓存命中                    : {out[4]['cache_hits']} 句")
    if 5 in out and isinstance(out[5], dict) and "poisoned" in out[5]:
        print(f"  被打断的半句写坏缓存            : "
              f"{'✗ 是（需修）' if out[5]['poisoned'] else '✓ 否'}")
    if 6 in out and isinstance(out[6], dict) and "bounded" in out[6]:
        print(f"  云端挂掉是否卡住流程            : "
              f"{'✓ 不卡' if out[6]['bounded'] else '✗ 卡住'}")
    errors = {n: v for n, v in out.items() if isinstance(v, dict) and "error" in v}
    print(f"  抛异常的情景                    : {list(errors) or '无'}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
