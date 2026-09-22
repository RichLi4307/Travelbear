# -*- coding: utf-8 -*-
"""TTS 模块 —— 文本进，声音出。

接口契约（以仓库骨架为准）：

    speak(text: str, wait: bool = True) -> bool
    stop() -> None
    is_speaking() -> bool

补充接口（本模块自用 / 演示现场需要）：

    set_volume(level: int) -> None     # 0-100
    set_voice(voice_id: str) -> None
    warmup() -> None

设计要点：
  * 句级流水线：分句 -> 合成线程 -> 预取队列 -> 播放线程
    （不依赖云端"真流式"，即使流式降级也能边合成边播）
  * 逐句容错：某句云端失败就跳过该句；连续失败 2 次则本次播报剩余句子不再重试，
    免得游客对着机器干等一串超时
  * 打断三步：置位 stop_event -> 清空队列 -> abort 播放流
  * speak() 任何情况下不向调用方抛异常

环境变量（全部可选，均有默认值）：
    MIMO_API_KEY        小米 MiMo 的 Key
    TTS_VOICE           预置音色，默认 REDACTED-ROTATE-ME
    TTS_STYLE           风格描述（放进 user 消息）
    TTS_TIMEOUT         单次网络读超时，默认 5 秒
    TTS_DEADLINE        单句合成总时限，默认 15 秒
    TTS_QUEUE           预取队列容量，默认 2
    TTS_CACHE_DIR       音频缓存目录，默认 .tts_cache
    TTS_PLAYER          pyaudio / sounddevice / aplay / dryrun / null，默认自动探测
                        （null = 只合成、只写缓存，不出声也不等待 —— 预热用）
    TTS_FORCE_ENGINE    cloud / mock，默认空（自动）
    TTS_ALSA_DEVICE     aplay 设备，如 plughw:1,0
    TTS_VOLUME          初始音量 0-100，默认 100
"""

from __future__ import annotations

import array
import base64
import hashlib
import json
import logging
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional, Tuple

__all__ = ["TTS", "speak", "feed", "stop", "is_speaking", "set_volume", "set_voice", "warmup"]

log = logging.getLogger("tts")

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"
# Token Plan（订阅制）用的是另一套域名，Key 以 tp- 开头。
# 平台两种凭证不通用，这里按 Key 前缀自动切换，省得手动改代码。
TOKEN_PLAN_URL = "https://token-plan-cn.xiaomimimo.com/v1/chat/completions"
MIMO_MODEL = "mimo-v2.5-tts"

CLOUD_SAMPLE_RATE = 24000          # MiMo 流式 pcm16 固定 24kHz

DEFAULT_STYLE = (
    "温和沉稳的中文导游，语速中等偏慢，吐字清晰，"
    "语气亲切但不夸张，像在给身边的朋友做讲解。"
)

DEFAULT_STYLE_EN = (
    "A warm and clear English-speaking tour guide. "
    "Moderate pace, well articulated, friendly but not overly dramatic — "
    "the tone of someone showing a friend around campus."
)


def looks_latin(text: str) -> bool:
    """判断文本主体是否为拉丁字母（用于自动切换风格描述）。"""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if ord(c) < 128) / len(letters) > 0.5

# 队列里的"一句结束"标记。winsound 这类一次性播放的后端靠它判断何时攒够一整句。
class _EndOfSentence:
    __slots__ = ()

    def __repr__(self) -> str:
        return "<EOS>"


_EOS = _EndOfSentence()

PRESET_VOICES = [
    "REDACTED-ROTATE-ME", "冰糖", "茉莉", "苏打", "白桦",
    "Mia", "Chloe", "Milo", "Dean",
]

_HARD_BREAK = "。！？!?；;\n\r"
_SOFT_BREAK = "，,、：:）)】]"
# 实测（2026-09，MiMo-V2.5-TTS 真流式）：首块到达时间与文本长度无关，
# 9 字 1.01s / 65 字 0.90s / 200 字 0.74s；生成速度约 4.4 倍实时。
# 所以分句的目的已经不是"降首字延迟"，而是：
#   1) 降级粒度（云端挂了只丢一句）
#   2) 缓存复用粒度
#   3) 语境自然度（别切太碎，否则句间停顿生硬）
# 因此长度放宽：软标点处 80 字，无软标点 120 字硬切。
_SENTENCE_MAX = 80                 # 超过这个长度就在软标点处切
_SENTENCE_HARD = 120               # 完全没有软标点时的硬切长度
_SENTENCE_MIN = 6                  # 短碎片并入前一句

