import asyncio
import sys
import os

# ========== 修正：根据脚本自身位置计算项目根目录 ==========
# 获取当前脚本的绝对路径
current_file = os.path.abspath(__file__)
# 往上两级：tests → agent → bear-guide（项目根目录）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
# 把项目根目录加入Python搜索路径
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from location.mock_location import MockLocation
from vision.mock_vision import MockVision
from tts.mock_tts import MockTTS
from agent.orchestrator import Orchestrator
from agent.state_machine import AgentStateMachine

async def test_full_flow():
    print("========== 全链路Mock测试开始 ==========")

    location = MockLocation()
    vision = MockVision()
    tts = MockTTS()
    orchestrator = Orchestrator(location, vision, tts)
    agent = AgentStateMachine(orchestrator)

    await agent.run_once()

    print("========== 全链路Mock测试通过 ==========")

if __name__ == "__main__":
    asyncio.run(test_full_flow())
