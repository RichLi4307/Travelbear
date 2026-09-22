# -*- coding: utf-8 -*-
"""播报文本清洗：去掉 LLM 文案里 TTS 不该读出来的东西。

两类目标：
1. 括号内容——LLM 常生成舞台指示，如 （轻轻点点屏幕边框），TTS 会照字面读出来
2. TTS 无法稳定发声的字符——emoji、零宽字符、杂项符号（★ ❤ 等）

只影响送给 TTS 的文本；LLM 原始输出在日志里保持原样。
"""

import logging
import re
import unicodedata

log = logging.getLogger("agent.text_clean")

# 成对括号（全/半角圆、方、花、尖、全角黑括号）及其内容，不支持嵌套
_PAREN_RE = re.compile(r"[（(〔\[【\{][^（()）〕\]】\{\}【]*[)）〕\]】\}]")


def _is_unspeakable(ch: str) -> bool:
    """TTS 无法稳定发声的字符：控制符、emoji（非 BMP）、零宽、杂项符号。"""
    cp = ord(ch)
    if cp < 0x20 or cp == 0x7F:
        return True
    if cp > 0xFFFF:
        return True
    if ch in "​‌‍﻿":
        return True
    if unicodedata.category(ch) == "So":   # BMP 杂项符号：★ ❤ ♂ 等
        return True
    return False


def clean_for_speech(text: str) -> str:
    """清洗为适合 TTS 朗读的文本；清洗结果为空则回退原文（交由上层兜底）。"""
    if not text:
        return text
    no_paren = _PAREN_RE.sub("", text)
    cleaned = "".join(ch for ch in no_paren if not _is_unspeakable(ch))
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    if not cleaned:
        log.warning("清洗后文本为空，回退用原文")
        return text
    if cleaned != text:
        log.info("播报文本已清洗：%d → %d 字", len(text), len(cleaned))
    return cleaned
