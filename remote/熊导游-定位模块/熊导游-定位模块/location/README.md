# 定位模块：单人版执行手册

> 配套：`docs/分工5-定位模块.md`（原始分工）、`采购清单-定位模块.md`（买什么）。
> 本文是**当前实际执行的方案**：一个人做，室外用本地围栏识别景区与点位，室内用蓝牙信标，不确定的地方才回落高德在线反解。

---

## 一、这个模块交付什么

一个 `location/` 包，对外只有一个函数：

```python
from location import get_location, start
start()                 # 主程序启动时调一次
loc = get_location()    # 按键时调
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

两条硬承诺：**永不抛异常**、**永不阻塞**（目标 < 200ms）。Agent 可以无脑调。

---

## 二、定位是怎么判定的

```
GNSS fix (WGS84)
   │
   ├─ 1. 本地围栏：景区内点位    "泮池"                 ← scenic_areas.yaml，0 延迟
   │      要求 is_precise（≥6 星，HDOP ≤5）
   │
   ├─ 2. 本地围栏：景区          "上海大学（宝山校区）"   ← 同样离线，要求宽松（≥4 星，HDOP ≤10）
   │
   ├─ 3. 高德在线反解            "XX公园北门"           ← 只在 1、2 都没命中时才问
   │
   └─ 4. 兜底                    "当前位置附近"         ← 有坐标但认不出地方

无有效 fix → 蓝牙信标 → 都没有 → mode="none"
```

**四个设计决定，都是有原因的：**

| 决定 | 为什么 |
|---|---|
| **本地围栏做主力，在线反解做兜底** | 本地判定零延迟、零配额、断网可用；演示现场断网也不会翻车。高德只在去陌生地方时才用得上 |
| **景区级和点位级分两档精度门槛** | 景区半径 900m，GPS 误差几米无所谓；点位半径 40m，就必须卡精度。分档之后树荫下只有 4 颗星时仍能报出景区名，而不是掉进 `none` —— 演示时这个差别是致命的 |
| **景区边界迟滞（×1.15）** | 沿边界走路时景区名不会一跳一跳 |
| **gps↔ble 迟滞（20s）** | 在门口来回走时不会疯狂切换模式，Agent 才不会说出自相矛盾的讲解 |

---

## 三、现在就能测什么（零硬件）

**采购还没到货，这台笔记本已经能测掉大半。** 下面四件事今天就能做。

### 1. 整体体检 —— 一次命令

```bash
pip install -r requirements.txt
pytest -q          # 期望：110 passed
```

覆盖范围：契约字段冻结、两档质量门槛（景区级/点位级）、围栏距离与 WGS84↔GCJ-02 折算、
景区与点位判定、景区边界迟滞、gps/ble/none 三态切换与切换迟滞、iBeacon 广播解析、
RSSI 中位数滤波、高德响应解析与非阻塞降级、两个配置文件的合法性、以及 6 个端到端场景。

### 2. 看它"跑起来" —— 路线模拟器

```bash
python location/tools/simulate_route.py             # 6 个场景，把整条链路演一遍
python location/tools/simulate_route.py --assert    # 顺便断言，19 项
python location/tools/simulate_route.py --list      # 看有哪些场景
```

它**不 mock 任何业务逻辑**：喂假坐标，走真实的 `GeofenceIndex` + `LocationStateMachine`，
读真实的 `scenic_areas.yaml`。所以你在笔记本上看到的，就是 GPS 到货后现场会看到的。

```
场景：外滩漫步
────────────────────────────────────────────────────────────────────────────
  ✔ gps 「外白渡桥」                   conf=0.95   站在外白渡桥（在点位圈里）
  ✔ gps 「陈毅广场（南京东路口）」     conf=0.95   走到陈毅广场
  ✔ gps 「十六铺码头（延安东路口）」   conf=0.95   再走到十六铺码头
  ✔ gps 「外滩」                       conf=0.85   走到江边，但不在任何点位圈里
  ✔ gps 「当前位置附近」               conf=0.50   沿南京东路往西走，走出景区围栏

