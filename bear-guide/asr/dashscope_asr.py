# -*- coding: utf-8 -*-
"""阿里云 DashScope 语音识别（Paraformer）真实实现。

链路：录音（sounddevice）→ 上传音频到公网 URL → Paraformer 文件识别 → 文本。

依赖：
    - sounddevice（录音，树莓派上先 `sudo apt install libportaudio2` 再 pip install）
    - DASHSCOPE_API_KEY（与 LLM / 识图共用同一个阿里云 Key）

说明：
    麦克风 + OSS 存储到位后，本文件即可真实工作。当前无麦克风环境会自动
    降级为「静默」，绝不抛异常、不卡住主链路（契约要求）。
"""

import asyncio
import json
import os
import time
import urllib.error
import urllib.request

from common.types import ASRResult
from asr.asr_interface import ASRInterface

# Paraformer 非实时语音识别（文件转写）接口
ASR_SUBMIT_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"


class DashScopeASR(ASRInterface):
    """DashScope Paraformer 语音识别（待麦克风 + OSS 硬件接入）。"""

    def __init__(self, api_key: str = None, model: str = "paraformer-v2",
                 sample_rate: int = 16000):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.model = model
        self.sample_rate = sample_rate

    # ------------------------------------------------------------------
    # 对外契约：listen()
    # ------------------------------------------------------------------
    async def listen(self, timeout: float = 5.0) -> ASRResult:
        """聆听一段游客语音，返回识别文字。失败一律降级为静默，不抛异常。"""
        if not self.api_key:
            return ASRResult(success=False, text="", is_silence=True,
                             error_msg="未配置 DASHSCOPE_API_KEY，语音识别不可用")

        try:
            # 1. 录音
            wav_path = await asyncio.to_thread(self._record_audio, timeout)

            # 2. 上传音频到公网 URL（OSS / DashScope 上传接口）
            file_url = await asyncio.to_thread(self._upload_audio, wav_path)

            # 3. 调 Paraformer 识别
            text = await asyncio.to_thread(self._transcribe, file_url)

            # 4. 判断静默
            if not text or not text.strip():
                return ASRResult(success=True, text="", is_silence=True)
            return ASRResult(success=True, text=text.strip(), is_silence=False)

        except Exception as exc:
            return ASRResult(success=False, text="", is_silence=True,
                             error_msg=str(exc))

    # ------------------------------------------------------------------
    # 1. 录音（同步 IO，放子线程）
    # ------------------------------------------------------------------
    def _record_audio(self, timeout: float) -> str:
        """用 sounddevice 录 timeout 秒，返回 wav 文件路径。"""
        import wave
        import tempfile

        import sounddevice as sd

        duration = min(timeout, 15.0)  # 最多录 15 秒
        audio = sd.rec(int(duration * self.sample_rate),
                       samplerate=self.sample_rate, channels=1, dtype="int16")
        sd.wait()  # 阻塞直到录完

        path = os.path.join(tempfile.gettempdir(), f"asr_{int(time.time())}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio.tobytes())
        return path

    # ------------------------------------------------------------------
    # 2. 上传音频（TODO：需 OSS 或 DashScope 文件上传接口）
    # ------------------------------------------------------------------
    def _upload_audio(self, wav_path: str) -> str:
        """把本地音频上传到公网，返回可访问的 file_url。

        TODO(麦克风+OSS 到位后补)：Paraformer 需要音频的公网 URL（HTTP/HTTPS）。
        推荐用阿里云 OSS 存储（稳定、不限流）。这里先抛错，由 listen() 降级兜底。
        """
        raise NotImplementedError(
            "音频上传待接入 OSS / DashScope 文件上传接口（麦克风到位后补）")

    # ------------------------------------------------------------------
    # 3. 调 Paraformer 识别（提交任务 + 轮询）
    # ------------------------------------------------------------------
    def _transcribe(self, file_url: str, timeout: float = 30.0) -> str:
        """提交识别任务并轮询，返回转写文本。"""
        task_id = self._submit_task(file_url)
        return self._poll_result(task_id, timeout)

    def _submit_task(self, file_url: str) -> str:
        body = json.dumps({
            "model": self.model,
            "input": {"file_urls": [file_url]},
            "parameters": {"channel_id": [0]},
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            ASR_SUBMIT_URL, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["output"]["task_id"]

    def _poll_result(self, task_id: str, timeout: float) -> str:
        """轮询任务状态，SUCCEEDED 后返回文本。"""
        url = f"{ASR_SUBMIT_URL}/{task_id}"
        deadline = time.time() + timeout

        while time.time() < deadline:
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self.api_key}",
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            status = data["output"].get("task_status", "")
            if status == "SUCCEEDED":
                return self._extract_text(data)
            if status == "FAILED":
                raise RuntimeError(f"识别失败：{data['output'].get('message', status)}")

            time.sleep(1)  # PENDING / RUNNING：等 1 秒再查

        raise TimeoutError(f"识别超时（{timeout}s）")

    def _extract_text(self, data: dict) -> str:
        """从返回结果里提取转写文本（兼容多种结构，以实测为准）。"""
        results = data["output"].get("results", [])
        if not results:
            return ""
        first = results[0]

        # 结构 1：直接给文本
        if isinstance(first.get("text"), str):
            return first["text"]
        # 结构 2：句子列表
        sentences = first.get("sentences", [])
        if sentences:
            return "".join(s.get("text", "") for s in sentences)
        # 结构 3：结果文件 URL（需二次下载）
        if first.get("transcription_url"):
            with urllib.request.urlopen(first["transcription_url"], timeout=20) as resp:
                return resp.read().decode("utf-8")
        return ""
