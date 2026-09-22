# -*- coding: utf-8 -*-
"""按键硬件检测（脱离主程序，单独验证三颗按钮）。

    GPIO17 功能键：按一下 → 「叮咚」叫一声
    GPIO18 音量＋：按一下 → 音量 +5%，并在新音量下滴一声
    GPIO27 音量−：按一下 → 音量 −5%，并在新音量下滴一声
    长按功能键 2 秒 或 Ctrl+C → 退出

用法（在项目根目录 bear-guide 下）：
    .venv/bin/python agent/hw_check.py
"""

import os
import sys
import time

# 让脚本可直接以 `python agent/hw_check.py` 方式运行
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from gpiozero import Button   # noqa: E402

from agent.service import play_tones   # noqa: E402
from agent.volume import get_volume, volume_down, volume_up   # noqa: E402

# 与 agent/button.py 保持一致：接 3.3V 高电平有效；音量键防抖取小值保证跟手
TRIGGER_PIN = 17
VOL_UP_PIN = 18
VOL_DOWN_PIN = 27
PULL_UP = False
BOUNCE_MS = 300
VOL_BOUNCE_MS = 50
EXIT_HOLD_S = 2.0

# 功能键「叫声」：两声上行音
FUNCTION_TONES = ((660.0, 0.12), (990.0, 0.2))
# 音量键反馈：短促一滴（在新音量下播放，顺便试音量大小）
TICK_TONES = ((880.0, 0.08),)
TICK_AMPLITUDE = 8000


def main() -> None:
    print(f"当前音量 {get_volume()}%", flush=True)
    print(f"功能键=GPIO{TRIGGER_PIN}（按一下叫一声） 音量+=GPIO{VOL_UP_PIN} 音量-=GPIO{VOL_DOWN_PIN}",
          flush=True)
    print(f"长按功能键 {EXIT_HOLD_S:.0f} 秒或 Ctrl+C 退出", flush=True)

    trigger = Button(TRIGGER_PIN, pull_up=PULL_UP,
                     bounce_time=BOUNCE_MS / 1000.0, hold_time=EXIT_HOLD_S)
    vol_up = Button(VOL_UP_PIN, pull_up=PULL_UP, bounce_time=VOL_BOUNCE_MS / 1000.0)
    vol_down = Button(VOL_DOWN_PIN, pull_up=PULL_UP, bounce_time=VOL_BOUNCE_MS / 1000.0)

    state = {"held": False, "exit": False}

    def _on_trigger_press() -> None:
        state["held"] = False

    def _on_trigger_held() -> None:
        state["held"] = True
        state["exit"] = True

    def _on_trigger_release() -> None:
        if not state["held"]:
            print("[功能键] 叮咚！", flush=True)
            play_tones(FUNCTION_TONES)

    def _on_vol_up() -> None:
        print(f"[音量+] {volume_up()}%", flush=True)
        play_tones(TICK_TONES, amplitude=TICK_AMPLITUDE)

    def _on_vol_down() -> None:
        print(f"[音量-] {volume_down()}%", flush=True)
        play_tones(TICK_TONES, amplitude=TICK_AMPLITUDE)

    trigger.when_pressed = _on_trigger_press
    trigger.when_held = _on_trigger_held
    trigger.when_released = _on_trigger_release
    vol_up.when_pressed = _on_vol_up
    vol_down.when_pressed = _on_vol_down

    try:
        while not state["exit"]:
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    print(f"检测结束，当前音量 {get_volume()}%", flush=True)


if __name__ == "__main__":
    main()
