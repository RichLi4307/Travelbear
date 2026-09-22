# -*- coding: utf-8 -*-
"""演示前预热：把讲解文案全量合成为本地音频缓存，让现场播报不走网络、首字≈0。

定位（2026-09-19 修订，务必先读）
--------------------------------
**这是可选体验优化，不是"断网保底"承诺。** 收益有两条：
① 首字延迟从 0.8 s（中位）降到 ≈0（命中缓存不发起任何网络请求）；
② 抗 TTS 厂商 API 抖动/限流 —— 这条与"现场网络是否断开"无关，是最现实的风险。

为什么不叫保底：断网时 TTS 是**最后**受冲击的模块。链路是
`识图/定位（云端 VLM）→ LLM 生成文案 → TTS 合成` —— 前面任一环断了，
本模块手上根本没有文本可播，缓存里存着音频也不知道该播哪一条。
"整机断网保底"要三个模块**同时**做文本固化才成立，协同成本远超收益，已明确不做。
**跑不跑这个脚本都不影响演示**，不跑就是实时合成，首字 0.8 s。

用法
----
    python precache_cli.py demo_script.txt
    python precache_cli.py demo_script.txt --voice REDACTED-ROTATE-ME
    python precache_cli.py demo_script.txt --check-only     # 只校验，不合成

输入文件：每行一条讲解词；`#` 开头为注释，空行忽略。

三个必须知道的前提
------------------
1. **文案要提前拿到手**：缓存键 = sha1(TTS_FORCE_ENGINE | TTS_VOICE | 文本)，
   措辞一变键就变，缓存不命中、白跑一次。演示路线定了之后顺手向 Agent 要一份
   路线讲解词即可（**零开发量，不需要他们加接口**）；要不到就不预热。
   备注：`temperature=0` 之类**不算保证**（服务端版本/并发/浮点都会漂，且漂了不报错），
   真要逐字一致只能靠显式落盘的文本文件。此条只作参考，**不要求上游做任何事**。
2. 预热时的音色与 force_engine 必须与现场一致，否则缓存键变化、预热全部失效。
3. 预热必须在**云端可用**时进行。云端不可用时该句会被跳过（不写缓存），脚本会检测并报错。
"""

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tts import TTS, Config, split_sentences, CLOUD_SAMPLE_RATE  # noqa: E402

logging.basicConfig(
    level=logging.WARNING,          # 默认安静，只报问题；--verbose 可放开
    format="%(asctime)s %(levelname)-7s [precache] %(message)s",
)
log = logging.getLogger("tts.precache")


def load_lines(path: str):
    """读入讲解文案，每行一条。"""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read().splitlines()
    items = []
    for line in raw:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        items.append(s)
    return items


def cached_rate(cache_dir: str, key: str):
    """返回缓存文件对应的采样率；未命中返回 None。"""
    try:
        for name in os.listdir(cache_dir):
            if name.startswith(key + ".") and name.endswith(".pcm"):
                try:
                    return int(name.split(".")[-2])
                except (ValueError, IndexError):
                    return 0        # 文件在但文件名异常
    except FileNotFoundError:
        return None
    return None


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024


