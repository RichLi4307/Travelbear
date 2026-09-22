"""
vision/vision_qwen_vl.py —— 云端 Qwen-VL-Max 识图实现
技术路线：采集一帧 → OpenCV 预处理 → 云端多模态大模型 → 返回 SceneInfo
"""
import logging
import os
import time
import base64
import asyncio
from pathlib import Path
from typing import Optional

import cv2
from openai import AsyncOpenAI

from common.types import SceneInfo
from vision.vision_interface import VisionInterface

log = logging.getLogger("agent.vision")

# ============ 配置 ============
CAPTURE_DIR = Path(__file__).parent.parent / "captures"
CAPTURE_DIR.mkdir(exist_ok=True)

# 限定识别的四个景点
TARGET_LANDMARKS = ["外滩", "静安寺", "松江广富林", "滴水湖"]

PROMPT = f"""你是景区导览助手，请根据图片判断这是哪个景点。
可选景点：{'、'.join(TARGET_LANDMARKS)}

输出JSON，字段如下：
- scene_summary: 一句话场景描述，如"黄浦江畔的外滩风光，远处可见东方明珠"
- landmark_guess: 如果判断是上面4个景点之一，填景点名；不确定就空字符串
- objects: 画面中主要元素列表
- confidence: 0~1自评置信度

只输出JSON，不要解释。"""


class QwenVLVision(VisionInterface):
    """云端 Qwen-VL-Max 识图实现"""

    def __init__(self, image_source: str = "camera", api_key: str = None):
        """
        Args:
            image_source: 图像来源
                - "camera": 打开 USB 摄像头实时采集
                - 本地图片路径: 调试时读指定图片
            api_key: DashScope API Key，不传则从环境变量 DASHSCOPE_API_KEY 读取
        """
        self.image_source = image_source
        self.client = AsyncOpenAI(
            api_key=api_key or os.environ.get("DASHSCOPE_API_KEY", ""),
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def _grab_frame_sync(self) -> str:
        """同步采集一帧（在子线程中调用）。

        摄像头 USB 偶发掉线重枚举（和 WiFi 共用 USB2 集线器），打开/读帧
        失败时重试 3 次、间隔 0.6s；持续失败才抛错（走业务降级链路）。
        """
        if self.image_source != "camera":
            frame = cv2.imread(self.image_source)
            if frame is None:
                raise FileNotFoundError(f"读不到图片: {self.image_source}")
            return self._save_frame(frame)

        last_err: Optional[Exception] = None
        for attempt in range(3):
            cap = cv2.VideoCapture(0)
            ok, frame = cap.read()
            cap.release()
            if ok:
                return self._save_frame(frame)
            last_err = RuntimeError("摄像头打开或读帧失败")
            log.warning("取景第 %d/3 次失败，0.6s 后重试", attempt + 1)
            time.sleep(0.6)
        raise last_err

    @staticmethod
    def _save_frame(frame) -> str:
        """预处理（长边 ≤1024、JPEG 质量 80）并落盘，返回图片路径。"""
        h, w = frame.shape[:2]
        scale = 1024 / max(h, w)
        if scale < 1:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        out_path = str(CAPTURE_DIR / f"capture_{int(time.time())}.jpg")
        cv2.imwrite(out_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return out_path

    async def get_scene(self, timeout: float = 8.0) -> SceneInfo:
        """
        采集一帧并返回场景描述（异步实现）。

        Args:
            timeout: 超时时间（秒），默认 8.0

        Returns:
            SceneInfo: 识别结果
        """
        try:
            # 1. 采集图片（同步IO放子线程，不阻塞事件循环）
            log.info("开始取景识别，来源=%s", self.image_source)
            img_path = await asyncio.to_thread(self._grab_frame_sync)
            log.debug("取景完成：%s", img_path)

            # 2. 转 base64
            with open(img_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

            # 3. 调云端 VLM（带超时）
            start = time.monotonic()
            resp = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model="qwen-vl-max",
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                            {"type": "text", "text": PROMPT}
                        ]
                    }],
                    response_format={"type": "json_object"},
                ),
                timeout=timeout,
            )
            log.info("云端识图完成：耗时=%.1fs", time.monotonic() - start)

            # 4. 解析返回
            import json
            data = json.loads(resp.choices[0].message.content)

            # 组装 scene_description：场景描述 + 地标猜测
            scene_desc = data.get("scene_summary", "")
            landmark = data.get("landmark_guess", "")
            if landmark:
                scene_desc = f"[{landmark}] {scene_desc}"
            log.info("识图结果：%s（置信度 %.2f）", scene_desc,
                     float(data.get("confidence", 0.0)))

            return SceneInfo(
                success=True,
                scene_description=scene_desc,
                confidence=float(data.get("confidence", 0.0)),
                error_msg="",
            )

        except asyncio.TimeoutError:
            log.warning("云端识图超时（%.0fs）", timeout)
            return SceneInfo(
                success=False,
                scene_description="",
                confidence=0.0,
                error_msg=f"云端识别超时（{timeout}s）",
            )
        except Exception as e:
            log.warning("识图失败：%s", e)
            return SceneInfo(
                success=False,
                scene_description="",
                confidence=0.0,
                error_msg=str(e),
            )
