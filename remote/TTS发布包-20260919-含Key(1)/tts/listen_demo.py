# -*- coding: utf-8 -*-
"""导游小熊 · 播报试听

目的：让人**实际听到**效果，而不是看日志里的数字。

用法：
    双击项目根目录的「试听.bat」
    或在 tts/ 下执行  python listen_demo.py

语音走真实云端链路（MiMo TTS）。Key 从 tts/.env 读，没有会提示粘贴。
本文件不含任何凭据，可以进仓库。
"""

from __future__ import annotations

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:                                   # 只兜底编码异常，不改终端编码本身
    sys.stdout.reconfigure(errors="replace")
except Exception:                      # noqa: BLE001
    pass

import waitan                                                          # noqa: E402
from tts import PRESET_VOICES, Config, TTS, _wav_header, split_sentences  # noqa: E402

SEC_PER_CHAR = 0.196                   # 实测拟合：音频秒数 ≈ 0.196 × 字数
OUTDIR = os.path.join(HERE, "试听导出")


# ------------------------------------------------------------------ 演示文本
# 地点：上海外滩。文案统一放在 waitan.py 里，换地点只改那一个文件。

T_SHORT = waitan.OPENING                       # 开场白
T_STANDARD = waitan.text_of("外白渡桥")         # 标准讲解
T_LONG = waitan.text_of("汇丰银行大楼")         # 长讲解


# ------------------------------------------------------------------ 小工具

def load_env_file() -> None:
    """bat 里可能没读到，这里自己补一次（已存在的环境变量不覆盖）。"""
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())
    except OSError:
        pass


def wrap_cn(text: str, width: int = 28) -> list:
    return [text[i:i + width] for i in range(0, len(text), width)]


def pause(prompt: str = "  按回车继续… ") -> None:
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()


def show_text(text: str) -> None:
    for line in wrap_cn(text):
        print("    " + line)


# ------------------------------------------------------------------ 动作

def play(engine: TTS, title: str, text: str, ask: bool = True) -> bool:
    """播一段，并汇报它到底花了多久、有没有降级。"""
    print()
    print("-" * 58)
    print(f"  {title}")
    print("-" * 58)
    print(f"  {len(text)} 字 · 预计音频 {len(text) * SEC_PER_CHAR:.0f} 秒")
    print()
    show_text(text)
    print()
    if ask:
        try:
            input("  按回车开始播报… ")
        except (EOFError, KeyboardInterrupt):
            print()
            return False
    t0 = time.time()
    try:
        ok = engine.speak(text, wait=True)
    except KeyboardInterrupt:
        engine.stop()
        print("\n  （被你中断了）")
        return False
    print()
    print(f"  {'✓ 出声完毕' if ok else '✗ 没出声，检查网络 / API Key'}  "
          f"墙钟 {time.time() - t0:.1f} 秒")
    print(f"  {engine.summary()}")
    return ok


def voice_tour(engine: TTS) -> None:
    """9 个音色念同一句，方便盲测投票。"""
    original = engine.cfg.voice
    print()
    print("-" * 58)
    print("  音色对比")
    print("-" * 58)
    print(f"  {len(PRESET_VOICES)} 个音色依次念同一句：")
    print(f"  「{T_SHORT}」")
    print()
    pause("  按回车开始（全程约 1 分钟）… ")
    for i, voice in enumerate(PRESET_VOICES, 1):
        tag = "  ← 当前选定" if voice == original else ""
        print(f"\n  [{i}/{len(PRESET_VOICES)}] {voice}{tag} " + "-" * 6)
        engine.set_voice(voice)
        engine.speak(T_SHORT, wait=True)
        time.sleep(0.4)
    engine.set_voice(original)
    print()
    print("  听完了。要换就告诉我音色名，或改 tts/.env 里的 TTS_VOICE。")


def interrupt_demo(engine: TTS, at: float = 2.0) -> None:
    """播到一半按停，验证交互手感。"""
    print()
    print("-" * 58)
    print("  打断体验")
    print("-" * 58)
    print(f"  开始播报后 {at:.0f} 秒自动按停，注意听它是不是立刻闭嘴。")
    print()
    show_text(T_LONG)
    pause("\n  按回车开始… ")

    def _later() -> None:
        time.sleep(at)
        print(f"\n  >>> {at:.0f} 秒到，按停")
        engine.stop()

    threading.Thread(target=_later, daemon=True).start()
    t0 = time.time()
    engine.speak(T_LONG, wait=True)
    dt = time.time() - t0
    print(f"\n  实际只用了 {dt:.1f} 秒（这段音频本身约 "
          f"{len(T_LONG) * SEC_PER_CHAR:.0f} 秒）")
    print(f"  {engine.summary()}")


