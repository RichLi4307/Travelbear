# -*- coding: utf-8 -*-
"""扬声器音量控制：直接调 ALSA amixer 相对增减 PCM 通道。

和播放链（aplay 走 bcm2835 PCM）天然一致，不依赖任何 Python 音频库，
音量加/减按键最终都调到这里。
"""

import re
import shutil
import subprocess

# 每次按键的调节步长（占量程百分比）
STEP_PERCENT = 5
# 树莓派 3.5mm 耳机口对应声卡 0 的 PCM 通道
CARD = "0"
CONTROL = "PCM"

_AMIXER = shutil.which("amixer") or "/usr/bin/amixer"
_PERCENT_RE = re.compile(r"\[(\d+)%\]")


def _amixer(*args) -> str:
    result = subprocess.run(
        [_AMIXER, "-c", CARD, *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"amixer 执行失败：{result.stderr.strip()}")
    return result.stdout


def get_volume() -> int:
    """返回当前音量百分比（0-100），读不到返回 -1。"""
    match = _PERCENT_RE.findall(_amixer("sget", CONTROL))
    return int(match[-1]) if match else -1


def _change(step: int) -> int:
    """相对调节音量，返回调节后的百分比。

    注意：该声卡的 PCM 是 dB 刻度（-102.39~+4dB），amixer 的 5%+/- 相对
    语法在其上语义错乱（0% 时按 + 永远停在 0，15% 时按 - 会跳到 100%），
    所以这里先读当前值、算好目标后用绝对百分比设置（已验证线性正确）。
    """
    cur = get_volume()
    if cur < 0:
        return -1
    target = min(100, max(0, cur + step))
    if target == cur:
        return cur
    match = _PERCENT_RE.findall(_amixer("sset", CONTROL, f"{target}%"))
    return int(match[-1]) if match else get_volume()


def volume_up() -> int:
    """音量加一档，返回当前音量百分比。"""
    return _change(+STEP_PERCENT)


def volume_down() -> int:
    """音量减一档，返回当前音量百分比。"""
    return _change(-STEP_PERCENT)
