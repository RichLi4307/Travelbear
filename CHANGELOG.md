# CHANGELOG

本项目的所有重要变更记录在此。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)。

## [未发布] - 2026-09-22 日志与交互反馈

### 功能
- **按键受理提示音**：待机时短按功能键，立即放短促单声反馈「已受理、开始干活」，消除按下到出声之间的空窗（`state_machine.trigger()` 受理分支）
- **LLM 返回结果落日志**：`agent.llm` INFO 全量输出讲解文案/问答回复，附请求耗时与字数；`--debug` 另有原始响应
- **识图日志**：`agent.vision` INFO（开始取景/云端耗时/识别结果+置信度），失败/超时 WARNING
- **TTS 日志补全**：`tts.interface` INFO（开始播报字数/播报完成句数/外部停止），与引擎层 `tts` 日志互补
- **日志分级体系**：`main.py` 统一配置根日志（默认 INFO），`--debug` 降为 DEBUG 输出全文载荷

### 修复
- `.env` 增加 `TTS_VOLUME=15`：修复 TTS 引擎每次启动把系统音量重置为 100% 的问题（音量标定以 .env 为准）

## [未发布] - 2026-09-22 部署适配（树莓派 5 实机）

### 部署与环境
- 解压 `ICAN.zip`，主程序位于 `bear-guide/`，Python 虚拟环境 `.venv/`
- 安装全部依赖；`opencv-python` 替换为 `opencv-python-headless`（无头系统缺 libGL，项目未用 GUI 接口）
- gpiozero 依赖的 `lgpio` 从系统 `dist-packages` 复制入 venv（Pi 5 必需，pip 源码编译失败）
- 新增 systemd 单元 `bear-guide/deploy/bear-guide.service`（开机自启，`enable --now` 即用；密钥仍走 .env，不写进单元文件）

### 热键（`agent/button.py`）
- 三键映射：**GPIO17 功能键 / GPIO18 音量+ / GPIO27 音量−**
- 实测按钮接 3.3V 高电平有效 → `PULL_UP=False`（原默认上拉会逻辑反置）
- 功能键短按/长按区分：held 标志保证「解锁的长按」不触发短按
- 音量键仅短按、无长按事件
- 键盘回退（开发机无 gpiozero 时）：回车=短按，p=长按，+/-=音量
- 新增检测脚本 `agent/hw_check.py`：按功能键叫一声、音量键带滴声反馈，长按退出

### 功能
- **服务锁**（`agent/service.py`）：上电默认上锁；长按功能键 2 秒解锁/上锁；解锁时本地自检（API Key/摄像头/音量通道/aplay）通过后放「就绪提示音」
- **提示音体系**：就绪=上行两声 / 上锁=单声低音 / 打断=下行两声 / 音量=滴声（本地合成，aplay 播放，不联网）
- **打断完善**（`agent/state_machine.py`）：播报中短按=停 TTS+提示音+明确日志；问答轮次中打断会结束整个对话；回待机日志区分「讲解完成/被打断」
- **音量控制**（`agent/volume.py`）：amixer 绝对值增减 ±5%，边界钳制；修复该声卡（dB 刻度）`amixer 5%+/-` 相对语法错乱问题（`5%-` 会从 15% 跳到 100%）
- 音量键防抖 300ms→50ms（lgpio 防抖机制=固定响应延迟；功能键保持 300ms 以满足 ≥300ms 要求）
- 压制 `vendor.location.gnss/ble_scan` 无硬件重连告警刷屏（WARNING→ERROR）

### 变更（冻结）
- **连续语音问答冻结**：`state_machine.py` 的 `ENABLE_CONVERSATION=False`，设备为「一按一讲」模式（讲解完直接回待机，不聆听）
  - 原因：麦克风未被系统识别（USB 声卡不枚举）+ `sounddevice` 缺系统 libportaudio2 + ASR 音频上传未实现
  - 解冻：硬件到位后常量改 `True`，并把 `main.py` 的 ASR 接线恢复为 `DashScopeASR()`

### 文档
- `agent/README.md`：热键接线表、提示音体系、按键路由、打断行为、开机自启、已知待办（含 ASR 无「说完」检测问题记录）
- `AGENTS.md`：协作约定与 git 提交频率
- `deploy/init_git.sh`：git 仓库初始化脚本（上游基线 + 部署改动两次提交）

### 已知问题（详见 agent/README.md 待办）
- 麦克风 USB 声卡系统未识别（`arecord -l` 为空），真实模式 ASR 不可用
- GPS 未接入（无 /dev/ttyUSB0，代码自动降级）；蓝牙未开启（BLE 信标不可用）
- ASR 固定 5 秒录音窗口、无说完检测（冻结期间不阻塞使用）