def _latency(engine: TTS, t_ref: float, timeout: float = 14.0):
    """等到"第一个字出声"，返回相对 t_ref 的秒数；等不到返回 None。"""
    while time.time() - t_ref < timeout:
        first = (engine.last_metrics or {}).get("first_audio_at")
        if first is not None:
            return time.time() - t_ref
        time.sleep(0.005)
    return None


def scene_switch_demo(engine: TTS, at: float = 7.0) -> None:
    """场景转换：边走边换讲解目标 —— 上一段被掐断，新一段立刻接上。

    这是现场最核心的动作：游客走到下一个点位，讲解目标就变了。
    """
    chain = waitan.DEMO_SEQUENCE
    print()
    print("-" * 58)
    print("  场景转换演示 · 外滩")
    print("-" * 58)
    print(f"  模拟游客沿外滩往前走，每个点位讲 {at:.0f} 秒就走到下一个：")
    for i, name in enumerate(chain, 1):
        lead = "  " if i == 1 else "→ "
        print(f"  {lead}{i}. {name}（{waitan.short_of(name)}）")
    print()
    print("  每个点位的讲解都很长，你会在它讲到一半时听到**突然切到下一个目标**——")
    print("  那就是现场真实的手感：不留拖音，也不把上一段剩下的念完。")
    pause(f"  按回车开始（全程约 {int(len(chain) * at + 10)} 秒）… ")

    try:
        engine.stop()
        for i, name in enumerate(chain, 1):
            if i == 1:
                print(f"\n  [1/{len(chain)}] {name} —— 起播")
                t0 = time.time()
                engine.speak(waitan.text_of(name), wait=False)
                lat = _latency(engine, t0)
                print(f"        首字 {lat:.2f}s" if lat else "        首字 N/A")
            else:
                t0 = time.time()
                engine.stop()
                stop_ms = (time.time() - t0) * 1000.0
                t1 = time.time()
                engine.speak(waitan.text_of(name), wait=False)
                lat = _latency(engine, t1)
                lat_txt = f"{lat:.2f}s" if lat else "N/A"
                print(f"\n  >>> 游客走到「{name}」，切换讲解目标")
                print(f"        切换延迟 {lat_txt}（stop 本身用了 {stop_ms:.0f}ms）")
            if i < len(chain):
                time.sleep(at)
        engine.stop()
    except KeyboardInterrupt:
        engine.stop()
        print("\n  （被你中断了）")
        return

    print()
    print("  听完了。切换干净吗？——正常应该是立刻闭嘴、马上开口，中间没有拖尾。")
    print("  想量化就再跑 tts/test_scene_switch.py，那里有 6 个情景的完整数据。")


def export_wav(engine: TTS) -> None:
    """把演示词导出成 wav，方便发群里让队友/评委听。"""
    print()
    print("-" * 58)
    print("  导出 wav")
    print("-" * 58)
    items = [
        ("01_开场短句", T_SHORT),
        ("02_标准讲解-外白渡桥", T_STANDARD),
        ("03_长讲解-汇丰银行大楼", T_LONG),
    ]
    print(f"  共 {len(items)} 段，导出到：")
    print(f"  {OUTDIR}")
    print()
    pause("  按回车开始… ")
    os.makedirs(OUTDIR, exist_ok=True)
    print()
    for name, text in items:
        pcm = b""
        sample_rate = None
        failed = False
        for sentence in split_sentences(text):
            try:
                part, sr = engine.cloud.synthesize(sentence)
            except Exception as exc:                   # noqa: BLE001
                print(f"  ✗ {name}: {exc}")
                failed = True
                break
            if sample_rate is None:
                sample_rate = sr
            pcm += part
        if failed or not pcm or not sample_rate:
            continue
        path = os.path.join(OUTDIR, name + ".wav")
        with open(path, "wb") as fh:
            fh.write(_wav_header(sample_rate, len(pcm)) + pcm)
        print(f"  ✓ {name}.wav   {len(pcm) / 1024 / 1024:.1f} MB   "
              f"{len(pcm) / 2 / sample_rate:.1f} 秒")
    print()
    print("  这几个 wav 直接发群里，手机也能播。")


