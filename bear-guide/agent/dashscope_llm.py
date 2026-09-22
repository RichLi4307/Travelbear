import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class LLMResponse:
    content: str


class DashScopeLLM:
    """阿里云 DashScope 大模型客户端（OpenAI 兼容接口，零第三方依赖）。

    环境变量：DASHSCOPE_API_KEY（见 .env.example）
    默认模型：qwen-plus（文案生成，tecs 技术栈敲定）

    两种用法：
        chat(prompt)            单轮：讲解文案生成，不污染对话历史
        ask(user_msg, system)   多轮：语音问答，自动维护对话历史（记忆）
    """

    BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

    # 问答时的导游人设（系统提示词）
    GUIDE_SYSTEM = (
        "你是一名亲切专业的景区导游，正在和游客对话。"
        "请用口语化、有温度的中文回答游客的提问，结合之前已经介绍过的景点内容，"
        "回答简洁自然，不要书面语。如果游客问的问题和当前景点无关，也要友好回应。"
    )

    def __init__(self, api_key: str = None, model: str = "qwen-plus"):
        self.api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        self.model = model
        self._history = []   # 多轮对话历史：[{"role": ..., "content": ...}, ...]

    # ------------------------------------------------------------------
    # 对话历史管理
    # ------------------------------------------------------------------
    def reset_history(self):
        """清空多轮对话历史（每次进入新的问答会话前调用）。"""
        self._history = []

    # ------------------------------------------------------------------
    # 底层同步调用（接收 messages 列表）
    # ------------------------------------------------------------------
    def _call_sync(self, messages) -> str:
        body = json.dumps({
            "model": self.model,
            "messages": messages,
        }, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            self.BASE_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()

    # ------------------------------------------------------------------
    # 单轮：讲解文案生成
    # ------------------------------------------------------------------
    async def chat(self, prompt: str) -> LLMResponse:
        if not self.api_key:
            return LLMResponse(content="（未配置 DASHSCOPE_API_KEY，使用占位文案）欢迎来到这座校园，眼前是标志性建筑，承载了历届学子的青春记忆。")
        try:
            messages = [{"role": "user", "content": prompt}]
            content = await asyncio.to_thread(self._call_sync, messages)
            return LLMResponse(content=content)
        except Exception as exc:
            # 错误记日志，但给游客的话要干净，不暴露技术细节
            print(f"[LLM降级] 讲解生成失败，使用本地话术：{exc}", file=sys.stderr)
            return LLMResponse(content="欢迎来到我们的校园，让我为您简单介绍一下眼前的景致。")

    # ------------------------------------------------------------------
    # 多轮：语音问答（维护历史）
    # ------------------------------------------------------------------
    async def ask(self, user_msg: str, system: str = None) -> LLMResponse:
        """问答模式：把用户这句话连同历史一起发给 LLM，并记住本轮问答。"""
        if not self.api_key:
            return LLMResponse(content="（未配置 DASHSCOPE_API_KEY）这个问题我暂时无法回答，抱歉。")

        # 组装完整消息：系统人设 + 历史 + 本轮提问
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        else:
            messages.append({"role": "system", "content": self.GUIDE_SYSTEM})
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_msg})

        try:
            content = await asyncio.to_thread(self._call_sync, messages)
        except Exception as exc:
            print(f"[LLM降级] 问答生成失败：{exc}", file=sys.stderr)
            content = "抱歉，我刚刚走神了，能再说一遍吗？"

        # 记住这一轮问答，供下一轮上下文使用
        self._history.append({"role": "user", "content": user_msg})
        self._history.append({"role": "assistant", "content": content})

        return LLMResponse(content=content)
