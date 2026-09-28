# -*- coding: utf-8 -*-
"""熊导游 Agent 主入口。

运行方式：
    python agent/main.py            # 真实模式：GPIO 按键监听 + 真实 ASR（树莓派部署用）
    python agent/main.py --demo     # 演示模式：自动跑一次讲解（连续问答已冻结，ASR 用 Mock）

真实模式 vs 演示模式：
    真实模式   问答模块冻结（一按一讲），按键用 GPIO（开发机回车模拟）
    演示模式   ASR 用 MockASR（不接麦克风），自动跑一次讲解，方便看效果
"""

import argparse
import asyncio
import logging
import os
import sys

# ========== 根据脚本自身绝对路径计算项目根目录 ==========
current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(current_file))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# ========== 加载项目根目录的 .env（注入 MIMO_API_KEY / DASHSCOPE_API_KEY 等） ==========
def _load_env(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_env(os.path.join(project_root, ".env"))

# 压制定位同事模块「无硬件重连」的告警刷屏（ERROR 及以上的真实故障仍保留）
for _log_name in ("vendor.location.gnss", "vendor.location.ble_scan"):
    logging.getLogger(_log_name).setLevel(logging.ERROR)


def _setup_logging(debug: bool = False) -> None:
    """配置根日志（等级 + 格式）。各模块日志经各自 logger 输出。

    默认 INFO：正常事件（请求/结果/耗时/降级）；--debug 时 DEBUG：附加全文载荷。
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

from location.location_adapter import LocationAdapter   # 真实定位（同事代码 + 适配层）
from vision.vision_qwen_vl import QwenVLVision          # 真实识图（同事交付，Qwen-VL-Max）
from tts.tts_interface import get_tts                   # 真实 TTS（同事代码）
from agent.orchestrator import Orchestrator
from agent.state_machine import AgentStateMachine, ENABLE_CONVERSATION
from agent.dashscope_llm import DashScopeLLM            # 真实 LLM（DashScope）
from agent.button import ButtonMonitor                  # 按键监听（GPIO / 键盘）
from agent.service import GuideService                  # 导览服务锁（长按解锁/上锁）
from agent.volume import volume_up, volume_down         # 音量加/减（amixer）
from asr.dashscope_asr import DashScopeASR              # 真实 ASR（Paraformer，待麦克风）
from asr.mock_asr import MockASR                        # 演示用 ASR（不接麦克风）


def _build_agent(use_mock_asr: bool):
    """初始化五个模块 + 编排器 + 状态机，返回 agent 实例。"""
    location = LocationAdapter()                          # 真实定位（无硬件自动降级）
    vision = QwenVLVision(image_source="camera")          # 真实识图（开发机无摄像头可改本地图）
    tts = get_tts()                                       # 真实 TTS（必须在事件循环内调用）
    llm = DashScopeLLM()                                  # 真实 LLM
    # ASR：演示用 Mock；问答模块冻结时不接 ASR（None 即静默）；解冻后恢复 DashScopeASR
    if use_mock_asr:
        asr = MockASR()
    elif ENABLE_CONVERSATION:
        asr = DashScopeASR()
    else:
        asr = None

    orchestrator = Orchestrator(location, vision, tts, llm_client=llm, asr=asr)
    return AgentStateMachine(orchestrator)


async def main():
    parser = argparse.ArgumentParser(description="熊导游 Agent")
    parser.add_argument("--demo", action="store_true", help="演示模式：自动跑一次讲解（连续问答已冻结，ASR 用 Mock）")
    parser.add_argument("--debug", action="store_true", help="日志等级降为 DEBUG（输出请求/播报全文等载荷）")
    args = parser.parse_args()

    _setup_logging(args.debug)
    agent = _build_agent(use_mock_asr=args.demo)
    print("=== 熊导游 Agent 启动完成 ===")

    if args.demo:
        # 演示模式：自动跑一次完整流程，跑完退出
        await agent.run_guide()
        print("=== 演示结束 ===")
        return

    # 真实模式：监听按键。服务默认上锁，长按功能键 2 秒解锁后才能短按讲解
    loop = asyncio.get_running_loop()

    def _speak(text: str) -> None:
        """服务锁的语音提示：把 TTS 调度回主事件循环（可在按键回调线程调用）。"""
        asyncio.run_coroutine_threadsafe(
            agent.orchestrator.tts.speak(text, interrupt=True), loop)

    service = GuideService(speak_fn=_speak)
    agent.speak_fn = _speak            # 状态机受理提示语音与自检语音同一通道
    monitor = ButtonMonitor(
        on_short_press=lambda: service.on_short_press(agent.trigger),
        on_long_press=service.on_long_press,
        on_vol_up=volume_up,
        on_vol_down=volume_down,
    )
    monitor.start()
    # 保持事件循环常驻，等待按键触发
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
