"""让 `pytest` 在仓库根目录直接跑起来时能 import 到 location 包。

pytest 会把 conftest.py 所在目录加进 sys.path，所以这个文件放在仓库根目录。
"""
