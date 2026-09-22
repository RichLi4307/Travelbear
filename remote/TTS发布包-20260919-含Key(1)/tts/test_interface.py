# -*- coding: utf-8 -*-
"""契约接口自测：验证 tts/tts_interface.py 符合《模块接口契约 v1.0》。

全离线：mock 合成器（本地正弦音）+ dryrun 播放后端（按真实时长空等、不出声）。
所以它跑的是**真实时序**（音频多长就等多久），但不发任何网络请求、不发声。

用法：
    python test_interface.py
    python test_interface.py --online    # 改用真实云端合成（需要 .env 里的 Key）
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ------------------------------------------------------------------ 环境
# 必须在 import tts 之前设好
os.environ.setdefault("TTS_PLAYER", "dryrun")        # 按真实时长走，不出声
os.environ.setdefault("TTS_FORCE_ENGINE", "mock")    # 本地正弦音，零网络
os.environ.setdefault("TTS_CACHE", "0")              # 关缓存，每次都是真合成

logging.getLogger("tts").setLevel(logging.WARNING)

import tts as engine_mod                             # noqa: E402
from tts_interface import (                          # noqa: E402
    PLAY_STATUS_SOURCE, TTSAdapter, TTSInterface, get_tts,
)

BAR = "=" * 64
SHORT = "你好，我是你的导游小熊。"
LONG = ("欢迎来到外滩。"
        "这里是上海最著名的滨江步道，对岸就是陆家嘴。"
        "前面那栋绿色穹顶的建筑是和平饭店。")

_results: list = []
BASELINE: dict = {}          # 同文本的实测基准耗时，供后面做对比


def banner(text: str) -> None:
    print()
    print(BAR)
    print("  " + text)
    print(BAR)


def check(name: str, cond: bool, detail: str = "") -> bool:
    _results.append((name, bool(cond)))
    print("   %s %-46s %s" % ("[OK]" if cond else "[!!]", name, detail))
    return bool(cond)


def fmt(v) -> str:
    return "%.2fs" % v if isinstance(v, (int, float)) else str(v)


# ------------------------------------------------------------------ 情景

async def c1_contract() -> None:
    banner("情景 1 · 契约形状（文件名/基类/方法签名/返回字段）")
    print("   PlayStatus 来源：%s" % PLAY_STATUS_SOURCE)

    sig = inspect.signature(TTSInterface.speak)
    params = list(sig.parameters)
    check("TTSInterface.speak 是 async",
          inspect.iscoroutinefunction(TTSInterface.speak))
    check("参数顺序为 (self, text, interrupt)",
          params == ["self", "text", "interrupt"], str(params))
    check("interrupt 默认值为 False",
          sig.parameters["interrupt"].default is False)
    check("TTSAdapter 是 TTSInterface 的子类",
          issubclass(TTSAdapter, TTSInterface))

    # 抽象类不许被直接实例化
    try:
        TTSInterface()                                # type: ignore[abstract]
        check("TTSInterface 不能直接实例化", False, "居然成功了")
    except TypeError:
        check("TTSInterface 不能直接实例化", True)


async def c2_empty(adapter: TTSAdapter) -> None:
    banner("情景 2 · 空文本 / 纯标点：应返回成功且不做事")
    for label, text in (("空字符串", ""), ("纯空格", "   "), ("纯标点", "。。。！？")):
        st = await adapter.speak(text)
        check("%s → success=True" % label, st.success is True,
              "error_msg=%r" % (st.error_msg,))


async def c3_normal(adapter: TTSAdapter) -> None:
    banner("情景 3 · 正常播报：await 到播完才返回")
    t0 = time.time()
    st = await adapter.speak(SHORT)
    dt = time.time() - t0
    m = adapter.engine.last_metrics or {}
    BASELINE["SHORT"] = dt                # 后面用它判断"有没有多播了别人的音频"
    print("   耗时 %.2fs   引擎=%s" % (dt, m.get("engines")))
    check("success=True", st.success is True, "error_msg=%r" % (st.error_msg,))
    check("error_msg 为空", st.error_msg == "")
    check("返回时已不在播放（阻塞语义）",
          st.is_playing is False and adapter.is_playing() is False)
    check("确实等到了音频播完（耗时 > 0.5s）", dt > 0.5, fmt(dt))


async def c4_interrupt(adapter: TTSAdapter) -> None:
    banner("情景 4 · interrupt=True：抢占当前播报")
    a_start = time.time()
    task_a = asyncio.ensure_future(adapter.speak(LONG))

    # 等 A 真正开始出声
    while not adapter.is_playing() and time.time() - a_start < 3:
        await asyncio.sleep(0.02)

    await asyncio.sleep(0.5)                          # 播一小会儿
    b_start = time.time()
    st_b = await adapter.speak(SHORT, interrupt=True)
    dt_b = time.time() - b_start
    st_a = await task_a

    base = BASELINE.get("SHORT") or 0.0
    print("   A（长文本，被打断）: success=%s  error_msg=%r"
          % (st_a.success, st_a.error_msg))
    print("   B（短文本，抢占）  : success=%s  耗时 %.2fs  "
          "（同一句话的基准耗时 %.2fs，见情景 3）" % (st_b.success, dt_b, base))
    check("A 返回 success=False", st_a.success is False)
    check("A 的 error_msg 说明被打断", "打断" in (st_a.error_msg or ""),
          repr(st_a.error_msg))
    check("B 返回 success=True", st_b.success is True,
          "error_msg=%r" % (st_b.error_msg,))
    # 这一条查的是"串台"：被打断的 A，它的生产线程可能还卡在网络读里，
    # 醒来后把 A 剩下的句子推进 B 的队列 —— 表现为 B 播了远超自己长度的音频。
    # 所以判据不是绝对秒数，而是"与同一句话的独立基准是否接近"。
    check("B 用时 ≈ 同文本基准（没有把 A 的残余音频也播出来）",
          dt_b <= base * 1.4 + 0.5, "%.2fs vs 基准 %.2fs" % (dt_b, base))
    check("两条都返回了，没有协程卡死（A 用时 %.1fs）" % (time.time() - a_start),
          not task_a.cancelled())


async def c5_queue(adapter: TTSAdapter) -> None:
    banner("情景 5 · interrupt=False：排队，等前一条播完再播")
    t_call_a = time.time()
    task_a = asyncio.ensure_future(adapter.speak(LONG))
    await asyncio.sleep(0.3)

    m_a = adapter.engine.last_metrics                  # A 的 metrics 对象
    t_call_b = time.time()
    task_b = asyncio.ensure_future(adapter.speak(SHORT, interrupt=False))

    # B 什么时候才真正起步 = last_metrics 被换成新对象的那一刻
    t_b_real = None
    while time.time() - t_call_b < 15:
        if adapter.engine.last_metrics is not m_a:
            t_b_real = time.time()
            break
        await asyncio.sleep(0.02)

    st_a, st_b = await task_a, await task_b
    wait_for_a = (t_b_real - t_call_b) if t_b_real else None

    print("   A（长文本）: success=%s" % st_a.success)
    print("   B 调用后被压了 %s 才真正起步（A 还没播完）"
          % (fmt(wait_for_a) if wait_for_a is not None else "无法判定"))
    check("A success=True", st_a.success is True,
          "error_msg=%r" % (st_a.error_msg,))
    check("B success=True", st_b.success is True,
          "error_msg=%r" % (st_b.error_msg,))
    check("B 确实等了 A（等待 > 1.5s）",
          wait_for_a is not None and wait_for_a > 1.5,
          fmt(wait_for_a) if wait_for_a is not None else "-")
    check("B 不是被 A 拖死（总时长 %.1fs 合理）" % (time.time() - t_call_a),
          st_a.success and st_b.success)


async def c6_cancel(adapter: TTSAdapter) -> None:
    banner("情景 6 · 外层超时（wait_for）取消：音频必须真的停")
    t0 = time.time()
    timed_out = False
    st = None
    try:
        st = await asyncio.wait_for(adapter.speak(LONG), timeout=0.6)
    except asyncio.TimeoutError:
        timed_out = True
    t_timeout = time.time() - t0

    await asyncio.sleep(0.4)
    still = adapter.is_playing()
    print("   超时于 %.2fs 抛出；再等 0.4s 后 is_playing=%s" % (t_timeout, still))
    if not timed_out:
        print("   （没有超时，说明 speak 提前返回了：success=%s error_msg=%r）"
              % (getattr(st, "success", None), getattr(st, "error_msg", None)))
    check("wait_for 能正常超时（CancelledError 被透传）", timed_out)
    check("超时后音频已停止（不会「Agent 放弃了熊还在说」）", still is False)
    check("超时后引擎可继续使用", await _speak_ok(adapter))


async def _speak_ok(adapter: TTSAdapter) -> bool:
    st = await adapter.speak(SHORT)
    return st.success is True


async def c7_engine_raises(adapter: TTSAdapter) -> None:
    banner("情景 7 · 引擎抛异常：必须封装进 error_msg，不许外抛")

    class Boom:
        last_metrics = None

        def speak(self, text, wait=True):
            raise RuntimeError("模拟引擎内部炸了")

        def is_speaking(self):
            return False

        def stop(self):
            pass

    bad = TTSAdapter(engine=Boom())                   # type: ignore[arg-type]
    try:
        st = await bad.speak("这句话会触发引擎异常。")
        check("没有向外抛异常", True)
        check("success=False", st.success is False)
        check("error_msg 带上了原因", "模拟引擎内部炸了" in (st.error_msg or ""),
              repr(st.error_msg))
    except Exception as exc:                          # noqa: BLE001
        check("没有向外抛异常", False, "%s: %s" % (type(exc).__name__, exc))


async def c8_singleton(adapter: TTSAdapter) -> None:
    banner("情景 8 · 单例入口")
    a, b = get_tts(), get_tts()
    check("get_tts() 返回同一实例", a is b)
    check("单例与显式构造的适配器同类", type(a) is type(adapter))


# ------------------------------------------------------------------ 主流程

async def main_async(online: bool) -> int:
    if online:
        os.environ["TTS_FORCE_ENGINE"] = "cloud"
        print("  [在线模式] 用真实云端合成，需要 .env 里的 MIMO_API_KEY")

    banner("契约接口自测  tts/tts_interface.py")
    print("   播放后端 : %s（dryrun = 按真实时长空等，不出声）" % os.environ["TTS_PLAYER"])
    print("   合成引擎 : %s" % os.environ["TTS_FORCE_ENGINE"])

    adapter = TTSAdapter()
    try:
        await c1_contract()
        await c2_empty(adapter)
        await c3_normal(adapter)
        await c4_interrupt(adapter)
        await c5_queue(adapter)
        await c6_cancel(adapter)
        await c7_engine_raises(adapter)
        await c8_singleton(adapter)
    finally:
        await adapter.aclose()

    banner("汇总")
    passed = sum(1 for _n, ok in _results if ok)
    total = len(_results)
    for name, ok in _results:
        if not ok:
            print("   [失败] %s" % name)
    print("   %d/%d 项通过" % (passed, total))
    print("   %s" % ("全部通过" if passed == total else "有失败项，见上面"))
    return 0 if passed == total else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="TTS 契约接口自测")
    ap.add_argument("--online", action="store_true", help="用真实云端合成")
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args.online))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
