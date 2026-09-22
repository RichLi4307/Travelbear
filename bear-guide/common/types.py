from dataclasses import dataclass
from enum import Enum

# ==================== 设备全局状态枚举 ====================
class DeviceState(Enum):
    """设备所有可能的状态，同一时间只能处于一个状态"""
    IDLE = "idle"              # 待机：等待用户按键
    ACQUIRING = "acquiring"    # 采集中：并行获取定位+图像
    GENERATING = "generating"  # 生成中：调用大模型生成讲解文案
    SPEAKING = "speaking"      # 播报中：语音播放中
    LISTENING = "listening"    # 聆听中：等待游客语音提问
    ERROR = "error"            # 错误：异常状态，自动恢复

# ==================== 定位模块返回结构 ====================
@dataclass
class PositionInfo:
    """定位模块统一返回格式"""
    success: bool               # 核心：定位是否成功
    latitude: float = 0.0       # 纬度
    longitude: float = 0.0      # 经度
    poi_name: str = ""          # POI名称（例：上海大学宝山校区图书馆）
    address: str = ""           # 详细地址
    is_indoor: bool = False     # 室内/室外定位标记
    error_msg: str = ""         # 失败时填写错误原因，成功则为空

# ==================== 识图模块返回结构 ====================
@dataclass
class SceneInfo:
    """图像识别模块统一返回格式"""
    success: bool               # 核心：识别是否成功
    scene_description: str = "" # 场景文字描述（云端VLM输出）
    confidence: float = 0.0     # 识别置信度 0~1
    error_msg: str = ""         # 错误信息

# ==================== 语音模块返回结构 ====================
@dataclass
class PlayStatus:
    """语音播报模块统一返回格式"""
    success: bool               # 核心：播放是否成功
    is_playing: bool = False    # 是否正在播放
    error_msg: str = ""         # 错误信息

# ==================== 语音识别（ASR）模块返回结构 ====================
@dataclass
class ASRResult:
    """语音识别模块统一返回格式"""
    success: bool               # 核心：识别是否成功
    text: str = ""              # 识别出的文字（游客说的话）
    is_silence: bool = False    # 是否静默（没听到有效语音）
    error_msg: str = ""         # 错误信息
