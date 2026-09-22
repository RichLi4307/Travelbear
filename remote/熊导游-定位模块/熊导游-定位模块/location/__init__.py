"""定位模块（室外 GPS 围栏 + 室内蓝牙双模）。

Agent 侧只需要：

    from location import get_location, start
    start()                    # 主程序启动时调一次，把串口和蓝牙扫起来
    ...
    loc = get_location()       # 按键时调，非阻塞

``get_location()`` 返回：
    {
        "mode": "gps" | "ble" | "none",
        "poi_name": str,       # "泮池" / "上海大学（宝山校区）" / "三号展厅-青铜器展位"
        "lat": float | None,   # WGS84 原始坐标（调试用）
        "lon": float | None,
        "beacon_id": str | None,
        "confidence": float,   # 0~1，0.95=景区内点位 0.85=景区 0.7=在线/信标 0.5=只有坐标
        "timestamp": str,
    }

室外位置命名是分层的，见 switch.py 的模块说明。

本包 import 时不依赖任何第三方库；pyserial / pynmea2 / bleak / PyYAML 都是按需延迟导入。
"""
from .contract import BeaconHit, Fix, Location
from .geofence import GeofenceIndex, ScenicArea, Spot, haversine_m
from .location import get_location, start, stop

__all__ = ["get_location", "start", "stop", "Fix", "BeaconHit", "Location",
           "GeofenceIndex", "ScenicArea", "Spot", "haversine_m"]
