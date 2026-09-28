# 定位模块：执行手册（vendor 副本，2026-09-28 整合修订）

> 本文件对应 `vendor/location/`，是定位模块（分工5）交付包整合进熊导游主程序后的
> **当前实际运行版本**。2026-09-28 整合修订：仲裁规则（BLE 最高优先 + 陌生信标过滤）、
> 阈值 -85、树莓派 5 串口事实。原始交付文档见 `remote/熊导游-定位模块/`，
> 分工背景见 `tecs/分工5-定位模块.md`（已归档）。
> 跨模块改动记录见 `tecs/整合期跨模块改动清单.md` 第五节。

---

## 一、这个模块交付什么

一个 `location` 包，对外只有一个函数：

```python
from vendor.location import get_location, start
start()                 # 主程序启动时调一次（后台常驻搜星 + 扫信标）
loc = get_location()    # 按键时调，实测 <5ms
```

```python
{
    "mode": "gps" | "ble" | "none",
    "poi_name": str,      # "泮池" / "上海大学（宝山校区）" / "三号展厅-青铜器展位"
    "lat": float | None,  # WGS84 原始坐标（调试用）
    "lon": float | None,
    "beacon_id": str | None,
    "confidence": float,  # 0.95 点位 / 0.85 景区 / 0.70 在线或信标 / 0.50 只有坐标
    "timestamp": str,
}
```

两条硬承诺：**永不抛异常**、**永不阻塞**。Agent 侧经 `location/location_adapter.py` 适配。

---

## 二、定位是怎么判定的（2026-09-28 现行规则）

```
信标有新鲜命中（beacons.yaml 映射出展位名） ──→ ble 「某展位」 conf=0.70
        （无条件最高优先，GPS 含点位级也不插嘴）
        │
        └─ 信标过期 10s 或不在场 → 才轮到 GPS：
              GNSS fix (WGS84)
                 ├─ 1. 本地围栏：景区内点位  "泮池"                ← is_precise（≥6星 HDOP≤5）
                 ├─ 2. 本地围栏：景区        "上海大学（宝山校区）"  ← 宽松（≥4星 HDOP≤10）
                 ├─ 3. 高德在线反解          "XX公园北门"          ← 只在 1、2 都没命中时
                 └─ 4. 兜底                  "当前位置附近"
GPS 无效且信标不在场 → mode="none"
```

**五个设计决定（含 2026-09-28 整合期新增的两条）：**

| 决定 | 为什么 |
|---|---|
| **BLE 最高优先**（负责人拍板） | 信标固定在展位上是米级物理证据；展厅内 GPS 从窗缝漏进来的 fix 会把「某展位」覆盖成「某校区」，讲解当众串味 |
| **只有映射出名字的信标才算数**（实测暴露的漏洞） | 环境里别人的 iBeacon（邻居设备 −69dBm）在阈值放宽后会越线，把 GPS 顶掉、让上层拿到一串 UUID。不认识的不信 |
| **本地围栏做主力，在线反解做兜底** | 本地判定零延迟、零配额、断网可用 |
| **景区级和点位级分两档精度门槛** | 景区半径几百米，误差无所谓；点位半径几十米，必须卡精度 |
| **两条迟滞**（gps↔ble 20s、景区边界 ×1.15） | 防止边界/门口来回走时模式乱跳 |

---

## 三、现在就能测什么

### 1. 仲裁行为（主程序侧，9 条直跑测试）

```bash
cd bear-guide
python location/tests/test_ble_priority.py    # BLE 最高优先 + 陌生信标过滤，9 条
```

交付包自带的 110 条 pytest 在 `remote/熊导游-定位模块/tests/`（交付快照，含
3 条与现行仲裁语义冲突的旧期望，见跨模块清单）。

### 2. 看它"跑起来"——路线模拟器

```bash
cd bear-guide
python vendor/location/tools/simulate_route.py --list     # 6 个场景
python vendor/location/tools/simulate_route.py            # 跑一遍看判定输出
```

它**不 mock 任何业务逻辑**：喂假坐标，走真实的 `GeofenceIndex` + `LocationStateMachine`，
读真实的 `scenic_areas.yaml`。
注意：场景「走进室内」的断言文本基于交付时"GPS 优先"旧语义，输出仅供看判定过程，
现行语义以 `test_ble_priority.py` 为准（人在展厅内永远报展位，信标过期才切 GPS）。

### 3. 验证"永不抛异常"这条硬承诺

```bash
python -c "import sys; sys.path.insert(0,'vendor'); import json; from vendor.location import get_location; print(json.dumps(get_location(), ensure_ascii=False))"
```

