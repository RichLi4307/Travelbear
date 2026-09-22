from common.types import PositionInfo
from location.location_interface import LocationInterface


class LocationAdapter(LocationInterface):
    """把同事的定位模块（同步 dict）适配成 Agent 契约（async PositionInfo）。

    同事的 get_location() 返回 dict，字段是 mode/poi_name/lat/lon/confidence，
    和契约里的 PositionInfo（success/latitude/longitude/poi_name/...）对不上，
    这里做一层翻译。定位本身 <5ms 非阻塞，同步调用不会卡住事件循环。
    """

    def __init__(self):
        # 启动定位模块的后台线程（串口读 GNSS + 蓝牙扫信标）
        from vendor.location import start
        start()

    async def get_position(self, timeout: float = 5.0) -> PositionInfo:
        from vendor.location import get_location

        loc = get_location()  # 同步、非阻塞，实测 <5ms

        mode = loc.get("mode", "none")
        if mode == "none":
            return PositionInfo(
                success=False,
                poi_name="定位不可用",
                error_msg="定位模块返回 none（无 GPS 无信标）",
            )

        confidence = loc.get("confidence", 0.0)
        if confidence < 0.5:
            # 同事的约定：confidence < 0.5 就是"只有坐标认不出地方"，别播
            return PositionInfo(
                success=False,
                poi_name=loc.get("poi_name", ""),
                error_msg=f"定位置信度不足（{confidence}）",
            )

        return PositionInfo(
            success=True,
            latitude=loc.get("lat") or 0.0,
            longitude=loc.get("lon") or 0.0,
            poi_name=loc.get("poi_name", ""),
            address="",                              # 同事没有这个字段
            is_indoor=(mode == "ble"),               # 蓝牙定位 = 室内
        )
