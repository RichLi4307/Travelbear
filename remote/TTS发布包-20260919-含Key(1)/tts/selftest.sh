#!/usr/bin/env bash
# TTS 模块 · 一键自检（树莓派 / Linux 用；Windows 请用项目根的 一键验收.bat）
#
# 用法：
#     cd tts && ./selftest.sh              全离线自检，不出声、不联网
#     cd tts && ./selftest.sh --online     追加真实云端合成（需要 Key，会出声）
#     cd tts && ./selftest.sh --quick      只做环境体检
#
# 整个脚本零第三方依赖，不需要 pip install 任何东西。

set -uo pipefail
cd "$(dirname "$0")" || exit 1

if [ ! -f selftest.py ]; then
    echo "[x] 当前目录没有 selftest.py，请在 tts/ 目录内运行本脚本。" >&2
    exit 1
fi

# 依次尝试：python3 -> python
PY=""
for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
        PY="$cand"
        break
    fi
done

if [ -z "$PY" ]; then
    echo "[x] 没找到 python3，请先安装：sudo apt install python3" >&2
    exit 1
fi

# 统一 UTF-8，避免 SSH 终端语言环境没配好时中文乱码
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

exec "$PY" selftest.py "$@"
