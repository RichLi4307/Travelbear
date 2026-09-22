"""
tests/test_vision.py —— 识图模块独立自测
用法：
  python tests/test_vision.py                    # 用 test.jpg 测试
  python tests/test_vision.py 你的图片.jpg        # 指定图片测试
  python tests/test_vision.py camera             # 调摄像头测试
"""
import asyncio
import sys
from pathlib import Path

# 把项目根目录加入 path，方便 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from vision.vision_qwen_vl import QwenVLVision


async def test_vision(image_source: str):
    print(f"测试图像源: {image_source}")
    print("-" * 50)

    vision = QwenVLVision(image_source=image_source)
    result = await vision.get_scene(timeout=8.0)

    print(f"success:         {result.success}")
    print(f"scene_description: {result.scene_description}")
    print(f"confidence:      {result.confidence}")
    print(f"error_msg:       {result.error_msg}")

    if result.success:
        print("\n✅ 识别成功")
    else:
        print(f"\n❌ 识别失败: {result.error_msg}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        img = sys.argv[1]
    else:
        img = "test.jpg"

    asyncio.run(test_vision(img))
