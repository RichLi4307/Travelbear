# -*- coding: utf-8 -*-
"""生成阶段慢响应保护测试：中途安抚一次 + 最终超时转兜底。

直跑：python agent/tests/test_slow_generate.py（无需 key / 硬件）
"""
import asyncio
import os
import sys

current_file = os.path.abspath(__file__)
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import agent.state_machine as sm
from agent.state_machine import AgentStateMachine
from common.types import DeviceState, PositionInfo, SceneInfo


class MockOrchestrator:
    """只实现 _run_guide_once 用到的三个方法。"""

    def __init__(self, gen_delay: float):
        self._gen_delay = gen_delay

    async def acquire_all(self):
        return (PositionInfo(success=False, poi_name=""),
                SceneInfo(success=False))

    async def generate_script(self, position, scene):
        await asyncio.sleep(self._gen_delay)
        return "测试文案"

    async def play_script(self, text):
        pass


async def case_soothe():
    """生成 7 秒（>6s 阈值）：安抚触发一次，最终正常播报。"""
    agent = AgentStateMachine(MockOrchestrator(gen_delay=7.0))
    voices = []
    agent.speak_fn = voices.append
    await agent._run_guide_once()
    soothed = [v for v in voices if "想想" in v or "想一想" in v]
    assert agent.state == DeviceState.IDLE, f"状态未回待机：{agent.state}"
    assert len(soothed) == 1, f"安抚应触发 1 次，实际 {len(soothed)}：{voices}"
    print("✅ 中途安抚：7s 生成触发 1 次安抚，流程正常完成")


async def case_fast_no_soothe():
    """生成 0.2 秒（正常）：不触发安抚。"""
    agent = AgentStateMachine(MockOrchestrator(gen_delay=0.2))
    voices = []
    agent.speak_fn = voices.append
    await agent._run_guide_once()
    assert not voices, f"快响应不应有语音，实际：{voices}"
    print("✅ 快响应：2s 内完成，无安抚语音")


async def case_timeout():
    """生成 100 秒（远超 30s 上限）：超时转本地兜底，抱歉语音优先于安抚。"""
    old_soothe, old_timeout = sm.GENERATING_SOOTHE_S, sm.GENERATING_TIMEOUT_S
    sm.GENERATING_SOOTHE_S, sm.GENERATING_TIMEOUT_S = 0.3, 0.6
    try:
        agent = AgentStateMachine(MockOrchestrator(gen_delay=100.0))
        voices = []
        agent.speak_fn = voices.append
        await agent._run_guide_once()
        assert agent.state == DeviceState.IDLE, f"状态未回待机：{agent.state}"
        assert len(voices) == 2, f"应是 安抚+抱歉 各 1 条：{voices}"
        assert "想一想" in voices[0] or "想想" in voices[0], f"第 1 条应是安抚：{voices}"
        assert "抱歉" in voices[1], f"第 2 条应是抱歉：{voices}"
        print("✅ 超时兜底：安抚在先抱歉在后各 1 条，正常回待机")
    finally:
        sm.GENERATING_SOOTHE_S, sm.GENERATING_TIMEOUT_S = old_soothe, old_timeout


async def main():
    await case_fast_no_soothe()
    await case_soothe()
    await case_timeout()
    print("\n慢响应保护测试全部通过（3 条）")


if __name__ == "__main__":
    asyncio.run(main())
