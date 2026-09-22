"""
vision/vision_qwen_vl.py —— 云端 Qwen-VL-Max 识图实现
技术路线：采集一帧 → OpenCV 预处理 → 云端多模态大模型 → 返回 SceneInfo
"""
import logging
import os
import subprocess
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

PROMPT = f"""你是景区导览系统的"眼睛"，请仔细观察这张照片，为讲解员收集素材。

输出JSON，字段如下：
- scene_summary: 详细的场景描述，3到5句话。只写画面里确实看到的东西：环境类型（室内/室外、自然/建筑）、主要景物及其外观特征、人物活动、光线氛围。看不清或不确定的不要写，不要猜地点名称
- landmark_guess: 如果能确定是以下景点之一：{'、'.join(TARGET_LANDMARKS)}，填景点名；否则留空字符串
- confidence: 这张照片对导览讲解的价值，0到1打分——清晰的室外景点/建筑0.7到0.9；画面模糊、室内场景、屏幕或纸张等内容0.1到0.3

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

        两个实测要点：
        - 该摄像头每次开流后自动曝光要 ~10 帧才收敛，首帧必过曝（均值 200+），
          所以丢弃前 9 帧取第 10 帧
        - 连续自动对焦默认是关的，每次开流恢复（设备重枚举后设置会丢）
        - 摄像头 USB 偶发掉线重枚举（和 WiFi 共用 USB2 集线器），失败重试 3 次
        """
        if self.image_source != "camera":
            frame = cv2.imread(self.image_source)
            if frame is None:
                raise FileNotFoundError(f"读不到图片: {self.image_source}")
            return self._save_frame(frame)

        last_err: Optional[Exception] = None
        for attempt in range(3):
            self._enable_autofocus()
            cap = cv2.VideoCapture(0)
            frame, ok = None, False
            for _ in range(10):          # 前 9 帧丢弃，给自动曝光收敛时间
                ok, f = cap.read()
                if not ok:
                    break
                frame = f
            cap.release()
            if ok and frame is not None:
                return self._save_frame(frame)
            last_err = RuntimeError("摄像头打开或读帧失败")
            log.warning("取景第 %d/3 次失败，0.6s 后重试", attempt + 1)
            time.sleep(0.6)
        raise last_err

    @staticmethod
    def _enable_autofocus() -> None:
        """恢复连续自动对焦（该摄像头默认关闭，且无自拍距离变化时影响小）。"""
        try:
            subprocess.run(
                ["v4l2-ctl", "-d", "/dev/video0",
                 "--set-ctrl=focus_automatic_continuous=1"],
                capture_output=True, timeout=5)
        except Exception:                              # noqa: BLE001
            pass

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