def dir_size(path: str) -> int:
    total = 0
    try:
        for name in os.listdir(path):
            fp = os.path.join(path, name)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    except FileNotFoundError:
        pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把讲解文案全量合成为本地音频缓存（演示前离线保底）")
    ap.add_argument("script", help="文案文件，每行一条讲解词")
    ap.add_argument("--voice", default=None, help="覆盖音色（须与现场一致）")
    ap.add_argument("--check-only", action="store_true",
                    help="只校验缓存状态，不做合成")
    ap.add_argument("--verbose", action="store_true", help="打印逐句进度")
    args = ap.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    if not os.path.exists(args.script):
        print(f"[错误] 找不到文案文件：{args.script}", file=sys.stderr)
        return 2

    items = load_lines(args.script)
    if not items:
        print(f"[错误] {args.script} 里没有有效文案（空行和 # 注释会被忽略）",
              file=sys.stderr)
        return 2

    # 预热不允许出声，也不允许按音频时长空等
    os.environ["TTS_PLAYER"] = "null"

    cfg = Config()
    if args.voice:
        cfg.voice = args.voice
    engine = TTS(cfg)

    cache_dir = os.path.abspath(cfg.cache_dir)
    print("=" * 66)
    print("  讲解文案预热（演示前离线保底）")
    print("=" * 66)
    print(f"  文案文件   : {os.path.abspath(args.script)}")
    print(f"  讲解条数   : {len(items)}")
    print(f"  音色       : {cfg.voice}")
    print(f"  force_engine: {cfg.force_engine or '(自动)'}")
    print(f"  缓存目录   : {cache_dir}")
    print(f"  云端采样率 : {CLOUD_SAMPLE_RATE} Hz")
    print("-" * 66)

    # 分句（与现场播报使用同一个分句器，保证缓存键一致）
    all_sents = []
    for it in items:
        all_sents.extend(split_sentences(it))
    print(f"  分句结果   : {len(all_sents)} 句（现场播报按此粒度命中缓存）")

    key_of = engine._cache_key           # 有意复用：保证与运行时的键完全一致

    # ---- 前置检查：云端必须可用 ----
    cloud_ok = engine.cloud.available()
    if not cloud_ok and cfg.force_engine != "mock":
        print()
        print("  [错误] 云端不可用（MIMO_API_KEY 未配置或无效），无法预热。")
        print("         本模块已裁掉本地兜底引擎，预热必须联网完成。已中止。")
        engine.close()
        return 3
    if not cloud_ok:
        print()
        print(f"  [注意] 云端不可用，但已显式指定 TTS_FORCE_ENGINE={cfg.force_engine}。")
        print("         注意：这样预热出来的不是现场音质。")

    # ---- 阶段一：合成 ----
    if args.check_only:
        print("\n  跳过合成（--check-only）")
    else:
        t0 = time.time()
        ok_cnt = 0
        print()
        for i, text in enumerate(items, 1):
            n_before = len([s for s in split_sentences(text)
                            if cached_rate(cache_dir, key_of(s)) is not None])
            t1 = time.time()
            ok = engine.speak(text, wait=True)
            dt = time.time() - t1
            sents = split_sentences(text)
            n_after = len([s for s in sents
                           if cached_rate(cache_dir, key_of(s)) is not None])
            status = "命中" if n_after == len(sents) and n_before == n_after else "新增"
            if ok:
                ok_cnt += 1
            else:
                status = "失败"
            print(f"  [{i:>2}/{len(items)}] {len(text):>3} 字 / {len(sents)} 句  "
                  f"{dt:>5.1f}s  {status}"
                  + (f"  {text[:22]}…" if args.verbose else ""))
        total_t = time.time() - t0
        print("-" * 66)
        print(f"  合成完成   : {ok_cnt}/{len(items)} 条成功，耗时 {total_t:.1f}s")

    # ---- 阶段二：校验（这是判断"现场能不能离线播"的唯一依据）----
    print()
    print("  校验缓存状态 …")
    missing, wrong_rate = [], []
    for s in all_sents:
        sr = cached_rate(cache_dir, key_of(s))
        if sr is None:
            missing.append(s)
        elif sr != CLOUD_SAMPLE_RATE:
            wrong_rate.append((s, sr))

    hit = len(all_sents) - len(missing) - len(wrong_rate)
    print(f"  命中       : {hit}/{len(all_sents)} 句")
    if wrong_rate:
        print(f"  [警告] {len(wrong_rate)} 句的缓存采样率不是云端值 —— 说明合成时降级了：")
        for s, sr in wrong_rate[:5]:
            print(f"         {sr} Hz  {s[:30]}…")
        print("         现场会播这段兜底音质的音频，建议清掉缓存重跑。")
    if missing:
        print(f"  [失败] {len(missing)} 句没有缓存，现场这 {len(missing)} 句将走实时合成：")
        for s in missing[:5]:
            print(f"         {s[:40]}…")

    print("-" * 66)
    if not missing and not wrong_rate:
        print("  ✅ 全部命中。现场这些句子直接读本地文件，不发起网络请求（首字 ≈0）。")
        print("     注意：现场运行时的音色与 TTS_FORCE_ENGINE 必须与本次一致，")
        print("          任一改变都会导致缓存键变化、全部失效。")
        rc = 0
    else:
        print("  ❌ 未全部命中，不要就此拿去演示。见上面的失败/警告明细。")
        rc = 1

    print(f"  缓存体积   : {human_size(dir_size(cache_dir))}")
    print(f"  缓存目录   : {cache_dir}")
    print("  —— 现场演示前建议再跑一次 `--check-only` 复核。")
    print("=" * 66)

    engine.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
