# -*- coding: utf-8 -*-
"""TTS 模块自测。不依赖其他任何模块，四种场景下都能跑：

    python test_tts.py                     # 联网，云端合成一句话并播放
    python test_tts.py --mock --dry-run    # 完全离线：不联网、不发声，只验证流水线逻辑
    python test_tts.py --long              # 300 字长文本，测首字延迟与句间衔接
    python test_tts.py --interrupt 3       # 播到第 3 秒自动调 stop()，验证 0.5s 内静音
    python test_tts.py --volume 40         # 音量
    python test_tts.py --voice 白桦         # 换音色
    python test_tts.py --bench             # 分句器与降级路径的批量自检
    python test_tts.py --list-voices       # 打印官方预置音色清单

没有 API Key、没有扬声器、没有网络时，用 --mock --dry-run 也能验证全部内部逻辑。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tts as tts_mod                      # noqa: E402
from tts import TTS, PRESET_VOICES, split_sentences, Config   # noqa: E402

SHORT = "你好，我是你的导游小熊。很高兴为你讲解。"

LONG = (
    "欢迎来到上海大学的樱花大道。每年三月中旬，道路两侧的日本晚樱会同时绽放，"
    "形成一条长约四百米的粉色长廊，是校园里最受欢迎的打卡地点之一。"
    "这条大道建于一九八五年，最初只种植了两排共计六十株樱花树，"
    "后来经过三次补种才形成今天的规模。樱花的花期很短，通常只有七到十天，"
    "如果遇到倒春寒或者连续降雨，观赏期还会进一步缩短。"
    "所以每年樱花盛开的那几天，这里总是挤满了拍照的师生和慕名而来的市民。"
    "沿着大道继续向前走，右手边那栋红砖建筑就是学校的老图书馆，"
    "它建于一九二四年，是上海市优秀历史建筑，现在已经改造成了校史陈列馆。"
)


def build_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="TTS 模块自测")
    ap.add_argument("--text", default=None, help="自定义播报文本")
    ap.add_argument("--voice", default=None, help=f"音色，可选: {', '.join(PRESET_VOICES)}")
    ap.add_argument("--volume", type=int, default=None, help="音量 0-100")
    ap.add_argument("--mock", action="store_true", help="用本地正弦音替代真实合成（不联网）")
    ap.add_argument("--dry-run", action="store_true", help="不真正发声，只跑流水线")
    ap.add_argument("--long", action="store_true", help="用 300 字长文本")
    ap.add_argument("--interrupt", type=float, default=None,
                    help="播到第 N 秒自动调 stop()，验证打断延迟")
    ap.add_argument("--no-wait", action="store_true", help="speak(wait=False) 异步模式")
    ap.add_argument("--bench", action="store_true", help="跑分句器与内部逻辑自检")
    ap.add_argument("--list-voices", action="store_true", help="打印预置音色清单")
    ap.add_argument("--voice-demo", action="store_true",
                    help="逐个试听所有预置音色（同一句文本，方便盲测投票）")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args()


def apply_env(args: argparse.Namespace) -> None:
    if args.mock:
        os.environ["TTS_FORCE_ENGINE"] = "mock"
    if args.dry_run:
        os.environ["TTS_PLAYER"] = "dryrun"


def bench() -> int:
    print("== 分句器自检 ==")
    cases = [
        ("短句", "你好。"),
        ("长句无标点", "这是一段没有任何标点的超长文本用来测试强制切分逻辑" * 4),
        ("混合标点", "你好！这里是樱花大道，建于1985年；每年三月开放。"),
        ("空串", "   "),
        ("软标点长句", "这是一句非常长的句子，中间用逗号分隔，用来测试软标点切分是否生效，"
                       "如果超过四十个字符就应该在逗号处断开，而不是一直等到句号出现，"
                       "这样可以把首字延迟压到一秒多。"),
        ("讲解词", LONG),
    ]
    ok = True
    for name, text in cases:
        parts = split_sentences(text)
        lens = [len(p) for p in parts]
        over = [n for n in lens if n > tts_mod._SENTENCE_HARD]
        print(f"  [{name}] {len(parts)} 句，长度分布 {lens}")
        if over:
            print(f"    !! 存在超长句 {over}")
            ok = False
        if name == "空串" and parts:
            print("    !! 空串应返回空列表")
            ok = False

    print("\n== 增益自检 ==")
    pcm = b"\x00\x00\xff\x7f\x00\x80\x01\x00"
    g0 = tts_mod._apply_gain(pcm, 1.0)
    g2 = tts_mod._apply_gain(pcm, 2.0)
    print(f"  gain=1.0 直通: {g0 == pcm}")
    print(f"  gain=2.0 限幅: {g2.hex()}")
    if g0 != pcm:
        ok = False

    print("\n== WAV 解析自检 ==")
    sr = 24000
    data = b"\x01\x02\x03\x04" * 10
    wav = tts_mod._wav_header(sr, len(data)) + data
    parsed, psr = tts_mod._parse_wav(wav)
    print(f"  解析成功={parsed == data} 采样率={psr}")
    if parsed != data or psr != sr:
        ok = False

    print("\n自检结果:", "通过" if ok else "存在失败项")
    return 0 if ok else 1


def main() -> int:
    args = build_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    if args.list_voices:
        print("官方预置音色（mimo-v2.5-tts）：")
        for v in PRESET_VOICES:
            print("  -", v)
        return 0

    if args.bench:
        return bench()

    apply_env(args)

    engine = TTS(Config())
    print(f"播放后端 : {engine.player.backend}"
          f"{'（dry-run，不发声）' if args.dry_run else ''}")
    print(f"强制引擎 : {os.environ.get('TTS_FORCE_ENGINE') or '自动'}")
    print(f"云端凭据 : {'已配置' if engine.cloud.available() else '未配置 MIMO_API_KEY'}")
    engine.warmup()

    if args.voice:
        engine.set_voice(args.voice)
    if args.volume is not None:
        engine.set_volume(args.volume)

    if args.voice_demo:
        demo_text = args.text or "欢迎来到上海大学的樱花大道，我是你的导游小熊。"
        print(f"\n音色盲测：{len(PRESET_VOICES)} 个音色依次念同一句话")
        print(f"文本: {demo_text}\n")
        for i, v in enumerate(PRESET_VOICES, 1):
            print(f"--- [{i}/{len(PRESET_VOICES)}] {v} " + "-" * 20)
            engine.set_voice(v)
            engine.speak(demo_text)
        print("\n试听完毕。把这段录音发群里投票，定了告诉我。")
        engine.close()
        return 0

    text = args.text or (LONG if args.long else SHORT)
    print(f"\n文本: {text[:40]}{'...' if len(text) > 40 else ''} （{len(text)} 字）")

    stopper = None
    if args.interrupt is not None:
        def _do_stop():
            time.sleep(args.interrupt)
            print(f"\n>>> {args.interrupt}s 到，调用 stop()")
            engine.stop()
        stopper = threading.Thread(target=_do_stop, daemon=True)
        stopper.start()

    t0 = time.time()
    if args.no_wait:
        ok = engine.speak(text, wait=False)
        print("speak(wait=False) 已返回:", ok)
        while engine.is_speaking():
            time.sleep(0.1)
    else:
        ok = engine.speak(text)

    if stopper:
        # 打断场景：额外验证 stop() 之后确实在 0.5s 内静音
        t_stop = time.time()
        while engine.is_speaking() and time.time() - t_stop < 1.0:
            time.sleep(0.02)
        quiet = time.time() - t_stop
        print(f"\nstop() 到静音耗时: {quiet*1000:.0f} ms "
              f"（要求 < 500 ms）: {'通过' if quiet < 0.5 else '不通过'}")

    print("\nspeak 返回:", ok)
    print("指标:", engine.summary())
    print(f"墙钟: {time.time() - t0:.2f}s")
    engine.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
