# 熊导游（TravelBear）

树莓派 4B 上的 AI 景区导览设备：按键触发 → 摄像头取景 + 定位 → 云端 LLM 生成讲解 → 语音播报。

本文件是**仓库总 README**（面向 GitHub 与项目组）。技术细节协作约定见 [AGENTS.md](AGENTS.md)，变更记录见 [CHANGELOG.md](CHANGELOG.md)，**硬件清单与接线见 [硬件清单.md](硬件清单.md)**，代码主程序在 `bear-guide/`。

**当前状态（2026-09-28）**：硬件全链路实测闭环——GNSS 室外定位、BLE 信标室内定位、识图、LLM、TTS、按键热键、服务锁、打断均通过真实验收（记录见 `tecs/验收日志/`）。待现场事项：信标进馆布点与阈值标定、信标供电方案。

## 模块对接状态

| 模块 | 实现 | 状态 |
| --- | --- | --- |
| 定位（室外） | `vendor/location`（GNSS NEO-M8N + 本地围栏） | ✅ 实测：9星/HDOP 2.87，命中「上海大学（宝山校区）」 |
| 定位（室内） | `vendor/location`（BLE iBeacon 信标映射） | ✅ 实测：1号信标映射「三号展厅-青铜器展位」 |
| 识图 | `QwenVLVision`（同事交付，Qwen-VL-Max） | ✅ 真实（置信度<0.05 的画面不用） |
| TTS | `get_tts()`（同事交付，小米 MiMo / 云端兜底） | ✅ 真实（真出声，尾音完整） |
| LLM | `DashScopeLLM`（阿里云 qwen-plus） | ✅ 真实（实测 2.4s/127字） |
| ASR | `DashScopeASR`（Paraformer） | ⚠️ 冻结：等麦克风+OSS，设备为一按一讲 |

## 定位方案（两级定位 + BLE 最高优先）

- **室外**：NEO-M8N GPS 接 GPIO14/15（`/dev/ttyS0`），后台线程常驻搜星；本地围栏两档判定（景区级宽松/点位级严格），未命中走高德在线反解兜底。
- **室内**：ESP32-C3 信标广播 iBeacon，树莓派板载蓝牙扫描 → RSSI 中值滤波 → 阈值过滤 → 展位映射（`vendor/location/beacons.yaml`）。
- **仲裁（负责人拍板）**：**BLE 新鲜命中无条件最高优先**——信标固定在展位上是米级物理证据，GPS（含点位级）不插嘴；信标过期 10 秒自动落回 GPS。消除展厅内 GPS 漏入弱 fix 导致讲解串味。
- 行为锁定在 `bear-guide/location/tests/test_ble_priority.py`（9 条）。

| 信标 | 展位 | UUID | major:minor | RSSI 阈值 |
| --- | --- | --- | --- | --- |
| 1号 | 三号展厅-青铜器 | FDA50693-A4E2-4FB1-AFCF-C6EB07647825 | 1:1 | −85（无天线放宽，进馆标定精调） |
| 2号 | 三号展厅-陶瓷 | 同上 | 1:2 | −85 |
| 3号 | 一号展厅-入口 | E2C56DB5-DFFB-48D2-B060-D0F5A71096E0 | 2:1 | −85 |

## 核心文件说明（均在 `bear-guide/` 下）

| 文件 | 作用 |
| --- | --- |
| `agent/main.py` | 程序主入口：组装五个模块，支持 `--demo` 演示 / 真实按键监听 |
| `agent/state_machine.py` | 有限状态机：控制状态流转，`trigger()` 是按键入口（含打断） |
| `agent/orchestrator.py` | 任务编排器：并发采集、生成讲解、语音播报、语音问答 |
| `agent/prompt_builder.py` | 提示词拼装 + 降级策略（位置/识图失败各有兜底话术） |
| `agent/dashscope_llm.py` | LLM 客户端：单轮讲解 `chat()` + 多轮问答 `ask()`（带记忆） |
| `agent/button.py` | 按键监听：树莓派 GPIO / 开发机键盘，含防抖、线程安全 |

## 状态流转

```
IDLE → ACQUIRING → GENERATING → SPEAKING（讲解）→ IDLE   ← 当前模式：一按一讲
                                  ↓
                          LISTENING（听游客）             ← 已冻结（ENABLE_CONVERSATION=False）
                            ├─ 有提问 → GENERATING → SPEAKING → 回到 LISTENING
                            └─ 静默   → IDLE
```

任何环节异常都会自动恢复到 `IDLE`，不卡死设备。按键路由（在事件循环线程内判定，无竞争）：

- **待机时短按** = 讲一次（采集→生成→播报→回 IDLE，一按一讲）
- **播报中短按** = 打断：停 TTS → 放下行提示音 → 回 `IDLE`
- 采集/生成中短按 = 忽略；**连续问答已冻结**，当前不存在「聆听」状态
- 长按任何时候都是解锁/上锁

## 运行方式

在项目根目录（`bear-guide`）下：

```bash
# 演示模式：自动跑一次讲解（连续问答已冻结，ASR 用 Mock）
python -u agent/main.py --demo

# 真实模式：监听按键，游客按下触发讲解（树莓派部署用）
# -u 关输出缓冲：重定向到日志文件时 print 也能实时落盘
python -u agent/main.py
```

## 测试命令

```bash
python agent/tests/test_full_flow.py       # 纯 Mock 全流程（无需 key / 硬件）
python location/tests/test_ble_priority.py # 定位仲裁 9 条（BLE 最高优先）
```