# 英文常见缩写：句号在这里不是句末，不能切
_ABBREV = {
    "mr", "mrs", "ms", "dr", "prof", "st", "vs", "etc", "inc", "ltd",
    "e.g", "i.e", "u.s", "u.k", "u.n", "a.m", "p.m", "no", "fig",
}


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    api_key: str = field(default_factory=lambda: os.environ.get("MIMO_API_KEY", ""))
    voice: str = field(default_factory=lambda: os.environ.get("TTS_VOICE", "REDACTED-ROTATE-ME"))
    style: str = field(default_factory=lambda: os.environ.get("TTS_STYLE", DEFAULT_STYLE))
    style_en: str = field(default_factory=lambda: os.environ.get("TTS_STYLE_EN", DEFAULT_STYLE_EN))
    timeout: int = field(default_factory=lambda: _env_int("TTS_TIMEOUT", 5))
    deadline: int = field(default_factory=lambda: _env_int("TTS_DEADLINE", 15))
    queue_size: int = field(default_factory=lambda: _env_int("TTS_QUEUE", 2))
    feed_idle: float = field(default_factory=lambda: _env_float("TTS_FEED_IDLE", 1.5))
    cache_dir: str = field(default_factory=lambda: os.environ.get("TTS_CACHE_DIR", ".tts_cache"))
    player: str = field(default_factory=lambda: os.environ.get("TTS_PLAYER", "auto"))
    force_engine: str = field(default_factory=lambda: os.environ.get("TTS_FORCE_ENGINE", "").lower())
    alsa_device: str = field(default_factory=lambda: os.environ.get("TTS_ALSA_DEVICE", ""))
    volume: int = field(default_factory=lambda: _env_int("TTS_VOLUME", 100))
    cache_enabled: bool = field(default_factory=lambda: os.environ.get("TTS_CACHE", "1") == "1")


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------

def split_sentences(text: str) -> List[str]:
    """中文长文本分句。硬标点切句，超长句再按软标点切，过短碎片并入前句。"""
    text = (text or "").strip()
    if not text:
        return []

    raw: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            raw.append("".join(buf).strip())
            buf.clear()

    for i, ch in enumerate(text):
        # 英文句号：只在"句号 + 空格/行尾"时才切，且要跳过缩写和单个字母
        if ch == "." and (i + 1 >= len(text) or text[i + 1] in " \t\n\r"):
            j = i
            while j > 0 and text[j - 1] not in " \t\n\r":
                j -= 1
            token = text[j:i].lower()
            if token not in _ABBREV and len(token) > 1:
                buf.append(ch)
                flush()
                continue
        if ch in _HARD_BREAK:
            buf.append(ch)
            flush()
            continue
        buf.append(ch)
        if ch in _SOFT_BREAK and len(buf) >= _SENTENCE_MAX:
            flush()
        elif len(buf) >= _SENTENCE_HARD:       # 没有任何软标点的极端情况
            flush()
    flush()

    merged: List[str] = []
    for s in raw:
        if not s:
            continue
        if merged and len(s) < _SENTENCE_MIN:
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)
    return merged


