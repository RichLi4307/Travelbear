"""
common/types.py —— 全项目通用数据类型定义
所有模块返回值必须使用这里定义的类型，不许自行修改。
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SceneInfo:
    """识图模块返回值"""
    success: bool = False
    scene_description: str = ""
    confidence: float = 0.0
    error_msg: str = ""


@dataclass
class PositionInfo:
    """定位模块返回值"""
    success: bool = False
    latitude: float = 0.0
    longitude: float = 0.0
    poi_name: str = ""
    address: str = ""
    is_indoor: bool = False
    error_msg: str = ""


@dataclass
class PlayStatus:
    """语音模块返回值"""
    success: bool = False
    is_playing: bool = False
    error_msg: str = ""
