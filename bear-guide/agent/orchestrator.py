import asyncio
from common.types import PositionInfo, SceneInfo, ASRResult
from location.location_interface import LocationInterface
from vision.vision_interface import VisionInterface
from tts.tts_interface import TTSInterface
from asr.asr_interface import ASRInterface
from agent.prompt_builder import build_robust_prompt, build_local_script

class Orchestrator:
    """任务编排器：负责调度各个模块，串起完整业务流程（讲解 + 语音问答）"""

    def __init__(
        self,
        location: LocationInterface,
        vision: VisionInterface,
        tts: TTSInterface,
        llm_client=None,
        asr: ASRInterface = None
    ):
        self.location = location    # 定位模块实例
        self.vision = vision        # 识图模块实例
        self.tts = tts              # 语音模块实例
        self.llm = llm_client       # LLM客户端，Mock模式下为None
        self.asr = asr              # 语音识别模块，未接入时为None

    async def acquire_all(self):
        """
        并发执行定位和识图，两个任务同时启动，都完成再返回
        比串行节省一半时间
        """
        # 创建两个异步任务
        position_task = self.location.get_position(timeout=5.0)
        scene_task = self.vision.get_scene(timeout=8.0)

        # 同时等待两个任务完成
        position_result, scene_result = await asyncio.gather(
            position_task,
            scene_task,
            return_exceptions=False
        )
        return position_result, scene_result

    async def generate_script(self, position: PositionInfo, scene: SceneInfo) -> str:
        """生成讲解文案：真实LLM → 本地话术，逐级降级，任何情况都不说错话"""
        prompt, local_fallback = build_robust_prompt(position, scene)

        # 定位+识图都失败：直接固定话术，不依赖 LLM
        if local_fallback:
            return local_fallback

        # 无 LLM（Mock 模式）：本地兜底文案
        if not self.llm:
            return build_local_script(position, scene)

        # 真实模式：调用云端大模型生成讲解
        response = await self.llm.chat(prompt)
        return response.content

    async def play_script(self, text: str):
        """调用语音模块播报文案"""
        await self.tts.speak(text)

    # ------------------------------------------------------------------
    # 语音问答（新增）
    # ------------------------------------------------------------------
    async def listen_user(self, timeout: float = 5.0) -> ASRResult:
        """聆听游客提问，返回识别结果。

        未接入 ASR 时返回静默，绝不抛异常、不卡主链路。
        """
        if not self.asr:
            return ASRResult(success=False, text="", is_silence=True,
                             error_msg="未接入语音识别模块")
        return await self.asr.listen(timeout=timeout)

    async def generate_reply(self, user_text: str) -> str:
        """根据游客提问生成回答（多轮对话，带历史记忆）。"""
        if not self.llm:
            return "抱歉，我现在还不会回答这个问题。"
        response = await self.llm.ask(user_text)
        return response.content
