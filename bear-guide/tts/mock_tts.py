import asyncio
from common.types import PlayStatus
from tts.tts_interface import TTSInterface

class MockTTS(TTSInterface):
    """模拟语音模块，本地调试用，不发声只打印"""
    async def speak(self, text: str, interrupt: bool = False) -> PlayStatus:
        print(f"[语音播报] {text}")
        # 模拟播报时长，按文字长度估算
        await asyncio.sleep(len(text) * 0.1)
        return PlayStatus(success=True, is_playing=False)
