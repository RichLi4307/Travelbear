# -*- coding: utf-8 -*-
"""提示词拼装：讲解员人设 + 场景降级策略。

设计原则（实测教训）：
- 提示词要短：啰嗦的格式会拖慢速度、稀释约束
- 不设字数下限：硬凑字数是幻觉的头号来源，信息少就短讲
- 识图置信度极低的内容不进提示词：喂垃圾必出编造（"电子导览屏"事故；当时的
  主因是旧提示词，提示词重写后此门槛大幅放宽，只挡"完全没信息"的画面）
"""

from common.types import PositionInfo, SceneInfo

# 识图结果低于该置信度视为不可用。Qwen-VL 的置信度分布整体偏低（实测有效
# 画面 0.10~0.20），0.3 会把正常画面全卡掉；降到 0.05 只排除"模型完全没招"
# 的情况。编造治理主力是提示词铁律 + 低温短输出，不靠这道门槛。
SCENE_CONF_THRESHOLD = 0.05

# ==================== 讲解员规范（所有场景共用，保持精简） ====================
_GUIDE_RULES = """你是景区讲解员，给第一次到访的游客做现场讲解，讲稿会直接合成语音朗读。

【讲什么】按序取材，有什么讲什么：
1. 点题：景点名称与定位，一句话
2. 看点：现场信息里 2~3 个具体的可看之处
3. 内涵：来历或文化意义——知道才讲，拿不准用"相传"，不知道就不讲
4. 收尾：一句游览提示

【铁律】
- 只写现场信息里给出的名称与描述；没给出的设施、距离、数字、年代一律不写
- 现场信息模糊或很少时，就短讲泛讲，几十字也可以，绝不硬凑、不脑补
- 不向游客提问，全文陈述句
- 不用括号、动作描写、emoji
- 不提"图片、识别、摄像头、定位、AI"这类词
- 问候一句带过，不堆套话

【形式】口语短句；"各位游客朋友"开头；不超过180字；只输出正文。"""

# ==================== 场景模板（正常 + 降级） ====================

GUIDE_PROMPT_TEMPLATE = _GUIDE_RULES + """

【现场信息】
景点：{poi_name}
地址：{address}
眼前所见：{scene_desc}

请生成讲解。"""

GUIDE_PROMPT_POSITION_ONLY = _GUIDE_RULES + """

【现场信息】
景点：{poi_name}
地址：{address}

请仅根据位置信息生成讲解。"""

GUIDE_PROMPT_SCENE_ONLY = _GUIDE_RULES + """

【现场信息】
眼前所见：{scene_desc}

请仅根据眼前场景生成讲解；认不出具体景点就不点名、泛讲。"""

# 兜底：位置和场景都不可用（不走 LLM，直接念）
FALLBACK_SCRIPT_FULL = "欢迎来到我们的校园！我现在信号不太好，一时看不清眼前的具体位置，不过没关系，让我先带您四处走走，感受一下这里的氛围。"


def build_guide_prompt(position: PositionInfo, scene: SceneInfo) -> str:
    """原接口：把位置和场景拼成提示词（向后兼容）。"""
    return GUIDE_PROMPT_TEMPLATE.format(
        poi_name=position.poi_name,
        address=position.address,
        scene_desc=scene.scene_description
    ).strip()


def build_robust_prompt(position: PositionInfo, scene: SceneInfo):
    """
    降级策略核心：根据定位/识图的成功与否，返回 (prompt, local_fallback)。

    返回：
        (提示词字符串, None)    → 需要调用 LLM 生成
        (None, 固定话术字符串)  → 定位+识图都失败，直接用固定话术，不走 LLM
    """
    pos_ok = bool(position and position.success and position.poi_name)
    # 低置信画面宁可不用——垃圾描述是幻觉的头号来源
    scene_ok = bool(scene and scene.success and scene.scene_description
                    and scene.confidence >= SCENE_CONF_THRESHOLD)

    if pos_ok and scene_ok:
        return GUIDE_PROMPT_TEMPLATE.format(
            poi_name=position.poi_name,
            address=position.address,
            scene_desc=scene.scene_description
        ).strip(), None

    if pos_ok:
        # 只有位置可用（识图失败或不可信）
        return GUIDE_PROMPT_POSITION_ONLY.format(
            poi_name=position.poi_name,
            address=position.address,
        ).strip(), None

    if scene_ok:
        # 只有场景可用（定位失败）
        return GUIDE_PROMPT_SCENE_ONLY.format(
            scene_desc=scene.scene_description,
        ).strip(), None

    # 定位、识图都不可用
    return None, FALLBACK_SCRIPT_FULL


def build_local_script(position: PositionInfo, scene: SceneInfo) -> str:
    """无 LLM（Mock 模式）时的本地兜底文案：基于可用信息拼一句像样的话。"""
    pos_ok = bool(position and position.success and position.poi_name)
    scene_ok = bool(scene and scene.success and scene.scene_description)

    if pos_ok and scene_ok:
        return f"欢迎来到{position.poi_name}。您眼前的{scene.scene_description}，是这里最具代表性的景致。"
    if pos_ok:
        place = position.address or "我们校园里一个很有特色的地方"
        return f"欢迎来到{position.poi_name}，这里是{place}，让我为您介绍一下。"
    if scene_ok:
        return f"您眼前是{scene.scene_description}，这是校园里一处很有特色的景致。"
    return FALLBACK_SCRIPT_FULL
