# agent 模块说明

设备核心调度大脑：负责状态管理、任务并发编排、提示词拼装、全链路流程控制。

## 当前对接状态

| 模块 | 实现 | 状态 |
| --- | --- | --- |
| 定位 | `LocationAdapter`（同事代码 + 适配层） | ✅ 真实（无硬件自动降级） |
| 识图 | `QwenVLVision`（同事交付，Qwen-VL-Max） | ✅ 真实 |
| TTS | `get_tts()`（同事交付，小米 MiMo） | ✅ 真实（真出声） |
| LLM | `DashScopeLLM`（阿里云 DashScope） | ✅ 真实 |
| ASR | `DashScopeASR`（Paraformer） | ⚠️ 真实骨架，等麦克风+OSS |

## 核心文件说明

| 文件 | 作用 |
| --- | --- |
| `main.py` | 程序主入口：组装五个模块，支持 `--demo` 演示 / 真实按键监听 |
| `state_machine.py` | 有限状态机：控制状态流转，`trigger()` 是按键入口（含打断） |
| `orchestrator.py` | 任务编排器：并发采集、生成讲解、语音播报、语音问答 |
| `prompt_builder.py` | 提示词拼装 + 降级策略（位置/识图失败各有兜底话术） |
| `dashscope_llm.py` | LLM 客户端：单轮讲解 `chat()` + 多轮问答 `ask()`（带记忆） |
| `button.py` | 按键监听：树莓派 GPIO / 开发机键盘，含防抖、线程安全 |

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
python agent/tests/test_full_flow.py    # 纯 Mock 全流程（无需 key / 硬件）
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

### 2. 配置 `.env`（三个 Key）

在 `bear-guide/.env` 填：

```
AMAP_WEB_KEY=高德Key
DASHSCOPE_API_KEY=阿里云Key（识图+LLM+ASR 共用）
MIMO_API_KEY=小米Key
```

### 3. 按键接线（3 颗按钮，均接 3.3V 高电平有效）

引脚定义在 `agent/button.py` 顶部：

| 按钮 | 引脚（BCM） | 行为 |
| --- | --- | --- |
| 功能键 | GPIO17 | 短按=触发讲解（播报中再按=打断）；**长按 2 秒=解锁/上锁导览服务** |
| 音量＋ | GPIO18 | 短按音量+5%（滴声反馈，防抖50ms，无长按事件） |
| 音量− | GPIO27 | 短按音量−5%（滴声反馈，防抖50ms，无长按事件） |

按钮一端接 GPIO，一端接 3.3V（内部下拉）。上电默认**上锁**：
短按无效，长按功能键 2 秒解锁 → 自检 → 「就绪提示音」后可用。
若按钮是接 GND 的接法，把 `button.py` 里 `PULL_UP` 改成 `True`。

### 4. 启动

```bash
cd /home/shu/ICAN/bear-guide
python -u agent/main.py
```

看到 `[按键] 功能键=GPIO17（…）` 即表示按键监听就绪。上电默认上锁，长按功能键 2 秒解锁（自检 + 提示音）后短按即可讲解。

### 5. 开机自启（systemd，可选）

```bash
sudo cp deploy/bear-guide.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bear-guide
journalctl -u bear-guide -f    # 看日志
```

### 6. 已知待办（等硬件）

- **连续语音问答已冻结**（2026-09-22）：麦克风 + OSS 未到位，`state_machine.py` 里
  `ENABLE_CONVERSATION=False`，设备为「一按一讲」模式。
  解冻步骤：硬件到位后改常量为 `True`，并把 `main.py` 的 ASR 接线恢复为 `DashScopeASR()`。
- **ASR 无「说完」检测**：`asr/dashscope_asr.py` 是**固定 5 秒录音窗口**（`sd.rec` + `sd.wait`），
  没有 VAD 端点判定，不知道用户何时说完；说话超过 5 秒会被切断；
  每轮聆听固定耗时约 7~10 秒（5s 录音 + 上传 + 轮询识别）。
  改进路径：① 能量端点检测（连续静音 ~0.8s 判说完，改动小，numpy 已就绪）；
  ② 升级 Paraformer 实时识别（websocket，服务端自带 VAD/端点）。
- **ASR 音频上传**：`asr/dashscope_asr.py` 的 `_upload_audio()` 需要接 OSS（麦克风+OSS 到位后补），其余录音/识别代码已就绪。
- **TTS 音频设备**：上机跑 `aplay -l`，若出声不对在 `.env` 配 `TTS_ALSA_DEVICE=plughw:CARD=名字,DEV=0`。
