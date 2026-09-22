# AGENTS.md — ICAN（熊导游）项目协作约定

树莓派 5 上的 AI 导览设备：按键触发 → 摄像头取景 + 定位 → LLM 生成讲解 → TTS 播报。
主程序在 `bear-guide/`，多模块集成交付（TTS 发布包 / 识图 / 定位在 `remote/`，均已有机整合进 bear-guide）。

## 常用命令（都在 `bear-guide/` 目录下）

```bash
V=.venv/bin/python
$V agent/tests/test_full_flow.py     # 全链路 Mock 回归（必过）
$V -u agent/main.py                  # 真实模式（GPIO 按键，上电默认上锁）
$V -u agent/main.py --demo           # 演示模式（自动跑一次讲解）
$V -u agent/hw_check.py             # 按键硬件检测（按功能键叫一声，音量键带滴声）
amixer -c 0 sget PCM                # 查音量（当前标定 75/100）
sudo -n usb-reset-cam               # 摄像头固件卡死（抓帧挂起）时免密复位，不用重启
journalctl -u bear-guide -f         # systemd 模式看日志
```

## 热键速查（改接线只动 `agent/button.py` 顶部常量）

| 按键 | 短按 | 长按 2 秒 |
| --- | --- | --- |
| GPIO17 功能键 | 待机=讲一次；播报中=打断（停播+下行提示音） | 解锁/上锁导览服务 |
| GPIO18 / GPIO27 | 音量 ±5%（滴声反馈） | — |

按钮高电平有效（接 3.3V），`PULL_UP=False`；功能键防抖 300ms、音量键 50ms。

## 硬件现状

扬声器(3.5mm)✅ 移动WiFi(eth1)✅ 蓝牙✅(rfkill 已解、已通电) ｜ 摄像头⚠️(偶发掉线重枚举，卡死用 sudo -n usb-reset-cam 复位/重插/上供电hub) 麦克风❌(USB声卡不枚举) GPS❌(未接) ESP32（非本组）

## 已知技术坑（改之前先看）

- 该 Pi 的 PCM 是 dB 刻度：amixer 相对语法（`5%+/-`）在这块卡上语义错乱，必须用绝对百分比（`volume.py` 已封装）
- gpiozero 的 bounce_time 会被 lgpio 原样变成响应延迟，不是后台过滤
- 两个进程不能同时占 GPIO（kernel 级引脚占用，后者报 PinBusy）
- 无头系统跑 opencv 必须用 headless 版；Pi5 的 lgpio 用系统包复制进 venv
- stdout 重定向要 `python -u`，否则 print 全卡在缓冲区
- **TTS 引擎启动时用 `TTS_VOLUME`（默认 100）重置系统音量**——音量标定改 `bear-guide/.env` 里的 TTS_VOLUME，别只调 amixer
- **装 libportaudio2 会改变 TTS 后端探测结果**（auto 顺序 pyaudio→sounddevice→aplay，以前 sounddevice 不可用所以是 aplay）——本机已在 .env 钉死 `TTS_PLAYER=aplay`；以后动音频相关依赖，启动后先确认日志里「播放后端： aplay」
- **测试时音量要低**（当前测试基准 10%）：任何会出声的验证（demo/提示音/hw_check）前把 `amixer -c 0 sset PCM 10%`，正式使用标定 75/100
- **USB 摄像头会偶发掉线并以新身份（不同 vendor/product ID）重枚举**（和移动 WiFi 共用 USB2 集线器）：固件卡死（设备节点在、查询正常、抓帧挂起）时跑 `sudo -n usb-reset-cam` 免密复位；连 USB 设备节点都消失时只能重插，根治建议独立供电 hub

## Git 提交频率约定（必须遵守）

1. **每个可独立验证的改动单元完成并测试通过后，立即提交一次**——不积攒多个改动混进一个 commit，也不长时间不提交
2. **每次工作会话结束前必须提交**，工作区不留未提交的半成品
3. commit message 用中文，格式：`<类型>: <一句话简述>`，类型用 `功能`/`修复`/`文档`/`测试`/`部署`
4. 提交前必跑 `agent/tests/test_full_flow.py` 且通过；改动涉及按键/状态机时，另跑打断场景自测
5. **严禁提交密钥**：`.env` 等已被 .gitignore 排除；新增密钥类文件必须同步加进 .gitignore
6. 不主动 `git push`、不做 force/rebase 等历史改写操作（除非用户明确要求）
7. 每次较大的功能变更同步更新 `CHANGELOG.md` 和本文件提到的文档

仓库初始化：`git` 安装后跑 `bash deploy/init_git.sh`（上游基线 + 改动两次提交）。
