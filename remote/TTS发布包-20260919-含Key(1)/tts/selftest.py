# -*- coding: utf-8 -*-
"""TTS 模块 · 一键自检（测试版）

把模块的全部离线验证按顺序跑一遍，实时显示结果，最后汇总成一份
可以转发给别人的报告（`自检报告.txt`）。

用法
----
    python selftest.py              离线自检：环境体检 + 核心逻辑 + 契约接口
                                    不需要 API Key、不联网、不出声
    python selftest.py --online     再跑联网阶段（目标切换 / 真实合成，会出声）
    python selftest.py --quick      只做环境体检，几秒出结果
    python selftest.py --no-report  不写报告文件

Windows 也可以直接双击项目根的 `一键验收.bat`。

退出码
------
    0  没有失败项
    1  有失败项（报告里会标出是哪一段）

阶段划分
--------
    阶段 0  环境体检              Python 版本 / 播放后端 / aplay / Key / 缓存
    阶段 1  test_tts.py --bench    分句器 / 增益 / WAV 解析          ← 离线
    阶段 2  test_interface.py      契约接口 8 情景 29 断言            ← 离线
    阶段 3  test_scene_switch.py   讲解目标切换 6 情景（真发请求）    ← 联网
    阶段 4  test_tts.py --long     真实云端合成（会出声）             ← 联网

为什么这样设计
--------------
测试用例本身不写在这里，而是复用既有脚本（见上面的阶段表）。
本脚本只做「依次调用 → 实时显示 → 汇总判定 → 写报告」。
这样测试逻辑只有一份，不会出现「自检说通过、真测试却挂」的分叉。

没配 Key 时，阶段 3-4 标为「跳过」而不是「失败」—— Pi 首次部署就处在
「系统装好、Key 还没填」的状态，那时候不该报红。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import platform
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable or "python"
REPORT_NAME = "自检报告.txt"

# Windows 控制台默认用 GBK，子进程输出统一按 utf-8 解码，避免中文乱码
def _child_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


# ---------------------------------------------------------------- 环境体检


def load_dotenv(path: str) -> int:
    """把 .env 读进 os.environ（已存在的真实环境变量优先）。返回读入条数。"""
    if not os.path.exists(path):
        return 0
    n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
                    n += 1
    except OSError:
        return 0
    return n


def probe_backend() -> str:
    """探测播放后端（不打开设备，只做能力探测）。"""
    try:
        if HERE not in sys.path:
            sys.path.insert(0, HERE)
        import tts as engine  # noqa: PLC0415

        return engine.Player._detect("auto")
    except Exception as exc:  # noqa: BLE001
        return f"探测失败（{type(exc).__name__}: {exc}）"


def cache_stats(cache_dir: str) -> tuple[int, float]:
    """返回 (缓存条目数, 占用 MB)。"""
    if not os.path.isdir(cache_dir):
        return 0, 0.0
    n = 0
    total = 0
    for name in os.listdir(cache_dir):
        if not name.endswith(".pcm"):
            continue
        try:
            total += os.path.getsize(os.path.join(cache_dir, name))
            n += 1
        except OSError:
            pass
    return n, total / 1024 / 1024


def env_check() -> tuple[bool, list[str], bool]:
    """环境体检。返回 (是否致命, 报告行, 是否具备联网自检条件)。"""
    lines: list[str] = []
    fatal = False

    # Python 版本
    ver = sys.version.split()[0]
    parts = tuple(int(x) for x in ver.split(".")[:2] if x.isdigit())
    ok_ver = parts >= (3, 8)
    lines.append(
        f"{'OK ' if ok_ver else '!! '} Python {ver}"
        f"（{'满足' if ok_ver else '过旧'}，要求 >= 3.8）"
    )
    fatal |= not ok_ver

    # 平台
    lines.append(f"OK  运行平台 {platform.system()} {platform.release()} / {platform.machine()}")

    # 播放后端
    backend = probe_backend()
    is_linux = platform.system() == "Linux"
    if backend in ("dryrun", "null") or backend.startswith("探测失败"):
        lines.append(
            f"!!  播放后端：{backend}"
            f" —— 不会出声{'（Pi 上请确认已装 alsa-utils）' if is_linux else ''}"
        )
        fatal = True
    else:
        lines.append(f"OK  播放后端：{backend}")

    # aplay（Linux 上最关键的一条）
    if is_linux:
        aplay = shutil.which("aplay")
        lines.append(
            f"{'OK ' if aplay else '!! '} aplay：{aplay or '未找到（sudo apt install alsa-utils）'}"
        )
        fatal |= not aplay
        lines.append(f"OK  TTS_ALSA_DEVICE = {os.environ.get('TTS_ALSA_DEVICE') or '(未设置，用系统默认声卡)'}")

    # 云端凭据（只看有无，不打印内容）
    key = os.environ.get("MIMO_API_KEY", "")
    can_online = bool(key) and not key.startswith("invalid")
    tail = "" if not key else f"（长度 {len(key)}）"
    lines.append(f"{'OK ' if can_online else '-- '} MIMO_API_KEY：{'已配置' if key else '未配置'}{tail}")
    lines.append(f"OK  音色 TTS_VOICE = {os.environ.get('TTS_VOICE') or 'REDACTED-ROTATE-ME（默认）'}")

    # 缓存
    cache_dir = os.environ.get("TTS_CACHE_DIR") or os.path.join(HERE, ".tts_cache")
    n, mb = cache_stats(cache_dir)
    lines.append(f"OK  缓存目录 {cache_dir} —— {n} 条 / {mb:.1f} MB")

    # 可选依赖（不装也不影响核心链路）
    optional = []
    for mod in ("numpy", "pyaudio", "sounddevice"):
        try:
            __import__(mod)
            optional.append(f"{mod} 已装")
        except Exception:  # noqa: BLE001
            optional.append(f"{mod} 未装")
    lines.append(f"--  可选依赖：{'、'.join(optional)}（都不装也能跑）")

    return fatal, lines, can_online


# ---------------------------------------------------------------- 阶段执行


def run_stage(argv: list[str]) -> tuple[int, list[str]]:
    """跑一个阶段，实时打印输出并收集。返回 (退出码, 输出行)。"""
    proc = subprocess.Popen(
        [PY, *argv],
        cwd=HERE,
        env=_child_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        lines.append(line)
        print("    " + line, flush=True)
    proc.wait()
    return proc.returncode, lines


def find_line(lines: list[str], *needles: str) -> str:
    """从输出里找出最后一条包含任一关键词的行，用于报告摘要。"""
    hit = ""
    for line in lines:
        if any(n in line for n in needles):
            hit = line.strip()
    return hit


# 离线阶段：不需要 Key、不联网、不出声 —— 默认跑这些，任何机器上都能跑通。
OFFLINE_STAGES: list[tuple[str, str, list[str], tuple[str, ...]]] = [
    ("阶段 1", "核心逻辑：分句器 / 增益 / WAV 解析", ["test_tts.py", "--bench"],
     ("自检结果", "失败")),
    ("阶段 2", "契约接口：8 情景 / 29 断言", ["test_interface.py"],
     ("项通过", "全部通过", "失败")),
]

# 联网阶段：需要 MIMO_API_KEY，会真发请求（阶段 4 还会出声）。
# 只有加 --online 才跑；没配 Key 时标为「跳过」而不是「失败」——
# Pi 首次部署就处在「还没填 Key」的状态，那时候不该报红。
ONLINE_STAGES: list[tuple[str, str, list[str], tuple[str, ...]]] = [
    ("阶段 3", "讲解目标切换：6 情景（真发请求）", ["test_scene_switch.py"],
     ("快速连切是否串台", "抛异常的情景", "结论")),
    ("阶段 4", "真实云端合成（会出声）", ["test_tts.py", "--long"],
     ("引擎", "首字", "失败")),
]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="TTS 模块一键自检（测试版）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--online", action="store_true",
                    help="加跑联网阶段：目标切换 6 情景 + 真实云端合成（会出声）")
    ap.add_argument("--quick", action="store_true", help="只做环境体检")
    ap.add_argument("--no-report", action="store_true", help="不写报告文件")
    args = ap.parse_args()

    started = dt.datetime.now()
    n_dotenv = load_dotenv(os.path.join(HERE, ".env"))

    print("=" * 68)
    print("  TTS 模块 · 一键自检（测试版）")
    print("=" * 68)

    report: list[str] = [
        "TTS 模块 · 自检报告",
        "=" * 60,
        f"生成时间：{started.strftime('%Y-%m-%d %H:%M:%S')}",
        f"运行环境：{platform.system()} {platform.release()} / "
        f"Python {sys.version.split()[0]}",
        f"模块目录：{HERE}",
        f".env 读入：{n_dotenv} 条" if n_dotenv else ".env：无（用系统环境变量）",
        "",
    ]

    # ---- 阶段 0：环境体检 ----
    print("\n[阶段 0] 环境体检")
    print("-" * 68)
    fatal, env_lines, can_online = env_check()
    for line in env_lines:
        print("    " + line)
    report.append("[阶段 0] 环境体检 —— " + ("有致命问题" if fatal else "通过"))
    report.extend("    " + x for x in env_lines)
    report.append("")

    # results 三项：ok / fail / skip
    results: list[tuple[str, str, str, str]] = [
        ("阶段 0", "环境体检", "fail" if fatal else "ok",
         env_lines[0] if env_lines else "")
    ]

    def stage_run(key: str, title: str, argv: list[str], needles: tuple[str, ...]) -> None:
        """跑一个阶段：实时显示 → 记录结果 → 写报告。"""
        print(f"\n[{key}] {title}")
        print("-" * 68)
        t0 = dt.datetime.now()
        rc, lines = run_stage(argv)
        cost = (dt.datetime.now() - t0).total_seconds()
        summary = find_line(lines, *needles)
        ok = rc == 0
        results.append((key, title, "ok" if ok else "fail", summary))
        report.append(
            f"[{key}] {title} —— {'通过' if ok else '失败'}"
            f"（退出码 {rc}，耗时 {cost:.1f}s）"
        )
        if summary:
            report.append(f"    摘要：{summary}")
        report.append("")

    if not fatal and not args.quick:
        # ---- 离线阶段：默认跑 ----
        for key, title, argv, needles in OFFLINE_STAGES:
            stage_run(key, title, argv, needles)

        # ---- 联网阶段：只在 --online 时跑 ----
        if args.online:
            if can_online:
                for key, title, argv, needles in ONLINE_STAGES:
                    stage_run(key, title, argv, needles)
            else:
                for key, title, _argv, _needles in ONLINE_STAGES:
                    print(f"\n[{key}] {title}")
                    print("-" * 68)
                    print("    跳过：未配置 MIMO_API_KEY")
                    results.append((key, title, "skip", "未配置 MIMO_API_KEY"))
                    report.append(f"[{key}] {title} —— 跳过（未配置 MIMO_API_KEY）")
                    report.append("")
        else:
            print("\n[提示] 联网阶段（目标切换 / 真实合成）未跑 ——")
            print("       需要 Key 时加 --online：python selftest.py --online")
            report.append("联网阶段（阶段 3-4）未跑。加 --online 可执行（需要 MIMO_API_KEY）。")
            report.append("")

    # ---- 汇总 ----
    passed = sum(1 for _, _, st, _ in results if st == "ok")
    failed = sum(1 for _, _, st, _ in results if st == "fail")
    skipped = sum(1 for _, _, st, _ in results if st == "skip")
    total = len(results)
    all_ok = failed == 0
    mark = {"ok": "OK", "fail": "!!", "skip": "--"}

    print("\n" + "=" * 68)
    print("  汇总")
    print("=" * 68)
    for key, title, st, _ in results:
        print(f"  [{mark[st]}] {key}  {title}")

    summary_line = f"\n  {passed}/{total} 个阶段通过"
    if skipped:
        summary_line += f"，{skipped} 个跳过"
    summary_line += " —— " + ("全部通过" if all_ok else f"有 {failed} 项失败，见上")
    print(summary_line)

    report.append("=" * 60)
    report.append(f"结论：{passed}/{total} 个阶段通过"
                  + (f"，{skipped} 个跳过" if skipped else "")
                  + (" —— 全部通过" if all_ok else f" —— 有 {failed} 项失败"))
    for key, title, st, _ in results:
        report.append(f"  [{mark[st]}] {key} {title}")
    report.append("")
    report.append("说明：阶段 0-2 为离线自检（不出声、不联网），任何机器都能跑；")
    report.append("     阶段 3-4 为联网自检（需 --online 且配好 MIMO_API_KEY）。")
    report.append("     真机听感与现场手感请用 试听.bat / listen_demo.py 人工确认。")

    if not args.no_report:
        path = os.path.join(HERE, REPORT_NAME)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(report) + "\n")
            print(f"\n  报告已写入：{path}")
        except OSError as exc:
            print(f"\n  报告写入失败：{exc}")

    print()
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
