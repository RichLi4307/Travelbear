# -*- coding: utf-8 -*-
"""按键监听：把物理按键接到状态机的 trigger()。

- 树莓派：gpiozero.Button（GPIO 引脚，含防抖），需 `pip install gpiozero`
- 开发机：按回车键模拟按键（无需硬件，便于本地调试）

线程安全说明：
    gpiozero 的按键回调运行在它自己的后台线程，而 agent.trigger() 内部要
    用 asyncio 创建任务、必须在事件循环线程执行。所以这里用
    loop.call_soon_threadsafe() 把按键事件安全地送回事件循环。
"""

import asyncio
import threading

# 树莓派 GPIO 按键默认引脚（BCM 编号，按实际接线改）
DEFAULT_GPIO_PIN = 17
# 防抖时间（毫秒）：tecs 要求 ≥300ms，防止按钮抖动误触发
BOUNCE_MS = 300


class ButtonMonitor:
    """按键监听器：把「按下」事件转成对 callback 的一次调用。"""

    def __init__(self, callback, gpio_pin: int = DEFAULT_GPIO_PIN,
                 bounce_ms: int = BOUNCE_MS, loop: asyncio.AbstractEventLoop = None):
        self.callback = callback      # 事件循环线程里执行的函数（agent.trigger）
        self.gpio_pin = gpio_pin
        self.bounce_ms = bounce_ms
        self.loop = loop

    def start(self):
        """启动监听。树莓派走 GPIO，开发机回退到键盘（回车键）。"""
        self.loop = self.loop or asyncio.get_running_loop()

        try:
            import gpiozero  # noqa: F401
        except ImportError:
            self._start_keyboard()
        else:
            self._start_gpio()

    # ------------------------------------------------------------------
    # 内部：线程安全地派发按键事件
    # ------------------------------------------------------------------
    def _dispatch(self):
        """把按键事件安全地送回事件循环线程执行。"""
        self.loop.call_soon_threadsafe(self.callback)

    # ------------------------------------------------------------------
    # 树莓派：gpiozero（含防抖）
    # ------------------------------------------------------------------
    def _start_gpio(self):
        from gpiozero import Button
        btn = Button(self.gpio_pin, bounce_time=self.bounce_ms / 1000.0)
        btn.when_pressed = self._dispatch
        print(f"[按键] 已监听 GPIO{self.gpio_pin}（防抖 {self.bounce_ms}ms），按下即触发")

    # ------------------------------------------------------------------
    # 开发机：键盘回车模拟
    # ------------------------------------------------------------------
    def _start_keyboard(self):
        print("[按键] 开发机模式：按回车键模拟按键（gpiozero 未安装，Ctrl+C 退出）")

        def _listen():
            while True:
                try:
                    input()
                except EOFError:
                    break
                self._dispatch()

        threading.Thread(target=_listen, daemon=True).start()
