import asyncio
from common.types import SceneInfo
from vision.vision_interface import VisionInterface

class MockVision(VisionInterface):
    """模拟识图模块，用于本地调试"""
    async def get_scene(self, timeout: float = 8.0) -> SceneInfo:
        # 模拟云端API延迟2秒
        await asyncio.sleep(2.0)
        return SceneInfo(
            success=True,
            scene_description="一座现代风格的图书馆建筑，门前有广场和校训石",
            confidence=0.92
        )