### 4. 蓝牙探针（验证信标在线）

```bash
python vendor/location/tools/ble_probe.py    # 扫 iBeacon，打印可粘进 beacons.yaml 的片段
```

### 5. 高德 Key 体检

```bash
export AMAP_WEB_KEY=你的key
python vendor/location/tools/amap_check.py
```

选错 Key 类型会报 `10009`（工具会提示怎么改）；坐标系实测：上海 WGS84 直发偏移
~480m，工具内已做 WGS84→GCJ-02 折算。

---

## 四、配置怎么填

### `vendor/location/scenic_areas.yaml` —— 景区和点位

⚠️ **坐标必须是 WGS84**（高德拾取器给的是 GCJ-02，境内差 300–600m）。
写入 `coord: gcj02` 的条目加载时自动折算；或用 `tools/nmea_recorder.py --pick` 实测取点。

半径：景区 `radius_m` = 中心到最远边界；点位 `radius_m` = 触发半径，一般 20–60m。

### `vendor/location/beacons.yaml` —— 室内信标

1. 布好信标，站在展位正中跑 `python vendor/location/tools/beacon_calibrate.py`
2. 走到展位边界再记一个值
3. `rssi_threshold` 取中间偏保守值回填 **本文件（vendor 副本）**
   ⚠️ 不要改 `remote/` 交付包里的副本——主程序只读 vendor 这份
4. 当前全局 −85：信标无天线（板载），放宽以扩大触发半径；进馆后以"展位边界实测值"精调

---

## 五、上树莓派（本项目事实：Pi5）

**本项目用树莓派 5，与交付文档写的 4B 方案不同：**

- GPS 接 **GPIO14/15**（三根线：5V、GND、GPS.TX→Pin10），对应 **miniUART `/dev/ttyS0`**；
  Pi5 的 PL011 归板载蓝牙（BLE 扫描要用），不用抢。
- 系统一次性配置：`config.txt` 加 `enable_uart=1`；`cmdline.txt` 删除
  `console=serial0,115200`（否则串口被控制台占用）。配置备份 `.bak-gps`。
- `.env` 钉 `GNSS_PORT=/dev/ttyS0`、`GNSS_BAUD=9600`（NEO-M8N 默认输出 GNGGA
  多星座语句，pynmea2 按 sentence_type 匹配，无需配模块）。
- 若移植回 Pi4B：注意 4B 的 GPIO 真串口被蓝牙占用，那才需要 USB GPS（原交付方案）。

```bash
sudo usermod -aG dialout $USER      # 串口免 sudo
rfkill unblock bluetooth            # 板载蓝牙常被 soft-block
```

实测：室内窗边 9 星 / HDOP 2.87，围栏命中「上海大学（宝山校区）」conf 0.85。

---

## 六、文件地图

```
vendor/location/              ← 本模块（同事交付 + 整合期两处仲裁修复）
├── __init__.py               对外暴露 get_location / start / stop
├── contract.py               数据契约 + 两档质量门槛
├── geofence.py               本地围栏 + WGS84/GCJ-02 折算
├── gnss.py                   串口常驻读取 + NMEA 解析
├── ble_scan.py               bleak 扫描 + iBeacon 解析 + RSSI 中值滤波
├── geocode.py                高德在线反解（兜底，可选）
├── switch.py                 仲裁核心：BLE 最高优先 + 陌生信标过滤 + 两条迟滞
├── location.py               get_location() 唯一入口
├── scenic_areas.yaml         景区围栏 + 点位
├── beacons.yaml              信标 → 展位名（阈值 -85，进馆标定）
└── tools/                    标定/探针/模拟工具（直跑，无需 pytest）
    ├── simulate_route.py     零硬件演练整条链路（6 场景）
    ├── ble_probe.py          BLE 探针
    ├── beacon_calibrate.py   现场标定 RSSI
    ├── amap_check.py         高德 Key 体检
    ├── amap_fill_spots.py    高德 POI 自动填点位
    ├── where_am_i.py         实时看自己在围栏里的判定
    └── nmea_recorder.py      NMEA 录制/回放/取点

bear-guide/location/
├── location_adapter.py       vendor dict → Agent PositionInfo 适配层
└── tests/test_ble_priority.py  现行仲裁行为 9 条直跑测试（锁定跨模块修复）
```

配套阅读：
- 仓库根 `README.md`「定位方案」节 —— 项目级定位说明（信标表、仲裁规则）
- `tecs/整合期跨模块改动清单.md` 第五节 —— 对同事代码的两处仲裁修复及理由
