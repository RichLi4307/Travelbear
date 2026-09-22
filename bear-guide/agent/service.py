# -*- coding: utf-8 -*-
"""导览服务锁：控制「短按唤醒讲解」是否生效，上电默认上锁。

- 功能键短按：解锁状态下才触发讲解（播报中再按 = 打断）；上锁时给出提示
- 功能键长按：解锁 / 上锁切换。解锁时先跑自检，通过后放「就绪提示音」
- 音量加/减不受锁控制，任何时候都可用

提示音用 aplay 播放本地合成的正音（不联网、不花钱、秒响）。
"""

import asyncio
import math
import os
import shutil
import struct
import subprocess
import tempfile
import wave

from agent import volume

# 解锁自检通过后的提示音：两声上行音
READY_TONES = ((880.0, 0.15), (1320.0, 0.22))
# 上锁提示音：一声短低音
LOCK_TONES = ((520.0, 0.25),)
# 播报被打断的提示音：两声下行音（与「就绪上行」区分）
INTERRUPT_TONES = ((880.0, 0.1), (587.0, 0.16))
# 功能键「受理」提示音：两声短上行（按下了且开始干活）
RECEIVED_TONES = ((620.0, 0.09), (830.0, 0.14))
# 功能键「忙碌/已上锁」提示音：两声低短音（按了，但被忽略）
BUSY_TONES = ((440.0, 0.09), (440.0, 0.09))
# 自检项涉及的三个环境变量
_ENV_KEYS = ("AMAP_WEB_KEY", "DASHSCOPE_API_KEY", "MIMO_API_KEY")


def play_tones(tones, amplitude: float = 12000) -> None:
    """把一组 (频率Hz, 时长秒) 正音合成 wav 后用 aplay 播放（非阻塞）。"""
    aplay = shutil.which("aplay")
    if not aplay:
        print("[提示音] 未找到 aplay，跳过")
        return
    path = os.path.join(tempfile.gettempdir(), "bear_guide_tone.wav")
    samplerate = 22050
    frames = bytearray()
    for freq, dur in tones:
        n = int(samplerate * dur)
        edge = min(200, n // 4)  # 简单淡入淡出包络，避免爆音
        for i in range(n):
            env = min(1.0, i / edge, (n - i) / edge)
            sample = amplitude * env * math.sin(2 * math.pi * freq * i / samplerate)
            frames += struct.pack("<h", int(sample))
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(samplerate)
        w.writeframes(bytes(frames))
    subprocess.Popen([aplay, "-q", path],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GuideService:
    """导览服务锁：locked=True 时短按不触发讲解。

    speak_fn: 可选的语音播报回调（主程序把 TTS 调度器注入进来），
    用于「自检中/自检完成/已上锁」等场景语音；为 None 时只放提示音。
    """

    def __init__(self, speak_fn=None) -> None:
        self.locked = True
        self._speak = speak_fn
        print("[服务] 已上锁：长按功能键 2 秒解锁，音量键随时可用")

    def _voice(self, text: str) -> None:
        """让 TTS 说一句话；失败只记日志（提示音是可靠的兜底）。"""
        if not self._speak:
            return
        try:
            self._speak(text)
        except Exception as exc:                           # noqa: BLE001
            log.warning("提示语音播报调度失败：%s", exc)

    # ------------------------------------------------------------------
    # 功能键：短按（由 button 层调度回事件循环后执行）
    # ------------------------------------------------------------------
    def on_short_press(self, trigger) -> None:
        """短按=使用服务。上锁时给声音+语音反馈，避免用户以为没按到连按。"""
        if self.locked:
            print("[服务] 已上锁，长按功能键 2 秒解锁")
            play_tones(BUSY_TONES)
            self._voice("设备已上锁，请长按功能键两秒解锁")
            return
        trigger()

    # ------------------------------------------------------------------
    # 功能键：长按 = 解锁/上锁
    # ------------------------------------------------------------------
    def on_long_press(self) -> None:
        self.locked = not self.locked
        if self.locked:
            print("[服务] 已上锁，短按不再触发讲解")
            play_tones(LOCK_TONES)
            self._voice("已上锁")
        else:
            # 先给即时反馈（提示音 + 语音），再跑自检，避免用户一直按着等
            print("[服务] 解锁，开始自检...")
            play_tones(RECEIVED_TONES)
            self._voice("好的，正在自检，请稍候")
            self._self_check()
            print("[服务] 自检完成，可以讲解（再长按 2 秒上锁）")
            play_tones(READY_TONES)
            self._voice("自检完成，请开始使用")

    # ------------------------------------------------------------------
    # 开机自检：本地项目，不联网（联网模块运行时有各自降级）
    # ------------------------------------------------------------------
    def _self_check(self) -> None:
        checks = [
            ("三个 API Key 已配置", all(os.environ.get(k) for k in _ENV_KEYS)),
            ("识图链路（取景 + 云端识别）", self._check_vision()),
            ("音量通道可读", volume.get_volume() >= 0),
            ("aplay 播放命令可用", bool(shutil.which("aplay"))),
        ]
        for name, ok in checks:
            print(f"  [{'OK' if ok else '!!'}] {name}")

    @staticmethod
    def _check_vision() -> bool:
        """自检走真实识图链路，而不只验「摄像头能出图」。

        摄像头偶发掉线重枚举时两者会不一致；识别结果顺带打印，
        用户解锁时就能直接核对识图准不准。
        """
        try:
            from vision.vision_qwen_vl import QwenVLVision
            result = asyncio.run(
                QwenVLVision(image_source="camera").get_scene(timeout=8.0))
        except Exception as exc:
            print(f"       识图链路异常：{exc}")
            return False
        if result.success:
            print(f"       识别结果：{result.scene_description[:40]}")
            return True
        print(f"       识图失败：{result.error_msg}")
        return False