# ------------------------------------------------------------------ 自检

def selftest(engine: TTS) -> int:
    """静音后端跑一遍所有代码路径，验证不会抛异常。"""
    print("== 自检（TTS_PLAYER=null，不出声，只验证代码路径）==")
    results = []
    for title, text in (("短句", T_SHORT), ("标准", T_STANDARD), ("长文", T_LONG)):
        results.append((title, play(engine, title, text, ask=False)))

    print("\n== 音色切换 ==")
    original = engine.cfg.voice
    for voice in PRESET_VOICES[:3]:
        engine.set_voice(voice)
        print(f"  {voice}: speak={engine.speak(T_SHORT, wait=True)}")
    engine.set_voice(original)

    print("\n== 打断 ==")
    engine.speak(T_LONG, wait=False)
    time.sleep(0.3)
    engine.stop()
    print(f"  打断后 is_speaking={engine.is_speaking()}")

    print("\n== 对照：无 Key 时的两种表现 ==")
    saved = os.environ.get("MIMO_API_KEY", "")
    os.environ["MIMO_API_KEY"] = ""
    bad = TTS(Config())
    print(f"  cloud.available = {bad.cloud.available()}")
    hit = bad.speak(T_SHORT, wait=True)
    miss = bad.speak("这段文本从未合成过，无凭据时应优雅失败而不是抛异常。", wait=True)
    print(f"  已缓存文本 : speak={hit}   <- 命中缓存，无 Key 也能出声（保底生效）")
    print(f"  未缓存文本 : speak={miss}  <- 应为 False，且不抛异常")
    bad.close()
    os.environ["MIMO_API_KEY"] = saved

    ok = all(r[1] for r in results)
    print("\n自检结果:", "通过" if ok else "有失败项")
    for title, good in results:
        print(f"  {title}: {'OK' if good else 'FAIL'}")
    return 0 if ok else 1


# ------------------------------------------------------------------ 主流程

MENU = """
==========================================================
  导游小熊 · 播报试听（外滩）
==========================================================
  1  标准讲解（外白渡桥）         ← 先听这个
  2  开场短句（打招呼）
  3  长讲解（汇丰银行大楼）
  4  音色对比：9 个音色念同一句
  5  打断体验：播到 2 秒按停
  6  场景转换：边走边换讲解目标    ← 现场核心动作
  7  导出 wav 文件（发群里用）
  8  自定义文本（粘贴任意文案）
  0  退出
"""


def main() -> int:
    selftest_mode = "--selftest" in sys.argv
    if selftest_mode:
        os.environ["TTS_PLAYER"] = "null"

    load_env_file()
    if not os.environ.get("MIMO_API_KEY"):
        print("  没找到 MIMO_API_KEY（tts/.env 里应该是有的）。")
        try:
            typed = input("  粘贴一个，或直接回车退出: ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
        if not typed:
            return 1
        os.environ["MIMO_API_KEY"] = typed

    engine = TTS(Config())
    engine.warmup()

    if selftest_mode:
        try:
            return selftest(engine)
        finally:
            engine.close()

    try:
        while True:
            print(MENU)
            print(f"  后端 {engine.player.backend} | 音色 {engine.cfg.voice} | "
                  f"云端 {'可用' if engine.cloud.available() else '不可用'}")
            print()
            try:
                choice = input("  选一个: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if choice == "0":
                break
            if choice == "1":
                play(engine, "标准讲解 · 外白渡桥", T_STANDARD)
            elif choice == "2":
                play(engine, "开场短句", T_SHORT)
            elif choice == "3":
                play(engine, "长讲解 · 汇丰银行大楼", T_LONG)
            elif choice == "4":
                voice_tour(engine)
            elif choice == "5":
                interrupt_demo(engine)
            elif choice == "6":
                scene_switch_demo(engine)
            elif choice == "7":
                export_wav(engine)
            elif choice == "8":
                try:
                    custom = input("  粘贴文案（一行）: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print()
                    continue
                if custom:
                    play(engine, "自定义文本", custom)
            else:
                continue
            pause("  按回车回菜单… ")
    finally:
        engine.close()
        print("  已退出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
