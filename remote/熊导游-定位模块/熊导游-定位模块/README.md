# 具身智能导游伙伴（项目代号：熊导游）

> 一只挂在背包肩带上的硬壳熊形态 AI 导游。游客按一下它，它知道自己站在哪、看清眼前的
> 景物，然后用大模型组织出一段有温度的讲解说给你听。
>
> 一句话差异化：**具身**（长在身上的，不是手机里的）、**主动感知**（看你所看，不是你搜它答）、
> **情感载体**（熊，不是盒子）。

---

## 目录

- [仓库结构](#仓库结构)
- [上手指南](#上手指南)
- [各模块进度](#各模块进度)
- [技术栈总表](#技术栈总表)
- [接口契约](#接口契约)
- [密钥管理](#密钥管理)
- [协作规则](#协作规则)

---

## 仓库结构

```
.
├── location/            ✅ 定位模块（已完成，见 location/README.md）
│   ├── location.py          唯一入口 get_location()
│   ├── contract.py          数据契约（冻结）
│   ├── geofence.py          本地景区围栏
│   ├── gnss.py              串口读 NMEA
│   ├── ble_scan.py          蓝牙信标扫描
│   ├── geocode.py           高德在线反解（兜底）
│   ├── switch.py            三态切换 + 分层命名
│   ├── scenic_areas.yaml    景区与点位配置
│   ├── beacons.yaml         信标与展位名配置
│   └── tools/               本模块专用脚本（标定、探针、模拟器）
│
├── vision/              ⬜ 识图模块（待补）  picamera2 → 云端 VLM → 场景描述
├── tts/                 ⬜ 语音模块（待补）  文本 → 小米 MiMo-TTS / Piper 兜底 → 扬声器
├── agent/               ⬜ 本地 Agent（待补）主状态机，编排全链路
│
├── tests/               仓库级测试目录，文件名带模块前缀避免撞名
│   └── test_location_*.py   110 条，全部无需硬件
├── tools/               仓库级共享脚本（不属任何单个模块）
│   └── audit_python_env.py
├── demo/                ★ 零硬件全链路演示（给别人看 / 录 demo 视频用）
│   └── offline_demo.py
├── docs/                文档
│   ├── 交接说明.md          ★ 拿到模块先看这个：接口、集成、排查表、踩过的坑
│   ├── 演示脚本.md          怎么演、怎么讲、按观众分版本
│   ├── 讲解-定位是怎么工作的.md  写给不懂这块的人
│   ├── 分工5-定位模块.md
│   ├── 采购清单-定位模块.md
│   └── 两人分工-定位模块.md
│
├── requirements.txt     全项目依赖（各模块追加自己的，不要覆盖）
├── pytest.ini           测试配置
├── conftest.py          让 pytest 能找到各模块包
└── .env.example         环境变量模板（复制成 .env 填真值）
```

**约定**：每个模块自包含在自己的目录里 —— 代码、配置、专用脚本、README 都在一起。
这样合并时不会撞车，也能整个目录拷走。

---

## 上手指南

### 1. 环境

```bash
pip install -r requirements.txt
```

### 2. 跑测试

```bash
python -m pytest -q
# 期望：110 passed
```

测试**完全不需要硬件**，全部用假数据和文本回放。

### 3. 零硬件看它跑起来

```bash
python demo/offline_demo.py            # 三幕全链路演示（0.1 秒跑完，无停顿）
python demo/offline_demo.py --acts 1   # 只演一幕（当众演示时一幕一幕跑）
python demo/offline_demo.py --place 静安寺山门

python location/tools/simulate_route.py            # 6 个场景，单独看判定逻辑
python location/tools/simulate_route.py --assert   # 顺便断言（19 项）
```

演示脚本喂假坐标、走真实判定逻辑、读真实配置 —— **笔记本上看到的，就是现场会看到的**。
怎么讲、按观众怎么选版本，见 `docs/演示脚本.md`。

### 4. 验证蓝牙

```bash
python location/tools/ble_probe.py
```

扫得到任何设备就说明蓝牙链路通。安卓手机装 Beacon Simulator 广播 iBeacon
就能把室内链路走完，不用买信标。

### 5. 环境变量

```bash
cp .env.example .env      # 然后填进去
```

`.env` 已在 `.gitignore` 里，**不会进仓库**。千万不要把 Key 写进代码。

---

## 各模块进度

| 模块 | 负责人 | 状态 | 交付物 |
|---|---|---|---|
| 硬件整合 + 运维 | 田晓钟 | 🟡 采购未下单 | Pi 环境、外壳、供电、4G、仓库治理 |
| 定位 | 田晓钟（暂） | ✅ 软件完成，等硬件 | `location/` |
| 语音 | 待定 | ⬜ | `tts/` |
| 识图 | 待定 | ⬜ | `vision/` |
| 本地 Agent | 待定 | ⬜ | `agent/` |

> ⚠️ **演示机能不能跑，取决于最慢的那个模块，不是最快的。** 建议 D8 左右先合一次仓库，
> 哪怕其他模块还是空的。

**接手定位模块的人**：先读 `docs/交接说明.md`。里面有接口契约、集成示例、
一张"现象 → 原因 → 动作"的排查表，以及七条已经踩过的坑。

---

## 技术栈总表

| 层 | 选型 | 备注 |
|---|---|---|
| 语言 | **全栈 Python** | 树莓派生态最全；骨架先行，四人各填函数体 |
| 室外定位 | NEO-M8N GNSS（USB 串口）+ pynmea2 | **走 USB 不用 GPIO UART**，否则和板载蓝牙抢资源 |
| 室外命名 | 本地景区围栏为主，高德 Web API 兜底 | 围栏零延迟零配额；高德只在去陌生地方时用 |
| 室内定位 | Pi 板载蓝牙 + bleak 扫 iBeacon | 取"最近的信标"，不做三点定位 |
| 识图 | picamera2 → 通义千问 Qwen-VL（OpenAI 兼容） | 本地 YOLO 只能分类，讲不了"这是哪座雕塑" |
| 语音合成 | 小米 MiMo-TTS（流式）+ Piper 断网兜底 | |
| 编排 | Python 状态机（idle→定位+识图并行→生成→播报） | |
| 通信 | 商用插卡移动 WiFi（MVP） | 终局方向：SIM7600 4G HAT（自带 GNSS） |
| 外壳 | 3D 打印硬壳熊 + 铝散热背板 + 隐藏风道 | 纯毛绒绝热，锂电贴 60°C 热源有起火风险 |

---

## 接口契约

**D1 冻结，改签名必须通知全员。** 四个模块各自暴露一个干净函数：

```python
# location/location.py
def get_location() -> dict:
    """永不抛异常、永不阻塞（< 200ms）"""
    # {"mode": "gps"|"ble"|"none", "poi_name": str,
    #  "lat": float|None, "lon": float|None,
    #  "beacon_id": str|None, "confidence": float, "timestamp": str}

# vision/vision.py
def describe_scene() -> dict: ...

# tts/tts.py
def speak(text: str) -> None: ...

# agent/main.py
def main() -> None: ...      # 状态机总入口
```

定位模块的完整契约（含两档质量门槛的设计理由）见 `location/contract.py` 与
`location/README.md`。

---

## 密钥管理

四家 Key 统一走环境变量，**绝不写进代码**：

| 变量 | 用途 | 必需？ |
|---|---|---|
| `AMAP_WEB_KEY` | 高德 Web 服务（逆地理编码兜底） | 可选 —— 不填也能跑，只是去陌生地方认不出地名 |
| `DASHSCOPE_API_KEY` | 阿里云通义千问（识图 + 文案） | 识图模块必需 |
| `XIAOMI_MIMO_KEY` | 小米 MiMo-TTS | 语音模块必需 |
| `GNSS_PORT` | 串口设备（`/dev/ttyUSB0` 或 `COM5`） | Pi 上建议显式设置 |

`.env` 已被 `.gitignore` 排除。**Key 一旦进过 Git 历史，就算删掉也还留在历史里 ——
必须去控制台重置。**

---

## 协作规则

1. **接口契约 D1 冻结**，改签名必须通知全员
2. **main 分支保护**：禁止直接 push，必须走 PR；合并权归整合者（合并权 = 部署权）
3. **每人必须能独立自测**：定位模块 `python -m pytest -q`，识图/语音同理。
   主链路从 D8 起就该是"活的"，只是喂 mock
4. **每日同步只用文字，三句话**：做完啥 / 卡在哪 / 明天干啥。会议留给 D1、D10、D13
5. **跨模块改动提 PR**，不要直接改别人的目录