场景：走进室内（GPS 丢失转蓝牙）
────────────────────────────────────────────────────────────────────────────
  ✔ gps 「外滩」                       conf=0.85   在室外，GPS 正常
  ✔ ble 「三号展厅-青铜器展位」        conf=0.70   走进展厅，GNSS 不再出句子；35 秒后旧定位过期
  ✔ ble 「三号展厅-青铜器展位」        conf=0.70   走到门口，GPS 又拿到了新定位 —— 但迟滞期内不切回去
  ✔ gps 「外滩」                       conf=0.85   又过 11 秒（累计 21s > 20s 迟滞），切回 GPS
```

改 `scenic_areas.yaml` 的坐标或半径之后，重跑这个，立刻能看出判定会不会跳变。
**这比到现场才发现"点位圈画歪了"便宜太多。**

### 3. 验证"永不抛异常"这条硬承诺

```bash
python -c "import json; from location import get_location; print(json.dumps(get_location(), ensure_ascii=False))"
# {"mode": "none", "poi_name": "定位不可用", ...}   ← 没插 GPS 也不报错，这就是契约
```

### 4. 蓝牙 —— 不用买信标就能测

```bash
python location/tools/ble_probe.py
```

它扫附近的 BLE 广播，把 iBeacon 单独挑出来、直接打印能粘进 `beacons.yaml` 的片段。
**先验证蓝牙链路**：只要扫得到任何设备（音箱、耳机、手机都行）就说明通。
本机实测 10 秒扫到 33 个设备。

然后**安卓手机装一个 Beacon Simulator 类 App，选 iBeacon 模式广播** ——
`--ble` 那条链路今天就能走完，不用等信标到货。拿到真信标后再跑一次这个工具，
读出它真实的 UUID/Major/Minor/TX Power。

### 5. 高德 Key 体检（有了 Key 就能做）

```bash
# PowerShell
$env:AMAP_WEB_KEY="你的key"; python location/tools/amap_check.py
# Linux / 树莓派
export AMAP_WEB_KEY=你的key; python location/tools/amap_check.py
```

三件事一起做完：

1. **Key 能不能用、类型选对没有** —— 选成 Android/iOS 会报 `10009`，它会直接告诉你怎么改
2. **坐标系实测** —— 同一地点，发 WGS84 vs 发 GCJ-02 的结果并排打出来。
   上海实测偏移 **~480m**：不折算的话，站在外滩查出来是「微来美上海芮雅门诊部」，
   折算后才是「外滩-观景大道」
3. **反向核对你的坐标** —— 把 `scenic_areas.yaml` 里每个景区/点位拿去逆地理编码，
   看高德认作哪儿。这是拿**独立数据源**验证你填的坐标

> Key 只从环境变量读，不会写进任何文件。`.env` 已经在 `.gitignore` 里，照 `.env.example` 填。

### 现在还测不了的

| 想测 | 卡在哪 |
|---|---|
| 真实串口读 NMEA | 等 NEO-M8N 到货（`python location/tools/nmea_recorder.py --port COM3`） |
| 现场半径标定 | 等 GPS 到货（`python location/tools/where_am_i.py --port COM3`） |

> ⚠️ **别把 COM3 当成 GPS。** 本机现在就有 COM3/COM4，但那是"蓝牙链接上的标准串行"。
> GPS 插上后会是一个**新的** COM 口，用 `python -c "import serial.tools.list_ports as p; [print(x.device, x.description) for x in p.comports()]"` 看描述确认。

---

## 四、配置怎么填

### `location/scenic_areas.yaml` —— 景区和点位

⚠️ **坐标必须是 WGS84。** 高德/腾讯坐标拾取器给的是 GCJ-02（火星坐标），在中国境内和 WGS84 相差 **300–600m**。景区半径 900m 还能忍，点位半径 40m 就永远进不去。两个办法：

1. **实测取点（推荐）**：拿着 GNSS 模块站在目标点，跑
   ```bash
   python location/tools/nmea_recorder.py --port COM3 --pick
   ```
   它会采样 20s、取中位数，直接打印一段能粘进 yaml 的坐标，还会告诉你离散度（>15m 说明星况差，换个开阔位置重来）。
2. **从高德拾取器粘**：把该条目的 `coord` 写成 `gcj02`，加载时自动折算。

半径怎么定：景区 `radius_m` = 中心到最远边界的距离（校园一般 500–1200m）；点位 `radius_m` = 你希望"站多近才算到了"，一般 20–60m（太小走到跟前也不触发，太大相邻点位抢话）。

**调半径的实时工具**：`python location/tools/where_am_i.py --port COM3` —— 每秒刷新你在哪、离每个景区/点位多远、最终会被判成什么。改完 yaml 下一秒就生效，不用重启。站在点位正中记一个距离、走到触发边界再记一个，`radius_m` 取偏保守的中间值。

### `location/beacons.yaml` —— 室内信标

1. 布好信标，站在展位正中跑 `python location/tools/beacon_calibrate.py`，记下稳定值（比如 -62）
2. 走到展位边界，再记一个值（比如 -78）
3. `rssi_threshold` 取偏保守的中间值（比如 -72）回填 yaml
4. 同时用厂商 App 把信标 **TX Power 调小** —— 覆盖范围小了好调得多，比事后抠阈值有效

---

## 五、15 天单人排期

比两人版慢，但**围栏方案给单人省下了至少 3 天**：在线反解的 Key 申请、配额、超时、缓存这一整套现在都成了可选项。

| 日 | 干什么 | 需要硬件吗 |
|---|---|---|
| **D1** | 下单采购；改造 `scenic_areas.yaml`；跑通 `pytest -q` 全绿；把 D1 换算成真实日期发群里 | 不需要 |
| **D2** | `location/tools/nmea_recorder.py --replay` 熟悉解析器；用假 fix 打通 `get_location()` 全链路 | 不需要 |
| **D3** | GPS 到货 → 笔记本上 NMEA hello world，看到 `$GPGGA` 刷屏并解析出坐标 | 需要 |
| **D4** | 去目标景区实地**取点**：围栏中心 + 2–3 个讲解点位，回填 yaml | 需要 |
| **D5** | **调半径**：站在点位中心/边界，看 `get_location()` 输出对不对，改 `radius_m` | 需要 |
| **D6** | 手机装 Beacon Simulator 跑通 `ble_scan.py`；写 `beacons.yaml` | 不需要（手机即可） |
| **D7** | 三种 mode 在真机上全过：GPS 有效 / 拔掉 GPS 降级 BLE / 都没有返回 none | 需要 |
| **D8–D9** | 上树莓派：GPS 走 `/dev/ttyUSB0`，板载蓝牙扫信标，`systemd` 拉起 | 需要 Pi |
| **D10–D12** | 线下联调：配合 Agent 负责人跑全链路，定位耗时 p95 ≤ 3s | 需要 |
| **D13–D14** | 实地踩点：两个景区各走一遍，室里布 3 个信标，录 demo 视频兜底 | 需要 |
| **D15** | 缓冲 | — |

**高德 Key 降级为"有空再办"**：现在它是兜底路径，D8 之后有余力再申请个人认证。

---

## 六、上树莓派（别踩这个坑）

**树莓派 4B 的 GPIO14/15 真串口默认被板载蓝牙占用**，你在 `/dev/ttyS0` 拿到的是波特率随 CPU 频率漂移的 mini UART，GPS 数据会断续丢包。而用 `dtoverlay=disable-bt` 抢回 PL011 的代价是**板载蓝牙被禁用 —— 你的 BLE 扫描也没了**。

所以：**GPS 走 USB 串口（`/dev/ttyUSB0`），板载蓝牙原样留给 bleak**。不动 `config.txt`，两条链路各走各的。（树莓派 5 没这问题。）

```bash
sudo usermod -aG dialout $USER      # 串口免 sudo
rfkill unblock bluetooth            # 板载蓝牙常被 soft-block
export GNSS_PORT=/dev/ttyUSB0
export AMAP_WEB_KEY=...             # 可选
```

---

## 七、边界：单人版明确不做

- ❌ 三点定位 / RSSI 距离估算 —— 展位场景只要"最近的信标是谁"
- ❌ GPS 轨迹平滑 / 卡尔曼滤波 —— 围栏判定不需要
- ❌ POI 二级分类（景点/餐厅/公交站）—— Agent 只要一个名字
- ❌ 多边形围栏 —— 圆形够用，多边形在 15 天里是纯负债
- ⏳ SIM7600 4G HAT 一体化 —— 赛后方向，不是现在
- ⏳ 景区内导航 / 路径引导 —— 不在本模块范围

**卡壳时间盒**：任何子任务卡超过 4 小时，就降到"能演示的最低版本"，并把限制写进这里。15 天里，能跑通的不完美方案永远赢过跑不通的完美方案。

---

## 八、文件地图

本模块是**自包含**的：代码、配置、脚本、测试都在自己的地盘里，合并到团队 monorepo
不会和别的模块撞名。

```
location/                     ← 本模块（可以整个目录拷走）
├── __init__.py               对外只暴露 get_location / start / stop
├── contract.py               数据契约 + 两档质量门槛          ← 先看这个
├── geofence.py               本地围栏 + WGS84/GCJ-02 折算
├── gnss.py                   串口常驻读取 + NMEA 解析
├── ble_scan.py               bleak 扫描 + iBeacon 解析 + RSSI 滤波
├── geocode.py                高德在线反解（兜底，可选）
├── switch.py                 三态切换 + 分层命名 + 两条迟滞   ← 核心逻辑
├── location.py               get_location() 唯一入口
├── scenic_areas.yaml         景区围栏 + 点位（手填）
├── beacons.yaml              信标 → 展位名（现场标定）
├── README.md                 就是本文
└── tools/                    只有本模块用的脚本
    ├── simulate_route.py     ★ 零硬件跑通整条链路（6 个场景，也是答辩素材）
    ├── ble_probe.py          ★ BLE 探针：验证蓝牙 + 读真实信标参数
    ├── amap_check.py         ★ 高德 Key 体检 + 用高德反向核对坐标
    ├── amap_fill_spots.py    用高德 POI 搜索自动填点位坐标
    ├── where_am_i.py         现场校准：实时看你离每个围栏多远、会被判成什么
    ├── nmea_recorder.py      录制 / 回放 / --pick 取点
    └── beacon_calibrate.py   现场看 RSSI，调 TX Power 和阈值

