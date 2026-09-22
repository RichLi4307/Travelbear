from abc import ABC, abstractmethod
from common.types import SceneInfo

class VisionInterface(ABC):
    """图像识别模块抽象接口"""

    @abstractmethod
    async def get_scene(self, timeout: float = 8.0) -> SceneInfo:
        """
        获取当前场景描述
        :param timeout: 超时时间，单位秒
        :return: SceneInfo 场景识别结果对象
        """
        pass
