# -*- coding: utf-8 -*-
"""模拟语音识别模块：本地调试用，不接麦克风。

默认行为：第一次听返回一个固定提问，之后再听返回静默，
方便演示「讲解 → 提问 → 回答 → 静默结束」的完整问答闭环。
"""

import asyncio

from common.types import ASRResult
from asr.asr_interface import ASRInterface


class MockASR(ASRInterface):
    """模拟 ASR：不接麦克风，按预设返回提问。"""

    def __init__(self, canned_text: str = "请问这座建筑有什么历史故事吗？"):
        self._canned_text = canned_text
        self._asked = False

    async def listen(self, timeout: float = 5.0) -> ASRResult:
        # 模拟收音延迟
        await asyncio.sleep(0.5)

        # 第一次听返回固定提问，之后再听返回静默，让对话自然结束
        if self._canned_text and not self._asked:
            self._asked = True
            return ASRResult(success=True, text=self._canned_text, is_silence=False)

        return ASRResult(success=True, text="", is_silence=True)
