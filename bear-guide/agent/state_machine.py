import asyncio
import logging
import random
from common.types import DeviceState
from agent.orchestrator import Orchestrator
from agent.service import play_tones, INTERRUPT_TONES, RECEIVED_TONES, BUSY_TONES

log = logging.getLogger(__name__)

# 连续语音问答开关：麦克风 + OSS 未到位，冻结问答模块（一按一讲模式）。
# 解冻方法：硬件到位后改 True，并把 main.py 的 ASR 接线恢复为 DashScopeASR。
ENABLE_CONVERSATION = False

# 受理后的即时语音安抚（讲解员口吻，不说"拍照中/生成中"这类技术词）。
# 随机轮换防机械感；与识图/LLM 并发，不占用链路时间。
RECEIVED_VOICE = (
    "好嘞，我看看这是哪儿",
    "稍等，我瞅瞅眼前这个",
    "让我看看，这是哪儿呀",
)

class AgentStateMachine:
    """
    有限状态机：设备的大脑，控制状态流转
    保证同一时间只做一件事，逻辑清晰，不出乱
    """

    def __init__(self, orchestrator: Orchestrator):
        # 初始状态：待机
        self.state = DeviceState.IDLE
        # 编排器实例，具体干活的
        self.orchestrator = orchestrator
        # 中断标志：用于按键打断播报
        self._interrupt_flag = False
        # 语音提示通道（main.py 注入，与 GuideService 同一个 _speak）
        self.speak_fn = None

    async def run_once(self):
        """执行一次讲解流程（原有接口，保持兼容：采集 → 生成 → 播报）"""
        await self._run_guide_once()

    async def run_guide(self, max_rounds: int = 3):
        """完整导游流程：讲解一次 +（可选）多轮语音问答。

        讲解结束后自动进入「聆听」状态，游客提问就回答，
        连续静默（没听到提问）则结束对话、回到待机。

        当前 ENABLE_CONVERSATION=False（问答模块冻结）：讲解完即回待机，
        即「一按一讲」模式，不涉及语音识别。
        """
        # 1. 清空上一轮对话历史，避免串上下文
        llm = getattr(self.orchestrator, "llm", None)
        if llm and hasattr(llm, "reset_history"):
            llm.reset_history()

        # 2. 先讲解一次
        await self._run_guide_once()

        # 3. 讲解中被打断则直接返回，不再进入问答
        if self._interrupt_flag:
            self._interrupt_flag = False
            return

        # 4. 连续问答模块已冻结（等麦克风硬件），解冻见模块常量说明
        if not ENABLE_CONVERSATION:
            return

        # 5. 进入问答循环
        await self._conversation_loop(max_rounds)

    async def _run_guide_once(self):
        """讲解一次：采集 → 生成 → 播报，异常自动恢复"""
        # ========== 状态校验：非待机状态不重复执行 ==========
        if self.state != DeviceState.IDLE:
            # 正在播报时按键，触发中断
            self._interrupt_flag = True
            return

        try:
            # ========== 状态1：采集中 ==========
            self.state = DeviceState.ACQUIRING
            print(f"[状态] {self.state.value}：正在获取位置与画面...")

            # 并发获取定位+场景
            position, scene = await self.orchestrator.acquire_all()

            # ========== 状态2：生成中 ==========
            self.state = DeviceState.GENERATING
            print(f"[状态] {self.state.value}：正在生成讲解...")

            # 生成讲解文案
            script = await self.orchestrator.generate_script(position, scene)

            # ========== 状态3：播报中 ==========
            self.state = DeviceState.SPEAKING
            print(f"[状态] {self.state.value}：正在播报...")

            # 播放语音
            await self.orchestrator.play_script(script)

            # ========== 流程结束，回到待机 ==========
            self.state = DeviceState.IDLE
            if self._interrupt_flag:
                print("[状态] idle：播报被打断，返回待机")
            else:
                print("[状态] idle：讲解完成，返回待机")

        except Exception as e:
            # 异常处理：任何错误都不卡死，自动回到待机
            self.state = DeviceState.ERROR
            print(f"[错误] 流程异常：{str(e)}")
            await asyncio.sleep(2)
            self.state = DeviceState.IDLE

    async def _conversation_loop(self, max_rounds: int):
        """问答循环：聆听 → 生成回答 → 播报 → 再聆听，静默则结束"""
        try:
            for _ in range(max_rounds):
                # ========== 状态：聆听中 ==========
                self.state = DeviceState.LISTENING
                print(f"[状态] {self.state.value}：正在聆听游客提问...")

                result = await self.orchestrator.listen_user()

                # 静默 / 没听到有效语音 → 结束对话
                if not result.success or result.is_silence or not result.text.strip():
                    print("[状态] 游客没有提问，结束对话")
                    break

                print(f"[语音识别] 游客说：{result.text}")

                # ========== 状态：生成回答 ==========
                self.state = DeviceState.GENERATING
                print(f"[状态] {self.state.value}：正在生成回答...")

                reply = await self.orchestrator.generate_reply(result.text)

                # ========== 状态：播报回答 ==========
                self.state = DeviceState.SPEAKING
                print(f"[状态] {self.state.value}：正在播报回答...")

                await self.orchestrator.play_script(reply)

                # 播报回答中被按键打断：结束整个对话，回待机
                if self._interrupt_flag:
                    self._interrupt_flag = False
                    print("[打断] 回答播报被按键打断，结束对话")
                    break

        except Exception as e:
            print(f"[错误] 问答流程异常：{str(e)}")
        finally:
            self.state = DeviceState.IDLE
            print("[状态] idle：对话结束，返回待机")

    def trigger(self):
        """
        外部按键触发入口（GPIO 回调）。

        - 待机：受理——放「受理」提示音并启动完整导游流程（讲解 + 问答）
        - 播报中：打断当前播报，回到待机
        - 其他忙碌状态（采集/生成/聆听）：忽略
        """
        if self.state == DeviceState.SPEAKING:
            self._interrupt_flag = True
            asyncio.create_task(self._interrupt_speaking())
            return
        if self.state != DeviceState.IDLE:
            # 采集/生成中按了：给反馈音，避免用户以为没按到而重复按
            play_tones(BUSY_TONES)
            return
        self._interrupt_flag = False
        print("[按键] 已受理：开始采集位置与画面")
        play_tones(RECEIVED_TONES)
        if self.speak_fn:
            # 即时语音安抚：用户知道"听见了、在干活"，填补识图+生成前的空窗
            try:
                self.speak_fn(random.choice(RECEIVED_VOICE))
            except Exception as exc:                           # noqa: BLE001
                log.warning("受理语音提示调度失败：%s", exc)
        asyncio.create_task(self.run_guide())

    async def _interrupt_speaking(self):
        """打断当前播报（播报中再按按键）：停 TTS、放提示音。

        打断 = 停止本次播放回待机（非暂停续播，也非重启进程），
        日志记一条明确的「打断」，后续按 IDLE 路径可立即开新讲解。
        """
        tts = getattr(self.orchestrator, "tts", None)
        if tts and hasattr(tts, "stop"):
            try:
                await tts.stop()
            except Exception:
                pass
        print("[打断] 已停止当前播报（按键打断）")
        play_tones(INTERRUPT_TONES)