tests/                        仓库级共享测试目录
└── test_location_*.py        8 个文件 / 110 条，全部无需硬件
    ├── test_location_contract.py    契约与两档质量门槛
    ├── test_location_geofence.py    距离、坐标折算、围栏判定、边界迟滞
    ├── test_location_gnss.py        NMEA 解析（回放文本，不需要模块）
    ├── test_location_ble_scan.py    iBeacon 解析、RSSI 滤波、信标映射
    ├── test_location_geocode.py     高德响应解析、缓存、非阻塞降级
    ├── test_location_switch.py      三态切换与分层命名
    ├── test_location_config.py      两个配置文件的合法性
    └── test_location_scenarios.py   端到端场景（读真实配置）

tools/                        仓库级共享脚本（不属任何单个模块）
└── audit_python_env.py        环境体检：查被外部软件删掉的 Python 库文件
```

★ = 采购到货前就能用，先玩这几个。

**命令都在仓库根目录跑**，例如：

```bash
python -m pytest -q                              # 全部测试
python location/tools/simulate_route.py --assert # 零硬件演练
python location/tools/ble_probe.py               # BLE 探针
```

配套阅读：
- `docs/讲解-定位是怎么工作的.md` —— 从按下按钮到扬声器出声的完整链路，
  写给不熟悉这块的人，含坐标系坑和现场操作步骤
- `docs/采购清单-定位模块.md` —— 买什么、别买什么、到货怎么验收
