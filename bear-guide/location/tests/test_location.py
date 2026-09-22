import asyncio
import os
import sys

# ========== 根据脚本自身绝对路径计算项目根目录 ==========
# 获取当前脚本的绝对路径
current_file = os.path.abspath(__file__)
# 往上三级：tests → location → bear-guide（项目根目录）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(current_file)))
# 把项目根目录加入Python搜索路径，保证任意目录运行都不报错
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from location.mock_location import MockLocation


async def test_location():
    print("========== 定位模块Mock测试开始 ==========")

    # 初始化 Mock 定位模块
    location = MockLocation()

    # 调用接口方法
    result = await location.get_position(timeout=5.0)

    # 打印返回对象的所有字段
    print(f"success     : {result.success}")
    print(f"latitude    : {result.latitude}")
    print(f"longitude   : {result.longitude}")
    print(f"poi_name    : {result.poi_name}")
    print(f"address     : {result.address}")
    print(f"is_indoor   : {result.is_indoor}")
    print(f"error_msg   : {result.error_msg}")

    print("========== 定位模块Mock测试通过 ==========")


if __name__ == "__main__":
    asyncio.run(test_location())
