import asyncio
from common.types import PositionInfo
from location.location_interface import LocationInterface

class MockLocation(LocationInterface):
    """模拟定位模块，用于本地调试，不依赖硬件"""
    async def get_position(self, timeout: float = 5.0) -> PositionInfo:
        # 模拟硬件IO延迟1.5秒，真实感受流程
        await asyncio.sleep(1.5)
        # 返回模拟的上海大学校园定位数据
        return PositionInfo(
            success=True,
            latitude=31.3167,
            longitude=121.3920,
            poi_name="上海大学宝山校区图书馆",
            address="上海市宝山区上大路99号",
            is_indoor=False
        )
