# -*- coding: utf-8 -*-
"""开机预热入口 —— 供 systemd 的 ExecStart 调用。

作用：校验云端凭据、探测播放后端、记录运行参数，
让配置问题在开机时就暴露，而不是等游客按下按钮。

用法：
    python3 warmup_cli.py

退出码恒为 0：预热失败不应该阻断开机，只记日志。
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tts import TTS  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s [tts-warmup] %(message)s",
)


def main() -> int:
    log = logging.getLogger("tts.warmup")
    engine = TTS()   # 后端类型由 Player 自己打日志
    log.info("缓存目录: %s", os.path.abspath(engine.cfg.cache_dir))
    try:
        engine.warmup()
    except Exception as exc:  # noqa: BLE001
        log.warning("预热异常（不阻断开机）: %s", exc)
    finally:
        engine.close()

    if not engine.cloud.available():
        log.error("云端不可用 —— 设备将无声音，请检查 MIMO_API_KEY 与网络")
    else:
        log.info("预热完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
