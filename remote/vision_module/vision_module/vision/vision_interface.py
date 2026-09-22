"""
vision/vision_interface.py —— 识图模块抽象基类
所有识图实现必须继承本类并实现 get_scene 方法。
"""
from abc import ABC, abstractmethod
from common.types import SceneInfo


class VisionInterface(ABC):
    """识图模块抽象基类"""

    @abstractmethod
    async def get_scene(self, timeout: float = 8.0) -> SceneInfo:
        """
        采集一帧并返回场景描述。

        Args:
            timeout: 超时时间（秒），默认 8.0

        Returns:
            SceneInfo: 包含 success / scene_description / confidence / error_msg
        """
        pass