def _pcm_duration(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / 2.0 / float(sample_rate) if sample_rate else 0.0


def _apply_gain(pcm: bytes, gain: float) -> bytes:
    """对 int16 PCM 施加增益。gain==1.0 时原样返回，零依赖。"""
    if abs(gain - 1.0) < 1e-3:
        return pcm
    a = array.array("h")
    a.frombytes(pcm)
    for i, v in enumerate(a):
        nv = int(v * gain)
        if nv > 32767:
            nv = 32767
        elif nv < -32768:
            nv = -32768
        a[i] = nv
    return a.tobytes()


def _parse_wav(data: bytes) -> Tuple[bytes, int]:
    """解析 WAV，返回 (pcm16le, sample_rate)。只处理标准 PCM 容器。"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("不是合法的 WAV 数据")
    pos = 12
    sample_rate = 0
    pcm = b""
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            sample_rate = int.from_bytes(body[4:8], "little")
        elif cid == b"data":
            pcm = body
            break
        pos += 8 + size + (size & 1)
    if not pcm or not sample_rate:
        raise ValueError("WAV 缺少 data/fmt 块")
    return pcm, sample_rate


def _wav_header(sample_rate: int, data_len: int) -> bytes:
    import struct
    return b"".join([
        b"RIFF", (36 + data_len).to_bytes(4, "little"), b"WAVE",
        b"fmt ", (16).to_bytes(4, "little"),
        (1).to_bytes(2, "little"), (1).to_bytes(2, "little"),
        sample_rate.to_bytes(4, "little"),
        (sample_rate * 2).to_bytes(4, "little"),
        (2).to_bytes(2, "little"), (16).to_bytes(2, "little"),
        b"data", data_len.to_bytes(4, "little"),
    ])


# --------------------------------------------------------------------------
# 合成后端
# --------------------------------------------------------------------------

class CloudSynth:
    """小米 MiMo-V2.5-TTS。注意：端点是 /v1/chat/completions，不是 /v1/audio/speech。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    sample_rate = CLOUD_SAMPLE_RATE

    def available(self) -> bool:
        return bool(self.cfg.api_key)

    @property
    def endpoint(self) -> str:
        return TOKEN_PLAN_URL if self.cfg.api_key.startswith("tp-") else MIMO_URL

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        """逐块产出 PCM。首块到达时刻就是 TTFB（实测 0.7-1.0s）。"""
        req = urllib.request.Request(
            self.endpoint,
            data=self._payload(text, True),
            headers={
                "api-key": self.cfg.api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            for raw in resp:
                line = raw.strip()
                if not isinstance(line, bytes) or not line.startswith(b"data:"):
                    continue
                body = line[5:].strip()
                if body in (b"[DONE]", b"DONE"):
                    break
                try:
                    evt = json.loads(body.decode("utf-8"))
                except Exception:                          # noqa: BLE001
                    continue
                try:
                    audio = (evt["choices"][0].get("delta") or {}).get("audio")
                except Exception:                          # noqa: BLE001
                    continue
                if audio and audio.get("data"):
                    yield base64.b64decode(audio["data"])

    def _payload(self, text: str, stream: bool) -> bytes:
        style = self.cfg.style_en if looks_latin(text) else self.cfg.style
        body = {
            "model": MIMO_MODEL,
            "messages": [
                {"role": "user", "content": style},
                {"role": "assistant", "content": text},
            ],
            "audio": {
                "format": "pcm16" if stream else "wav",
                "voice": self.cfg.voice,
            },
            "stream": stream,
        }
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    def synthesize(self, text: str) -> Tuple[bytes, int]:
        """返回 (pcm16le, sample_rate)。流式优先，失败退回非流式。"""
        last_err: Optional[Exception] = None
        for stream in (True, False):
            try:
                return self._request(text, stream)
            except Exception as exc:                      # noqa: BLE001
                last_err = exc
                log.warning("云端合成(stream=%s)失败: %s", stream, exc)
        raise RuntimeError(f"云端合成失败: {last_err}")

    def _request(self, text: str, stream: bool) -> Tuple[bytes, int]:
        req = urllib.request.Request(
            self.endpoint,
            data=self._payload(text, stream),
            headers={
                "api-key": self.cfg.api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
            if stream:
                return self._read_sse(resp), CLOUD_SAMPLE_RATE
            return _parse_wav(resp.read())

    @staticmethod
    def _read_sse(resp) -> bytes:
        """解析 SSE，拼接 delta.audio.data（base64 的 pcm16）。"""
        chunks: List[bytes] = []
        for raw in resp:
            line = raw.strip()
            if not line:
                continue
            if isinstance(line, bytes):
                if not line.startswith(b"data:"):
                    continue
                payload = line[5:].strip()
                if payload in (b"[DONE]", b"DONE"):
                    break
                try:
                    evt = json.loads(payload.decode("utf-8"))
                except Exception:                          # noqa: BLE001
                    continue
            else:
                continue
            try:
                delta = evt["choices"][0].get("delta") or {}
                audio = delta.get("audio")
                if audio and audio.get("data"):
                    chunks.append(base64.b64decode(audio["data"]))
            except Exception:                              # noqa: BLE001
                continue
        if not chunks:
            raise RuntimeError("流式响应中没有音频数据")
        return b"".join(chunks)


class MockSynth:
    """离线自检用：生成正弦音，时长随文本长度变化。不产生任何网络请求。"""

    sample_rate = CLOUD_SAMPLE_RATE

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def available(self) -> bool:
        return True

    def synthesize_stream(self, text: str) -> Iterator[bytes]:
        pcm, _ = self.synthesize(text)
        for off in range(0, len(pcm), 8192):
            yield pcm[off:off + 8192]

    def synthesize(self, text: str) -> Tuple[bytes, int]:
        seconds = min(3.0, 0.4 + len(text) * 0.045)
        sr = CLOUD_SAMPLE_RATE
        n = int(sr * seconds)
        freq = 220 + (hash(text) % 120)
        a = array.array("h", [
            int(8000 * math.sin(2 * math.pi * freq * i / sr)) for i in range(n)
        ])
        return a.tobytes(), sr


# --------------------------------------------------------------------------
# 播放后端
# --------------------------------------------------------------------------

class Player:
    """统一播放接口，自动探测后端。切采样率时重开流，句内不混采样率。"""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.backend = self._detect(cfg.player)
        self.sr: Optional[int] = None
        self._pa = None
        self._stream = None
        self._proc = None
        self._sd = None
        log.info("播放后端: %s", self.backend)

    @staticmethod
    def _detect(preferred: str) -> str:
        if preferred and preferred != "auto":
            return preferred
        for name, probe in (
            ("pyaudio", "_probe_pyaudio"),
            ("sounddevice", "_probe_sounddevice"),
            ("winsound", "_probe_winsound"),
            ("aplay", "_probe_aplay"),
        ):
            if getattr(Player, probe)():
                return name
        return "dryrun"

    @staticmethod
    def _probe_pyaudio() -> bool:
        try:
            import pyaudio        # noqa: F401
            return True
        except Exception:         # noqa: BLE001
            return False

    @staticmethod
    def _probe_sounddevice() -> bool:
        try:
            import sounddevice    # noqa: F401
            return True
        except Exception:         # noqa: BLE001
            return False

    @staticmethod
    def _probe_winsound() -> bool:
        """Windows 自带，零依赖。开发机上不装任何东西也能出声。"""
        if not sys.platform.startswith("win"):
            return False
        try:
            import winsound     # noqa: F401
            return True
        except Exception:       # noqa: BLE001
            return False

    @staticmethod
    def _probe_aplay() -> bool:
        return bool(shutil.which("aplay"))

    # ---- 生命周期 ----

    def open(self, sample_rate: int) -> None:
        if self.sr == sample_rate and self._opened():
            return
        self.close()
        if self.backend == "pyaudio":
            import pyaudio
            self._pa = pyaudio.PyAudio()
            self._stream = self._pa.open(
                format=pyaudio.paInt16, channels=1, rate=sample_rate,
                output=True, frames_per_buffer=1024,
            )
        elif self.backend == "sounddevice":
            import sounddevice as sd
            self._sd = sd
            self._stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="int16")
            self._stream.start()
        elif self.backend == "aplay":
            cmd = ["aplay", "-q", "-t", "raw", "-f", "S16_LE", "-c", "1", "-r", str(sample_rate)]
            if self.cfg.alsa_device:
                cmd += ["-D", self.cfg.alsa_device]
            cmd.append("-")
            self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.sr = sample_rate

    def _opened(self) -> bool:
        return any([self._stream is not None, self._proc is not None])

    def write(self, pcm: bytes) -> None:
        if self.backend == "pyaudio" and self._stream is not None:
            self._stream.write(pcm)
        elif self.backend == "sounddevice" and self._stream is not None:
            self._stream.write(pcm)
        elif self.backend == "aplay" and self._proc is not None:
            try:
                self._proc.stdin.write(pcm)
                self._proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                pass
        else:
            time.sleep(_pcm_duration(pcm, self.sr or CLOUD_SAMPLE_RATE))

    @property
    def streaming(self) -> bool:
        """是否支持"边收边播"。

        winsound 只能一次播一个文件，没法往正在播的流里追加数据，
        所以必须攒够一整句再播（代价：Windows 上首字延迟多 1-2 秒）。
        树莓派用 aplay / pyaudio 是真流式，不受影响。
        """
        return self.backend in ("pyaudio", "sounddevice", "aplay")

    def play_buffer(self, pcm: bytes, sample_rate: int, stop_event) -> None:
        """整段播放（winsound / dryrun 走这条路）。"""
        if self.backend == "null":
            # 预热模式：只走合成与缓存写入，不出声、也不按音频时长空等。
            # 配合 precache_cli.py 使用，见文件头说明。
            return
        if self.backend == "winsound":
            self._play_winsound(pcm, sample_rate, stop_event)
        else:
            self._play_dryrun(pcm, sample_rate, stop_event)

    def _play_winsound(self, pcm: bytes, sample_rate: int, stop_event) -> None:
        import tempfile
        import winsound

        # 每次用独立文件名：SND_ASYNC 是异步读文件的，
        # 复用同一个路径会让下一个块覆盖掉正在播的文件 —— 听起来就是一卡一卡的。
        fd, path = tempfile.mkstemp(prefix="tts_", suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(_wav_header(sample_rate, len(pcm)))
                f.write(pcm)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            deadline = time.time() + _pcm_duration(pcm, sample_rate) + 0.05
            while time.time() < deadline:
                if stop_event.is_set():
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    return
                time.sleep(0.03)
        finally:
            try:
                os.unlink(path)
            except Exception:                              # noqa: BLE001
                pass

    def _play_dryrun(self, pcm: bytes, sample_rate: int, stop_event) -> None:
        total = _pcm_duration(pcm, sample_rate)
        step = 0.03
        waited = 0.0
        while waited < total:
            if stop_event.is_set():
                return
            time.sleep(min(step, total - waited))
            waited += step

    def abort(self) -> None:
        """立刻停止硬件输出，不留缓冲。"""
        try:
            if self.backend == "winsound":
                import winsound
                winsound.PlaySound(None, winsound.SND_PURGE)
                return
            if self.backend == "pyaudio" and self._stream is not None:
                self._stream.stop_stream()
            elif self.backend == "sounddevice" and self._stream is not None:
                self._stream.abort(ignore_errors=True)
            elif self.backend == "aplay" and self._proc is not None:
                try:
                    self._proc.stdin.close()
                except Exception:                          # noqa: BLE001
                    pass
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=0.5)
                except Exception:                          # noqa: BLE001
                    try:
                        self._proc.kill()
                    except Exception:                      # noqa: BLE001
                        pass
        except Exception as exc:                           # noqa: BLE001
            log.debug("abort 异常(可忽略): %s", exc)

    def close(self) -> None:
        try:
            if self.backend == "pyaudio":
                if self._stream is not None:
                    try:
                        self._stream.stop_stream()
                        self._stream.close()
                    except Exception:                      # noqa: BLE001
                        pass
                if self._pa is not None:
                    try:
                        self._pa.terminate()
                    except Exception:                      # noqa: BLE001
                        pass
            elif self.backend == "sounddevice":
                if self._stream is not None:
                    try:
                        self._stream.stop()
                        self._stream.close()
                    except Exception:                      # noqa: BLE001
                        pass
            elif self.backend == "aplay":
                if self._proc is not None:
                    try:
                        self._proc.stdin.close()
                    except Exception:                      # noqa: BLE001
                        pass
                    try:
                        # EOF 后 aplay 要把自身缓冲 + ALSA 缓冲里的尾音播完才退出。
                        # 只等 1s 会把最后几个字 kill 掉（实测截断尾音），给足 3s。
                        self._proc.wait(timeout=3.0)
                    except Exception:                      # noqa: BLE001
                        log.warning("aplay 尾音排空超时（3s）强制结束，尾音可能被截断")
                        try:
                            self._proc.kill()
                        except Exception:                  # noqa: BLE001
                            pass
        finally:
            self._stream = None
            self._pa = None
            self._proc = None
            self.sr = None


try:
    import numpy  # noqa: F401
    _HAS_NUMPY = True
except Exception:  # noqa: BLE001
    _HAS_NUMPY = False


# --------------------------------------------------------------------------
# 主类
# --------------------------------------------------------------------------

class TTS:
    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = cfg or Config()
        self.cloud = CloudSynth(self.cfg)
        self.mock = MockSynth(self.cfg)
        self.player = Player(self.cfg)

        self._q: "queue.Queue[Optional[Tuple[bytes, int]]]" = queue.Queue(self.cfg.queue_size)
        self._stop = threading.Event()
        self._producer_done = threading.Event()
        self._speaking = False
        # "播报代号"：每次 speak()/feed() 起步都 +1。
        # 被抢占的那次播报，它的生产线程可能还卡在合成器的网络读里 ——
        # 等新播报把 _stop 清掉之后，那个旧线程会"复活"，把旧句子继续推进队列，
        # 混在新播报里播出来（实测：本该 1.5s 的新播报播了 7.18s）。
        # 所有入队动作都带上代号，代号不等于当前值就丢弃，从根上堵住串台。
        self._gen = 0
        self._lock = threading.RLock()
        self._producer: Optional[threading.Thread] = None
        self._consumer: Optional[threading.Thread] = None

        # feed() 的待合成句子队列：生产者在播报期间持续消费，
        # feed 不断追加。为 None 表示"本次播报应结束"。
        self._sentences: "queue.Queue[Optional[str]]" = queue.Queue()

        self._volume = self.cfg.volume
        self._gain = 1.0
        self._consecutive_fail = 0
        self._give_up = False
        self.last_metrics: dict = {}

        if self.cfg.cache_enabled:
            try:
                os.makedirs(self.cfg.cache_dir, exist_ok=True)
            except Exception:                              # noqa: BLE001
                log.warning("缓存目录创建失败，已关闭缓存")
                self.cfg.cache_enabled = False

        self.set_volume(self._volume)

    # ---------------- 公开接口 ----------------

    def speak(self, text: str, wait: bool = True) -> bool:
        try:
            return self._speak(text, wait)
        except Exception as exc:                           # noqa: BLE001
            log.error("speak 发生异常: %s", exc, exc_info=True)
            self._speaking = False
            return False

    def stop(self) -> None:
        self._stop.set()
        self._drain_queue()
        self._drain_sentences()
        self.player.abort()
        if isinstance(self.last_metrics, dict) and self._speaking:
            self.last_metrics["stopped"] = True

    def feed(self, text: str) -> bool:
        """追加文本到当前播报队列，不打断正在播的声音。

        供流式 LLM 用：生成完一句就调一次 feed()，实现"边生成边播"。
        - 当前正在播报（speak 或 feed 模式）→ 追加到队尾，返回 True
        - 当前空闲 → 启动一条 feed 模式流水线，返回 True
        - stop() 已触发 → 丢弃，返回 False

        对非流式 LLM 无任何影响：它照常一次性调 speak() 即可。
        """
        text = (text or "").strip()
        if not text:
            return True
        try:
            with self._lock:
                if self._stop.is_set():
                    return False

                if not self._speaking:
                    # 空闲：启动 feed 模式流水线
                    return self._start_feed(text)

                # 正在播报：追加句子
                for sent in split_sentences(text):
                    self._sentences.put(sent)
                return True
        except Exception as exc:                      # noqa: BLE001
            log.error("feed 发生异常: %s", exc, exc_info=True)
            return False

    def _start_feed(self, text: str) -> bool:
        sentences = split_sentences(text)
        if not sentences:
            return True
        self._gen += 1                       # 换代，理由见 __init__ 里 self._gen 的注释
        gen = self._gen
        self._stop.clear()
        self._producer_done.clear()
        self._drain_queue()
        self._drain_sentences()
        self._speaking = True
        self._give_up = False
        self._consecutive_fail = 0

        metrics = {
            "chars": 0,              # feed 模式下由 _produce_feed 逐句累加
            "sentences": 0,
            "t_start": time.time(),
            "first_audio_at": None,
            "engines": {},
            "degraded": False,
            "truncated": False,
            "stopped": False,
            "failed": 0,
        }
        self.last_metrics = metrics

        for sent in sentences:
            self._sentences.put(sent)

        self._consumer = threading.Thread(target=self._consume, args=(gen,),
                                          name="tts-play", daemon=True)
        self._consumer.start()
        self._producer = threading.Thread(
            target=self._produce_feed, args=(metrics, gen), name="tts-synth", daemon=True
        )
        self._producer.start()
        return True

    def _drain_sentences(self) -> None:
        try:
            while True:
                self._sentences.get_nowait()
        except queue.Empty:
            pass

    def is_speaking(self) -> bool:
        return self._speaking

    def set_volume(self, level: int) -> None:
        level = max(0, min(100, int(level)))
        self._volume = level
        if self._set_system_volume(level):
            self._gain = 1.0
            log.info("音量 %d%%（系统级 amixer）", level)
        else:
            # 100% 对应 1.0，不做超额放大，避免削顶失真。
            # 树莓派上走 amixer 时才有额外的系统级余量。
            self._gain = level / 100.0
            log.info("音量 %d%%（软件增益 %.2f）", level, self._gain)

    def set_voice(self, voice_id: str) -> None:
        self.cfg.voice = voice_id
        log.info("音色切换为 %s", voice_id)

    @property
    def volume(self) -> int:
        return self._volume

    def warmup(self) -> None:
        """预热：检查云端凭据、记录运行参数。不发声、不发网络请求。"""
        log.info("warmup: 云端可用=%s", self.cloud.available())
        if not self.cloud.available():
            log.error("warmup: 未配置 MIMO_API_KEY —— 本模块将完全无法出声")
            return
        log.info("warmup: 音色=%s，超时=%ss，单句时限=%ss",
                 self.cfg.voice, self.cfg.timeout, self.cfg.deadline)
        if self.cfg.cache_enabled:
            log.info("warmup: 缓存目录=%s", self.cfg.cache_dir)

    # ---------------- 内部 ----------------

    def _speak(self, text: str, wait: bool) -> bool:
        with self._lock:
            if self._speaking:
                log.info("speak: 上一次播报未结束，先打断")
                self.stop()
                self._join_threads(1.0)

            sentences = split_sentences(text)
            if not sentences:
                return True

            # 先换代再清 _stop。顺序反了会留一个窗口：旧生产线程正好在
            # "已过 _stop 检查、还没入队"之间，清掉 _stop 后它就合法地把旧数据推进去了。
            self._gen += 1
            gen = self._gen
            self._stop.clear()
            self._producer_done.clear()
            self._drain_queue()
            self._speaking = True
            self._give_up = False
            self._consecutive_fail = 0

            metrics = {
                "chars": len(text),
                "sentences": len(sentences),
                "t_start": time.time(),
                "first_audio_at": None,
                "engines": {},
                "degraded": False,
                "truncated": False,
                "stopped": False,
                "failed": 0,
            }
            self.last_metrics = metrics

            self._consumer = threading.Thread(target=self._consume, args=(gen,),
                                          name="tts-play", daemon=True)
            self._consumer.start()
            self._producer = threading.Thread(
                target=self._produce, args=(sentences, metrics, gen),
                name="tts-synth", daemon=True
            )
            self._producer.start()

        if wait:
            self._join_threads(None)
            return (not metrics["stopped"]) and metrics["failed"] < metrics["sentences"]
        return True

    def _join_threads(self, timeout: Optional[float]) -> None:
        for t in (self._producer, self._consumer):
            if t is not None and t.is_alive():
                t.join(timeout)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    # ---- 生产者 ----

    def _stale(self, gen: Optional[int]) -> bool:
        """本次播报是否已经被新一代取代（被打断 / 又起了一条）。

        gen 为 None 表示不做代号校验，只看 _stop（兼容旧调用）。
        """
        if self._stop.is_set():
            return True
        return gen is not None and self._gen != gen

    def _current(self, gen: Optional[int]) -> bool:
        """本次播报是否仍是"当代"的（有权收尾/改状态）。"""
        return gen is None or self._gen == gen

    def _produce(self, sentences: List[str], metrics: dict, gen: int) -> None:
        # speak 一次性场景：遍历固定句子列表。
        try:
            for sent in sentences:
                if self._stale(gen):
                    break
                if not self._stream_sentence(sent, metrics, gen):
                    metrics["failed"] += 1
        finally:
            # 只有"当代"的生产线程有权收尾。否则一个残留的旧线程会把
            # _producer_done 提前置位，让新播报的消费者以为活儿干完了、提前退出。
            if self._gen == gen:
                self._producer_done.set()
            self._put_blocking(None, gen)

    def _produce_feed(self, metrics: dict, gen: int) -> None:
        # feed 流式场景：持续从增量队列取句，靠 idle 超时收尾。
        idle_since: Optional[float] = None
        try:
            while True:
                if self._stale(gen):
                    break
                try:
                    sent = self._sentences.get(timeout=0.2)
                except queue.Empty:
                    if idle_since is None:
                        idle_since = time.time()
                    elif time.time() - idle_since > self.cfg.feed_idle:
                        break
                    continue
                idle_since = None
                if sent is None:
                    continue
                if not self._stream_sentence(sent, metrics, gen):
                    metrics["failed"] += 1
                else:
                    metrics["sentences"] += 1
                    metrics["chars"] += len(sent)
        finally:
            if self._gen == gen:
                self._producer_done.set()
            self._put_blocking(None, gen)

    @staticmethod
    def _mark_first(metrics: dict) -> None:
        if metrics["first_audio_at"] is None:
            metrics["first_audio_at"] = time.time() - metrics["t_start"]

    def _stream_sentence(self, text: str, metrics: dict,
                         gen: Optional[int] = None) -> bool:
        """流式合成一句并推入播放队列。返回是否发出了音频。

        gen：播报代号（见 __init__ 里 self._gen 的注释）。传入后，
        一旦本次播报被新一代取代，就立刻停手、并且不再往队列里推任何数据。
        """
        if self._stale(gen):
            return False

        cached = self._cache_get(self._cache_key(text))
        if cached is not None:
            pcm, sr = cached
            metrics["engines"]["cache"] = metrics["engines"].get("cache", 0) + 1
            self._mark_first(metrics)
            self._put_blocking((pcm, sr), gen)
            self._put_blocking(_EOS, gen)
            return True

        engine = self._pick_engine()
        synth = {"cloud": self.cloud, "mock": self.mock}.get(engine)
        if synth is None:
            return False

        buf: List[bytes] = []
        emitted = False
        complete = False                # 整句是否收完 —— 只有收完才允许写缓存
        t0 = time.time()
        try:
            for block in synth.synthesize_stream(text):
                if self._stale(gen):
                    break
                if not emitted and time.time() - t0 > self.cfg.deadline:
                    raise RuntimeError(f"首块超过 {self.cfg.deadline}s 未到达")
                buf.append(block)
                emitted = True
                self._mark_first(metrics)
                self._put_blocking((block, synth.sample_rate), gen)
            else:
                complete = True         # 没被 break、也没抛异常 = 这一句完整收到了
            if emitted:
                self._put_blocking(_EOS, gen)
        except Exception as exc:                           # noqa: BLE001
            log.warning("引擎 %s 流式失败: %s（已发出 %d 块）", engine, exc, len(buf))
            if emitted:
                self._put_blocking(_EOS, gen)
            if engine == "cloud":
                self._consecutive_fail += 1
                if self._consecutive_fail >= 2:
                    self._give_up = True
                    log.warning("云端连续失败 %d 次，本次播报剩余句子直接跳过（不再等超时）",
                                self._consecutive_fail)
            if emitted:
                metrics["truncated"] = True               # 中途断流，已播部分不回退

        if emitted:
            if engine == "cloud":
                self._consecutive_fail = 0
            metrics["engines"][engine] = metrics["engines"].get(engine, 0) + 1
            # 只缓存**完整**的句子。被打断或中途断流的半句一旦写进缓存，
            # "走回头路"（命中缓存）就会播出被砍断的音频，而且再也修不回来
            # （缓存事先不知道它是残的）。实测：讲 2.5 秒打断，一句 2.08s 的
            # 完整音频被存成 0.80s —— 见 test_scene_switch.py 情景 5。
            if self.cfg.cache_enabled and complete:
                self._cache_put(self._cache_key(text), (b"".join(buf), synth.sample_rate))
        return emitted

    def _put_blocking(self, item, gen: Optional[int] = None) -> None:
        """入队。带 gen 时，只要本次播报已被新一代取代就直接丢弃 ——
        这是"串台"的最终防线：旧生产线程即使晚一步醒来，也进不了新播报的队列。"""
        while not self._stale(gen):
            try:
                self._q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue

    def _pick_engine(self) -> str:
        if self.cfg.force_engine in ("cloud", "mock"):
            return self.cfg.force_engine
        if self._give_up:
            return ""                        # 本次播报已放弃，不再发请求
        return "cloud" if self.cloud.available() else ""

    # ---- 消费者 ----

    def _consume(self, gen: Optional[int] = None) -> None:
        buf = bytearray()
        buf_sr: Optional[int] = None

        def flush() -> None:
            nonlocal buf, buf_sr
            if buf and buf_sr:
                self.player.play_buffer(bytes(buf), buf_sr, self._stop)
            buf = bytearray()
            buf_sr = None

        try:
            while True:
                if self._stop.is_set():
                    break
                try:
                    item = self._q.get(timeout=0.2)
                except queue.Empty:
                    if self._producer_done.is_set() and self._q.empty():
                        break
                    continue
                if item is None:
                    break
                if item == _EOS:                           # 一句结束
                    if self._stop.is_set():
                        break
                    flush()
                    continue
                pcm, sr = item
                if self._stop.is_set():
                    break
                if self.player.streaming:
                    flush()
                    self._play_chunk(pcm, sr)
                else:
                    if buf_sr is not None and sr != buf_sr:
                        flush()
                    buf_sr = sr
                    buf += pcm
            if not self._stop.is_set():
                flush()
        finally:
            # 只有"当代"的消费者有权收尾。一个被抢占后才退出的旧消费者
            # 如果照常执行 player.close()，会把**新播报刚打开的播放设备**关掉
            # （Player 是共用对象，Pi 上就是那个 aplay 子进程），
            # 表现为"新讲解开头一小段没声音"。
            if self._current(gen):
                try:
                    self.player.close()
                finally:
                    self._speaking = False

    def _play_chunk(self, pcm: bytes, sample_rate: int) -> None:
        data = _apply_gain(pcm, self._gain)
        self.player.open(sample_rate)
        block = 2048 * 2                                   # 2048 帧 * 2 字节
        for off in range(0, len(data), block):
            if self._stop.is_set():
                return
            self.player.write(data[off:off + block])

    # ---- 音量 ----

    @staticmethod
    def _set_system_volume(level: int) -> bool:
        if not shutil.which("amixer"):
            return False
        for ctrl in ("Master", "PCM", "Speaker"):
            try:
                proc = subprocess.run(
                    ["amixer", "sset", ctrl, f"{level}%"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2,
                )
                if proc.returncode == 0:
                    return True
            except Exception:                              # noqa: BLE001
                continue
        return False

    # ---- 缓存 ----

    def _cache_key(self, text: str) -> str:
        raw = f"{self.cfg.force_engine}|{self.cfg.voice}|{text}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def _cache_path(self, key: str, sample_rate: int) -> str:
        return os.path.join(self.cfg.cache_dir, f"{key}.{sample_rate}.pcm")

    def _cache_get(self, key: str) -> Optional[Tuple[bytes, int]]:
        if not self.cfg.cache_enabled:
            return None
        try:
            for name in os.listdir(self.cfg.cache_dir):
                if name.startswith(key + "."):
                    sr = int(name.split(".")[1])
                    with open(os.path.join(self.cfg.cache_dir, name), "rb") as f:
                        return f.read(), sr
        except Exception:                                  # noqa: BLE001
            return None
        return None

    def _cache_put(self, key: str, item: Tuple[bytes, int]) -> None:
        try:
            pcm, sr = item
            with open(self._cache_path(key, sr), "wb") as f:
                f.write(pcm)
        except Exception as exc:                           # noqa: BLE001
            log.debug("缓存写入失败: %s", exc)

    # ---- 收尾 ----

    def close(self) -> None:
        self.stop()
        self._join_threads(1.0)
        self.player.close()

    def summary(self) -> str:
        m = self.last_metrics
        if not m:
            return "(无播报记录)"
        total = time.time() - m["t_start"]
        first = m["first_audio_at"]
        return (
            f"字数={m['chars']} 句数={m['sentences']} "
            f"首字延迟={('%.2fs' % first) if first else 'N/A'} "
            f"总耗时={total:.2f}s 引擎={m['engines']} "
            f"降级={m['degraded']} 失败句={m['failed']} 被打断={m['stopped']}"
        )


# --------------------------------------------------------------------------
# 模块级单例（给 Agent 主程序直接 import 用）
# --------------------------------------------------------------------------

_default: Optional[TTS] = None
_default_lock = threading.Lock()


def _get() -> TTS:
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = TTS()
    return _default


def speak(text: str, wait: bool = True) -> bool:
    return _get().speak(text, wait)


def feed(text: str) -> bool:
    return _get().feed(text)


def stop() -> None:
    _get().stop()


def is_speaking() -> bool:
    return _get().is_speaking()


def set_volume(level: int) -> None:
    _get().set_volume(level)


def set_voice(voice_id: str) -> None:
    _get().set_voice(voice_id)


def warmup() -> None:
    _get().warmup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    text = sys.argv[1] if len(sys.argv) > 1 else "你好，我是你的导游小熊。"
    t = TTS()
    t.warmup()
    ok = t.speak(text)
    print("speak ->", ok)
    print(t.summary())
