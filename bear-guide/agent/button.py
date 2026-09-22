# -*- coding: utf-8 -*-
"""按键监听：功能键（短按=唤醒讲解 / 长按=服务锁）+ 音量加 + 音量减。

接线（BCM 编号，实测按钮一端接 GPIO，另一端接 3.3V，高电平有效，
内部下拉；若某颗按钮表现相反，把 PULL_UP 改成 True 即可）：
    GPIO17  功能键：短按=待机时触发讲解；播报中短按=打断（停播报+下行提示音）
                    长按 2 秒=解锁/上锁导览服务（锁开启时短按无效）
    GPIO18  音量＋：仅响应短按（无长按事件），防抖 50ms
    GPIO27  音量−：仅响应短按（无长按事件），防抖 50ms

短按/长按区分（保证「解锁的长按」不会触发讲解）：
    按下时清标志位 → held 事件触发长按并置位 → 松开时若标志已置位则不做短按。

- 树莓派：gpiozero.Button（Pi5 走 lgpio 后端，需 pip install gpiozero）
- 开发机：回车=功能键短按，p=长按(锁)，+/-=音量（无硬件调试）

线程安全：gpiozero 回调运行在它的后台线程，on_short_press 会被
调度回事件循环线程执行；长按和音量回调不碰事件循环，直接调用即可。
"""

import asyncio
import threading

from agent.service import play_tones
from agent.volume import volume_down, volume_up

# ---- 三个按键的 BCM 引脚（按实际接线改这里） ----
TRIGGER_PIN = 17        # 功能键
VOL_UP_PIN = 18         # 音量＋
VOL_DOWN_PIN = 27       # 音量−
# 按钮接 3.3V 高电平有效（实测），故内部用下拉；若改成接 GND 的接法，设 True
PULL_UP = False
# 功能键防抖（毫秒）：tecs 要求 ≥300ms；防抖值会原样变成按键响应延迟
BOUNCE_MS = 300
# 音量键防抖（毫秒）：机械抖动一般 <20ms，50ms 足够；小了响应才跟手
VOL_BOUNCE_MS = 50
# 功能键长按时长（秒）：达到即视为解锁/上锁
LONG_PRESS_S = 2.0


class ButtonMonitor:
    """把三路按键事件分发到：短按讲解 / 长按开关锁 / 音量加 / 音量减。"""

    def __init__(self, on_short_press, on_long_press=None,
                 on_vol_up=None, on_vol_down=None,
                 loop: asyncio.AbstractEventLoop = None):
        self.on_short_press = on_short_press   # 会经 loop.call_soon_threadsafe 调度
        self.on_long_press = on_long_press     # 直接调用（gpiozero 线程）
        self.on_vol_up = on_vol_up             # 直接调用
        self.on_vol_down = on_vol_down         # 直接调用
        self.loop = loop
        self._buttons = []

    def start(self):
        """启动监听。树莓派走 GPIO，开发机回退到键盘。"""
        self.loop = self.loop or asyncio.get_running_loop()

        try:
            import gpiozero  # noqa: F401
        except ImportError:
            self._start_keyboard()
        else:
            self._start_gpio()

    # ------------------------------------------------------------------
    # 内部：线程安全地派发功能键短按（要进事件循环）
    # ------------------------------------------------------------------
    def _dispatch(self, fn):
        self.loop.call_soon_threadsafe(fn)

    @staticmethod
    def _print_vol(fn) -> None:
        pct = fn()
        if pct is not None:
            print(f"[音量] {pct}%")
            # 短促一滴作为按键反馈，在新音量下播放，顺便让人感知音量大小
            play_tones(((880.0, 0.08),), amplitude=8000)

    # ------------------------------------------------------------------
    # 树莓派：gpiozero（含防抖、长按识别）
    # ------------------------------------------------------------------
    def _start_gpio(self):
        from gpiozero import Button

        trigger = Button(TRIGGER_PIN,
                         pull_up=PULL_UP,
                         bounce_time=BOUNCE_MS / 1000.0,
                         hold_time=LONG_PRESS_S)
        long_fired = {"fired": False}

        def _on_trigger_press():
            long_fired["fired"] = False

        def _on_trigger_held():
            long_fired["fired"] = True          # 长按成立，松开时不再算短按
            if self.on_long_press:
                self.on_long_press()

        def _on_trigger_release():
            if not long_fired["fired"] and self.on_short_press:
                self._dispatch(self.on_short_press)

        trigger.when_pressed = _on_trigger_press
        trigger.when_held = _on_trigger_held
        trigger.when_released = _on_trigger_release

        vol_up = Button(VOL_UP_PIN, pull_up=PULL_UP, bounce_time=VOL_BOUNCE_MS / 1000.0)
        vol_down = Button(VOL_DOWN_PIN, pull_up=PULL_UP, bounce_time=VOL_BOUNCE_MS / 1000.0)
        # 音量键只接 when_pressed：无长按事件，抖动被 bounce_time 滤掉
        if self.on_vol_up:
            vol_up.when_pressed = lambda: self._print_vol(self.on_vol_up)
        if self.on_vol_down:
            vol_down.when_pressed = lambda: self._print_vol(self.on_vol_down)

        self._buttons = [trigger, vol_up, vol_down]
        print(f"[按键] 功能键=GPIO{TRIGGER_PIN}（短按讲解/长按{LONG_PRESS_S:.0f}s开关锁，防抖{BOUNCE_MS}ms）"
              f" 音量+=GPIO{VOL_UP_PIN} 音量-=GPIO{VOL_DOWN_PIN}（防抖{VOL_BOUNCE_MS}ms）")

    # ------------------------------------------------------------------
    # 开发机：键盘模拟（回车=短按，p=长按，+/-=音量）
    # ------------------------------------------------------------------
    def _start_keyboard(self):
        print("[按键] 开发机模式：回车=功能键短按，p=长按(开关锁)，+/-=音量（Ctrl+C 退出）")

        def _listen():
            while True:
                try:
                    ch = input()
                except EOFError:
                    break
                ch = ch.strip().lower()
                if ch in ("+", "＋"):
                    self._print_vol(volume_up)
                elif ch in ("-", "－"):
                    self._print_vol(volume_down)
                elif ch == "p":
                    if self.on_long_press:
                        self.on_long_press()
                elif self.on_short_press:
                    self._dispatch(self.on_short_press)

        threading.Thread(target=_listen, daemon=True).start()
