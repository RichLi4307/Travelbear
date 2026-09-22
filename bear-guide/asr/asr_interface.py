# -*- coding: utf-8 -*-
"""语音识别（ASR）模块抽象接口 —— 契约文件。

契约要求（对齐 Vision / Location 接口风格，不可改）：
    文件位置：asr/asr_interface.py
    抽象基类：ASRInterface
    必须实现：async def listen(self, timeout: float = 5.0) -> ASRResult
    返回类型：common.types.ASRResult（success / text / is_silence / error_msg）
    内部异常不许抛出，必须封装进 error_msg

设计说明：
    和 vision_interface.py / location_interface.py 保持同一套"三件套"结构：
        asr_interface.py   契约（抽象基类）
        mock_asr.py        假实现（本地调试）
        dashscope_asr.py   真实实现（DashScope Paraformer，待麦克风硬件）
"""

from abc import ABC, abstractmethod

from common.types import ASRResult


class ASRInterface(ABC):
    """语音识别模块抽象接口。Agent 核心只依赖这个抽象 + listen() 一个方法。"""

    @abstractmethod
    async def listen(self, timeout: float = 5.0) -> ASRResult:
        """聆听一段游客语音，返回识别出的文字。

        参数：
            timeout  最长聆听秒数；超时未听到有效语音按静默处理

        返回 ASRResult：
            success    True = 本次聆听正常结束（可能识别到文字，也可能是静默）
            text       识别出的文字（游客说的话）；静默或失败时为空字符串
            is_silence True = 没听到有效语音（超时 / 纯静默）
            error_msg  失败原因；成功时为空字符串

        约定：本函数不抛业务异常，一切失败都进 error_msg。
        """
        pass
