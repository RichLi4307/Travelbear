# 熊导游项目 —— 识图模块（vision）

> 云端 Qwen-VL-Max 多模态大模型视觉识别，符合团队统一接口契约 v1.0。

## 目录结构

```
vision_module/
├── common/
│   ├── __init__.py
│   └── types.py              # 全项目通用数据类型（SceneInfo/PositionInfo/PlayStatus）
├── vision/
│   ├── __init__.py
│   ├── vision_interface.py   # 抽象基类 VisionInterface
│   └── vision_qwen_vl.py     # 云端 Qwen-VL-Max 实现
├── tests/
│   └── test_vision.py        # 独立自测脚本
├── captures/                 # 拍摄图片留存（答辩展示用）
├── .env.example              # 环境变量示例
├── requirements.txt          # 依赖清单
└── README.md
```

## 接口契约（与团队统一）

### 抽象基类

```python
class VisionInterface(ABC):
    async def get_scene(self, timeout: float = 8.0) -> SceneInfo
```

### 返回值 SceneInfo

| 字段 | 类型 | 说明 |
|---|---|---|
| success | bool | 识别是否成功 |
| scene_description | str | 场景文字描述（含地标猜测） |
| confidence | float | 置信度 0~1 |
| error_msg | str | 错误信息，成功时为空 |

### 通用规则

- ✅ 全异步实现（async/await）
- ✅ 内部异常不抛出，封装进 `error_msg`
- ✅ 超时控制（默认 8s）
- ✅ 图片本地留存到 `captures/`

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

复制 `.env.example` 为 `.env`，填入你的 DashScope API Key：

```
DASHSCOPE_API_KEY=sk-你的API_KEY
```

> API Key 获取地址：https://bailian.console.aliyun.com/?apiKey=1#/api-key

### 3. 运行测试

**用本地图片测试（推荐，不需要摄像头）：**
```bash
python tests/test_vision.py test.jpg
```

**调摄像头实时测试：**
```bash
python tests/test_vision.py camera
```

## 使用示例

```python
import asyncio
from vision.vision_qwen_vl import QwenVLVision

async def main():
    # 用本地图片调试
    vision = QwenVLVision(image_source="test.jpg")

    # 上树莓派后改成摄像头
    # vision = QwenVLVision(image_source="camera")

    result = await vision.get_scene(timeout=8.0)

    if result.success:
        print(f"场景: {result.scene_description}")
        print(f"置信度: {result.confidence}")
    else:
        print(f"失败: {result.error_msg}")

asyncio.run(main())
```

## 限定识别景点

当前限定识别以下 4 个景点（在 `vision_qwen_vl.py` 的 `TARGET_LANDMARKS` 中配置）：

- 外滩
- 静安寺
- 松江广富林
- 滴水湖

如需修改，直接改 `TARGET_LANDMARKS` 列表即可。

## 硬件要求

| 硬件 | 说明 |
|---|---|
| 树莓派 4B 8G | 主控 |
| USB 摄像头 / CSI 摄像头 | 图像采集（USB 直接用，CSI 需 picamera2） |
| 网络（Wi-Fi / 4G） | 调云端 API 必须联网 |

## 里程碑对齐

| 时间 | 交付 |
|---|---|
| D2–D3 | hello world：本地图片 → VLM 返回描述 |
| D4–D5 | 预处理管线 + SceneInfo 结构化输出 |
| D6–D7 | 提示词调优 + 超时/异常处理 |
| D8–D9 | 上树莓派：摄像头采集验证 |
| D10–D12 | 与 Agent 联调字段口径 |
| D13–D14 | 实地测试：逆光/弱光识别稳定性 |

## 边界与注意

- 输出的是"素材"（场景描述），最终讲解词由 Agent 模块生成
- `landmark_guess` 拿不准就留空，避免张冠李戴
- 每张照片本地留存，既是调试证据也是答辩素材
- 弱网超时返回 `success=False`，不抛异常卡死主链路
