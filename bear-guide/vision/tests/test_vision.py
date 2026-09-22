import asyncio
import os
import sys

# ========== 根据脚本自身绝对路径计算项目根目录 ==========
# 获取当前脚本的绝对路径
current_file = os.path.abspath(__file__)
# 往上三级：tests → vision → bear-guide（项目根目录）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
# 把项目根目录加入Python搜索路径，保证任意目录运行都不报错
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from vision.mock_vision import MockVision


async def test_vision():
    print("========== 识图模块Mock测试开始 ==========")

    # 初始化 Mock 识图模块
    vision = MockVision()

    # 调用接口方法
    result = await vision.get_scene(timeout=8.0)

    # 打印返回对象的所有字段
    print(f"success           : {result.success}")
    print(f"scene_description : {result.scene_description}")
    print(f"confidence        : {result.confidence}")
    print(f"error_msg         : {result.error_msg}")

    print("========== 识图模块Mock测试通过 ==========")


if __name__ == "__main__":
    asyncio.run(test_vision())