---

## 交付给硬件负责人：上机说明

### 1. 需要装的依赖（树莓派）

```bash
# 定位 + 识图
pip install pyserial pynmea2 bleak requests PyYAML opencv-python openai

# ASR 录音（先装系统库再装 python 包）
sudo apt install -y libportaudio2 alsa-utils
pip install sounddevice

# GPIO 按键
pip install gpiozero

# 把运行用户加进音频组和串口组（否则没声音 / 读不了 GPS）
sudo usermod -aG audio,dialout $USER
```

### 2. 配置 `.env`

在 `bear-guide/.env` 填：

```
AMAP_WEB_KEY=高德Key（在线反解兜底，不配则只用本地围栏）
DASHSCOPE_API_KEY=阿里云Key（识图+LLM+ASR 共用）
MIMO_API_KEY=小米Key（TTS）
GNSS_PORT=/dev/ttyS0     # GPS 接 GPIO14/15 时（Pi5 miniUART）
GNSS_BAUD=9600
TTS_VOLUME=75            # 默认音量 75%
TTS_PLAYER=aplay         # 钉死后端，防 libportaudio2 改变探测结果
```

### 3. GPS 接线（NEO-M8N，3 根线，3.3V 逻辑直连）

| GPS 线 | Pi5 引脚 | 说明 |
| --- | --- | --- |
| 5V | Pin 2/4 | 供电 |
| GND | Pin 6 | 必须共地 |
| GPS 的 TX | Pin 10（GPIO15） | GPS 发 → Pi 收 |
| GPS 的 RX / PPS | 不接 | 只读定位无需发命令；PPS 是授时用 |

系统侧一次性配置（已完成，重装系统需重做，备份 `.bak-gps`）：
`config.txt` 加 `enable_uart=1`；`cmdline.txt` 删除 `console=serial0,115200`。

### 4. 按键接线（3 颗按钮，均接 3.3V 高电平有效）

引脚定义在 `bear-guide/agent/button.py` 顶部：

| 按钮 | 引脚（BCM） | 行为 |
| --- | --- | --- |
| 功能键 | GPIO17 | 短按=触发讲解（播报中再按=打断）；**长按 2 秒=解锁/上锁导览服务** |
| 音量＋ | GPIO18 | 短按音量+5%（滴声反馈，防抖50ms，无长按事件） |
| 音量− | GPIO27 | 短按音量−5%（滴声反馈，防抖50ms，无长按事件） |

按钮一端接 GPIO，一端接 3.3V（内部下拉）。上电默认**上锁**：
短按无效，长按功能键 2 秒解锁 → 自检 → 「就绪提示音」后可用。
若按钮是接 GND 的接法，把 `bear-guide/agent/button.py` 里 `PULL_UP` 改成 `True`。

### 5. 启动

```bash
cd /home/shu/ICAN/bear-guide
python -u agent/main.py
```

看到 `[按键] 功能键=GPIO17（…）` 即表示按键监听就绪。上电默认上锁，长按功能键 2 秒解锁（自检 + 提示音）后短按即可讲解。受理后有讲解员口吻语音安抚（"好嘞，我看看这是哪儿"），约 8~10 秒出声。

### 6. 开机自启（systemd）

```bash
sudo cp bear-guide/deploy/bear-guide.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bear-guide
journalctl -u bear-guide -f    # 看日志
```

### 7. 摄像头与信标

- **摄像头**：USB 免驱。偶发掉线以新身份重枚举时，免密复位 `sudo -n usb-reset-cam`；浏览器看图 `python3 -m http.server 8901 --directory bear-guide/captures`。
- **信标**（ESP32-C3，非本组维护）：烧录与布点见交付包 `~/信标交付文件/`；进馆后需现场标定阈值（站在展位正中/边界各读一次 RSSI，回填 `vendor/location/beacons.yaml`）。
- **麦克风**：USB 声卡未枚举，ASR 不可用；设备形态为「一按一讲」，不阻塞交付。

### 8. 已知待办（等硬件）

- **连续语音问答已冻结**（2026-09-22）：麦克风 + OSS 未到位，`bear-guide/agent/state_machine.py` 里
  `ENABLE_CONVERSATION=False`，设备为「一按一讲」模式。
  解冻步骤：硬件到位后改常量为 `True`，并把 `bear-guide/agent/main.py` 的 ASR 接线恢复为 `DashScopeASR()`。
- **ASR 无「说完」检测**：`bear-guide/asr/dashscope_asr.py` 是**固定 5 秒录音窗口**（`sd.rec` + `sd.wait`），
  没有 VAD 端点判定，不知道用户何时说完；说话超过 5 秒会被切断；
  每轮聆听固定耗时约 7~10 秒（5s 录音 + 上传 + 轮询识别）。
  改进路径：① 能量端点检测（连续静音 ~0.8s 判说完，改动小，numpy 已就绪）；
  ② 升级 Paraformer 实时识别（websocket，服务端自带 VAD/端点）。
- **ASR 音频上传**：`bear-guide/asr/dashscope_asr.py` 的 `_upload_audio()` 需要接 OSS（麦克风+OSS 到位后补），其余录音/识别代码已就绪。
- **信标供电**：进馆确定（USB 充电头 / 移动电源）。
- **TTS 音频设备**：上机跑 `aplay -l`，若出声不对在 `.env` 配 `TTS_ALSA_DEVICE=plughw:CARD=名字,DEV=0`。
