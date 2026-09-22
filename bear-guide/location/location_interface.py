from abc import ABC, abstractmethod
from common.types import PositionInfo

class LocationInterface(ABC):
    """定位模块抽象接口，所有定位实现必须继承本类"""

    @abstractmethod
    async def get_position(self, timeout: float = 5.0) -> PositionInfo:
        """
        获取当前位置
        :param timeout: 超时时间，单位秒
        :return: PositionInfo 定位结果对象
        """
        pass
