# 项目骨架（SKELETON）

> 完整目录结构 + 每个文件的职责说明。

## 一、完整目录树

```
bear-guide/
├── common/
│   ├── __init__.py
│   └── types.py                 # 状态枚举 + 三种数据结构（不可改）
├── agent/                       # 核心调度大脑
│   ├── __init__.py
│   ├── main.py                  # 程序入口
│   ├── state_machine.py         # 有限状态机
│   ├── orchestrator.py          # 任务编排器
│   ├── prompt_builder.py        # 提示词拼装
│   ├── dashscope_llm.py         # DashScope LLM 客户端
│   └── tests/
│       ├── test_full_flow.py    # Mock 全链路测试
│       └── test_mock2.py        # 集成测试（真实 TTS + 适配定位 + LLM）
├── location/                    # 定位模块
│   ├── __init__.py
│   ├── location_interface.py    # 定位接口（契约，不可改）
│   ├── mock_location.py         # 假实现（本地调试）
│   ├── location_adapter.py      # 适配器：同事 dict → PositionInfo
│   └── tests/test_location.py
├── vision/                      # 识图模块
│   ├── __init__.py
│   ├── vision_interface.py      # 识图接口（契约，不可改）
│   ├── mock_vision.py           # 假实现（本地调试）
│   └── tests/test_vision.py
├── tts/                         # 语音模块
│   ├── __init__.py
│   ├── tts_interface.py         # 语音接口 + 适配层 + 单例入口
│   ├── tts.py                   # 真引擎（小米 MiMo，同事交付）
│   └── mock_tts.py              # 假实现（已退役，待删）
├── vendor/                      # 第三方模块隔离区
│   ├── __init__.py
│   └── location/                # 同事交付的定位模块（完整包）
│       ├── location.py          # 唯一入口 get_location()
│       ├── gnss.py              # GPS 串口读取
│       ├── ble_scan.py          # 蓝牙信标扫描
│       ├── switch.py            # 定位决策状态机
│       ├── geofence.py          # 本地景区围栏
│       ├── geocode.py           # 高德逆地理编码
│       ├── contract.py          # 内部数据契约
│       ├── scenic_areas.yaml    # 景区坐标配置
│       ├── beacons.yaml         # 蓝牙信标配置
│       └── tools/               # 调试工具（体检/校准/模拟）
├── .env                         # 真实密钥（不提交 git）
├── .env.example                 # 密钥模板
├── requirements.txt             # 依赖清单
├── .gitignore
├── 接口规范.txt                  # 模块接口契约 v1.0
├── a.md                         # README（项目说明）
└── b.md                         # SKELETON（本文件）
```

## 二、核心文件职责说明

### common/（公共数据类型，全项目共享）

| 文件 | 职责 |
|---|---|
| `types.py` | `DeviceState` 状态枚举 + `PositionInfo`/`SceneInfo`/`PlayStatus` 三个 dataclass |

### agent/（核心调度大脑）

| 文件 | 职责 |
|---|---|
| `state_machine.py` | 有限状态机，控制 `IDLE → ACQUIRING → GENERATING → SPEAKING → IDLE`，异常自动恢复 |
| `orchestrator.py` | 任务编排：并发定位+识图、调 LLM 生成文案、调 TTS 播报 |
| `prompt_builder.py` | 把位置+场景拼装成导游提示词模板 |
| `dashscope_llm.py` | DashScope 客户端，无 Key 或调用失败时降级返回占位文案 |
| `main.py` | 程序入口，初始化模块并启动状态机 |

### location/（定位）

| 文件 | 职责 |
|---|---|
| `location_interface.py` | 契约：`async get_position() -> PositionInfo` |
| `mock_location.py` | 假实现，返回上海大学图书馆，本地调试用 |
| `location_adapter.py` | 适配器：把 `vendor/location` 的 dict 翻译成 `PositionInfo` |

### vision/（识图）

| 文件 | 职责 |
|---|---|
| `vision_interface.py` | 契约：`async get_scene() -> SceneInfo` |
| `mock_vision.py` | 假实现，返回图书馆场景，本地调试用 |

### tts/（语音）

| 文件 | 职责 |
|---|---|
| `tts_interface.py` | 契约 `TTSInterface` + 适配层 `TTSAdapter` + 单例入口 `get_tts()` |
| `tts.py` | 真引擎：小米 MiMo 云端合成 + 句级流水线 + 打断 + 缓存 + 容错 |
| `mock_tts.py` | 假实现（只打印不出声），已退役待删 |

### vendor/location/（同事交付的定位模块，隔离存放）

| 文件 | 职责 |
|---|---|
| `location.py` | 唯一入口，`start()` 启动后台线程、`get_location()` 返回 dict |
| `gnss.py` | 常驻读 GPS 串口 |
| `ble_scan.py` | 常驻扫蓝牙信标 |
| `switch.py` | 决策：综合 GPS + 蓝牙 + 围栏选出最优位置 |
| `geofence.py` | 本地景区围栏匹配（零延迟） |
| `geocode.py` | 高德逆地理编码（坐标→地名，室外兜底） |
| `tools/` | 调试工具：`amap_check.py` 验 Key、`nmea_recorder.py` 现场采点等 |

## 三、接口契约速查

| 模块 | 抽象基类 | 必须实现的异步方法 | 返回类型 |
|---|---|---|---|
| 定位 | `LocationInterface` | `get_position(timeout=5.0)` | `PositionInfo` |
| 识图 | `VisionInterface` | `get_scene(timeout=8.0)` | `SceneInfo` |
| 语音 | `TTSInterface` | `speak(text, interrupt=False)` | `PlayStatus` |

> 完整契约见 `接口规范.txt`。替换任何模块时，只需继承对应接口、实现同名方法，Agent 核心零改动。
