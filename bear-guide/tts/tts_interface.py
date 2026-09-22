# -*- coding: utf-8 -*-
"""TTS 模块对外接口层 —— 实现《熊导游项目 模块接口契约 v1.0》。

契约要求（不可改，见项目根《接口规范》）：
    文件位置：tts/tts_interface.py
    抽象基类：TTSInterface
    必须实现：async def speak(self, text: str, interrupt: bool = False) -> PlayStatus
    返回类型：common.types.PlayStatus（success / is_playing / error_msg）
    内部异常不许抛出，必须封装进 error_msg

分层说明：
    tts.py            引擎本体（同步 + 后台线程，零第三方依赖，已实测）
    tts_interface.py  ← 本文件。只做"同步引擎 → 异步契约"的适配，不碰引擎逻辑

为什么是适配层而不是重写引擎：
    契约要求"异步"，指的是**对外接口是 async**。引擎内部用后台线程做流水线，
    对调用方没有可见差异；重写成 asyncio 原生反而会丢掉已实测的时序特性
    （首字 0.84s、冷切换 0.8s、stop 0-7ms），收益为零、风险不小。

三个语义选择（都已按最保守的方式实现，但**需要 Agent 负责人确认**）：
  1. speak() 是**阻塞语义**：await 到这句话播完才返回，此时 is_playing 通常为 False。
     如果契约本意是"启动即返回、音频在后台播"（is_playing=True），需要改一行。
  2. interrupt=True  → 立刻打断当前播报，改播新文本
     interrupt=False → 若正在播报，**先等它播完**再播新文本（不丢内容）
  3. 被 asyncio 取消（例如外层用 wait_for 加超时）时，会**先主动停音频再抛
     CancelledError**。否则会出现"Agent 已经放弃，熊还在说话"。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

log = logging.getLogger("tts.interface")


# --------------------------------------------------------------------------
# 引擎导入：两种启动方式都要能跑
#   · 仓库根目录在 sys.path（Agent 主程序 `from tts.tts_interface import ...`）
#   · tts/ 目录本身在 sys.path（我们自己的测试脚本，直接在本目录跑）
# --------------------------------------------------------------------------

try:
    from tts import tts as _engine                       # type: ignore
except ImportError:                                      # 本目录内直接运行
    import tts as _engine                                # type: ignore[no-redef]


# --------------------------------------------------------------------------
# PlayStatus
# --------------------------------------------------------------------------

try:
    from common.types import PlayStatus                  # type: ignore
    PLAY_STATUS_SOURCE = "common.types（Agent 侧定义）"
except Exception:                                        # noqa: BLE001
    from dataclasses import dataclass

    @dataclass
    class PlayStatus:                                    # type: ignore[no-redef]
        """临时占位，仅在找不到 common/types.py 时生效。

        拿到 Agent 的 common/types.py 后，上面的 import 就会成功，
        这个定义**不会被执行**。字段名按契约文本写死：
        success / is_playing / error_msg。
        """
        success: bool = False
        is_playing: bool = False
        error_msg: str = ""

    PLAY_STATUS_SOURCE = "本地占位（没找到 common/types.py，待 Agent 提供）"


def _status(success: bool, is_playing: bool, error_msg: str = "") -> Any:
    """构造 PlayStatus，绝不让"构造失败"演变成抛给 Agent 的异常。

    Agent 的 PlayStatus 可能是 dataclass / NamedTuple / pydantic 模型，
    只要字段名一致，关键字构造都能用。万一字段名对不上，逐级降级。
    """
    try:
        return PlayStatus(success=success, is_playing=is_playing,
                          error_msg=error_msg)
    except Exception as exc:                             # noqa: BLE001
        log.error("PlayStatus 构造失败（字段名对不上？）: %s", exc)
        for args in ((success, is_playing, "PlayStatus 构造失败: %s" % exc),
                     ()):
            try:
                return PlayStatus(*args)
            except Exception:                            # noqa: BLE001
                continue
        raise                                          # 连空构造都不行，只能抛


# --------------------------------------------------------------------------
# 契约规定的抽象基类
# --------------------------------------------------------------------------

class TTSInterface(ABC):
    """Agent 核心只依赖这个抽象 + speak() 这一个方法。"""

    @abstractmethod
    async def speak(self, text: str, interrupt: bool = False) -> PlayStatus:
        """异步播报一段文本。

        参数：
            text       要播报的文本。允许为空字符串 → 直接返回成功，不做事
            interrupt  True  = 立刻打断当前播报，改播这段
                       False = 不打断；若正在播报，等它播完再播这段

        返回 PlayStatus：
            success    True = 从头放到尾，没被打断、也没有整句合成失败
            is_playing 返回这一刻是否还有音频在播（正常情况下为 False）
            error_msg  失败原因；成功时为空字符串

        约定：**本函数不抛业务异常**，一切失败都进 error_msg。
        唯一的例外是 asyncio 的 CancelledError —— 那属于调用方的控制流
        （wait_for 超时、任务被 cancel），必须原样向上传播，
        否则超时机制会失效。传播前会先停掉音频。
        """


# --------------------------------------------------------------------------
# 实现
# --------------------------------------------------------------------------

class TTSAdapter(TTSInterface):
    """把 tts.py 的同步引擎适配成契约要求的异步接口。

    并发语义（重要，Agent 侧需要知道）：
        · 允许多个协程同时 await speak()，内部按"谁后启动谁说话"处理
        · interrupt=True 的调用会立刻抢占；被抢占的那一条返回 success=False
        · 同一进程内只有一路音频，不存在两个声音同时说话
    """

    POLL = 0.02          # 等待播完的轮询间隔（秒）
    _QUIET_TIMEOUT = 1.0  # 打断后等引擎静下来的上限（秒）

    def __init__(self, engine: Optional["_engine.TTS"] = None) -> None:
        self._engine = engine if engine is not None else _engine.TTS()
        self._start_lock = asyncio.Lock()
        self._closed = False

    # ---------------- 契约方法 ----------------

    async def speak(self, text: str, interrupt: bool = False) -> PlayStatus:
        text = (text or "").strip()
        if not text:
            return _status(True, self._engine.is_speaking(), "")
        if self._closed:
            return _status(False, False, "TTS 已关闭（close 之后不再接受播报）")

        log.info("开始播报：%d 字", len(text))
        log.debug("播报全文：%s", text)

        try:
            return await self._speak_impl(text, interrupt)
        except asyncio.CancelledError:
            # 调用方取消（wait_for 超时 / 任务被 cancel）：先停声音，再向上抛。
            self._stop_now()
            raise
        except Exception as exc:                         # noqa: BLE001
            log.error("speak 内部异常: %s", exc, exc_info=True)
            self._stop_now()
            return _status(False, self._engine.is_speaking(),
                           "%s: %s" % (type(exc).__name__, exc))

    async def _speak_impl(self, text: str, interrupt: bool) -> PlayStatus:
        # 通篇没有可播的句子（纯标点/空白）→ 引擎会直接返回，不必走流水线
        if not _engine.split_sentences(text):
            return _status(True, self._engine.is_speaking(), "")

        # ① 排队语义：不打断时先等当前播完
        if not interrupt:
            while self._engine.is_speaking():
                await asyncio.sleep(self.POLL)

        # ② 起步。"停止 + 启动"这个组合动作要串行化，否则两条流水线会交叠。
        async with self._start_lock:
            if interrupt:
                await self._wait_quiet()
            prev_metrics = self._engine.last_metrics
            ok = await asyncio.to_thread(self._engine.speak, text, False)
            metrics = self._engine.last_metrics
            if metrics is prev_metrics:
                metrics = None          # 引擎没真的启动一次播报

        if not ok:
            return _status(False, self._engine.is_speaking(),
                           "播报未能启动（引擎拒绝了本次请求）")

        # ③ 等这句播完。
        #    用轮询而不是 to_thread(join)，理由有三个：
        #      · 不长期占用线程池
        #      · 被调用方 cancel 时能及时停音频
        #      · 被新的 interrupt 抢占时能立刻跳出（metrics["stopped"] 会置位）
        while True:
            if metrics is not None and metrics.get("stopped"):
                break
            if not self._engine.is_speaking():
                break
            await asyncio.sleep(self.POLL)

        return self._verdict(metrics)

    # ---------------- 内部 ----------------

    async def _wait_quiet(self) -> None:
        """打断后等引擎真正静下来，再起新的播报。

        这个等待不是可有可无的：消费线程退出时会执行 `player.close()`，
        而 Player 是**共用对象**。如果不等它收尾就启动新播报，
        旧线程的 close() 会把新开的播放设备（Pi 上是 aplay 子进程）关掉。
        """
        self._stop_now()
        deadline = asyncio.get_running_loop().time() + self._QUIET_TIMEOUT
        while self._engine.is_speaking():
            if asyncio.get_running_loop().time() > deadline:
                log.warning("打断后引擎仍未静下来，强行继续")
                break
            await asyncio.sleep(self.POLL)

    def _verdict(self, metrics: Optional[dict]) -> PlayStatus:
        """按引擎的 metrics 判定本次播报结果。"""
        playing = self._engine.is_speaking()
        m = metrics or {}

        if m.get("consume_error"):
            # 播放消费线程崩过：音频很可能几乎没播，绝不能算成功
            log.error("播放消费线程曾异常，本次播报实际未完整出声：%s",
                      m["consume_error"])
            return _status(False, playing,
                           "播放线程异常（实际未完整出声）：%s" % m["consume_error"])

        if m.get("stopped"):
            log.info("播报被外部打断（未播完）")
            return _status(False, playing, "播报被打断，未播完")

        sentences = m.get("sentences") or 0
        failed = m.get("failed") or 0
        if sentences and failed >= sentences:
            log.error("播报失败：%d 句全部没出声（检查网络或 API 凭据）", sentences)
            return _status(False, playing,
                           "合成失败：%d 句全部没出声（检查网络或 API 凭据）" % failed)
        if failed:
            # 部分句子没出声、其余正常：算成功，降级细节只在日志里体现，
            # 避免 error_msg 在 success=True 时非空、把调用方绕晕。
            log.warning("本次播报有 %d/%d 句合成失败", failed, sentences)
        log.info("播报完成：%d 句，失败 %d", sentences, failed)
        return _status(True, playing, "")

    def _stop_now(self) -> None:
        try:
            self._engine.stop()
        except Exception as exc:                         # noqa: BLE001
            log.debug("stop 异常(可忽略): %s", exc)

    # ---------------- 非契约的辅助能力 ----------------

    @property
    def engine(self) -> "_engine.TTS":
        """底层引擎，供我们自己的调试脚本用；Agent 侧不要依赖。"""
        return self._engine

    def is_playing(self) -> bool:
        return self._engine.is_speaking()

    async def stop(self) -> None:
        """外部主动停止（契约里没有，但 Agent 若需要可以调）。"""
        log.info("收到外部停止请求")
        await asyncio.to_thread(self._stop_now)

    async def warmup(self) -> None:
        """开机预热：预先建连、预下载音色元数据，不发声。"""
        await asyncio.to_thread(self._engine.warmup)

    async def aclose(self) -> None:
        """释放资源。close 之后 speak() 一律返回失败。"""
        self._closed = True
        self._stop_now()
        await asyncio.to_thread(self._engine.close)


# --------------------------------------------------------------------------
# 单例入口
# --------------------------------------------------------------------------

_default: Optional[TTSAdapter] = None


def get_tts() -> TTSAdapter:
    """进程内单例。Agent 核心直接用这个即可，不用自己管构造和释放。

    注意：必须在**事件循环已经跑起来之后**再调用（内部要建 asyncio.Lock）。
    """
    global _default
    if _default is None:
        _default = TTSAdapter()
    return _default


__all__ = [
    "TTSInterface",
    "TTSAdapter",
    "PlayStatus",
    "get_tts",
    "PLAY_STATUS_SOURCE",
]
