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
IDLE → ACQUIRING → GENERATING → SPEAKING（讲解）
                                  ↓
                          LISTENING（听游客）
                            ├─ 有提问 → GENERATING → SPEAKING → 回到 LISTENING
                            └─ 静默   → IDLE
```

任何环节异常都会自动恢复到 `IDLE`，不卡死设备。播报中再按按键 = 打断。

## 运行方式

在项目根目录（`bear-guide`）下：

```bash
# 演示模式：自动跑一次「讲解 + 问答」，开发机调试用（ASR 用 Mock，不接麦克风）
python agent/main.py --demo

# 真实模式：监听按键，游客按下触发讲解（树莓派部署用）
python agent/main.py
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

### 3. 按键接线

默认 GPIO 引脚是 **BCM 17**（在 `agent/button.py` 顶部 `DEFAULT_GPIO_PIN` 改）。
按钮一端接 GPIO17，一端接 GND。按下即触发讲解，播报中再按 = 打断。

### 4. 启动

```bash
cd /home/pi/robot/bear-guide
python agent/main.py
```

看到 `[按键] 已监听 GPIO17（防抖 300ms）` 即表示就绪，按按钮开始讲解。

### 5. 已知待办（等硬件）

- **ASR 音频上传**：`asr/dashscope_asr.py` 的 `_upload_audio()` 需要接 OSS（麦克风+OSS 到位后补），其余录音/识别代码已就绪。
- **TTS 音频设备**：上机跑 `aplay -l`，若出声不对在 `.env` 配 `TTS_ALSA_DEVICE=plughw:CARD=名字,DEV=0`。
