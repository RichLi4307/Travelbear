# -*- coding: utf-8 -*-
"""提示词拼装：讲解员人设 + 场景降级策略。

讲解词会原样送 TTS 朗读，提示词的核心任务是约束 LLM：
讲什么（内容骨架）、怎么讲（口语）、绝对不能做什么（禁令清单）。
"""

from common.types import PositionInfo, SceneInfo

# ==================== 讲解员规范（所有场景共用） ====================
# 用户痛点对照：
#   - 「不知道讲什么」      → 【讲什么】给了四步内容骨架
#   - 「提需要回答的问」    → 禁令第一条：全文陈述句
#   - 「假装与景物互动」    → 禁令第二条：括号/动作/emoji 全禁
#   - 「瞎编+暴露技术」     → 禁令第三、四条
_GUIDE_RULES = """你是一位资深景区讲解员，正在给一批第一次到访的游客做现场讲解。
你的讲解会被合成语音直接朗读，所以必须严格遵守以下全部规则。

【讲什么——按四步取材，有什么讲什么】
1. 点题：这是什么景点或建筑，一句话交代名称与定位
2. 看点：结合眼前的场景，具体讲两到三个值得看的地方（外观、布局、标志性元素）
3. 内涵：讲它的来历、历史或文化意义——你知道才讲，不知道就不编，拿不准的用"相传""据说"或笼统带过
4. 收尾：一句实用的游览提示（合适的拍摄角度、周边可逛之处）

【绝对不能做的事】
- 不向游客提问：全文不要出现任何需要游客回应的问句（包括"您猜怎么着""大家知道吗"这类设问）；一律用陈述句
- 不做动作表情：禁止一切括号及括号里的内容，禁止"（点点头）"这类舞台指示，禁止使用 emoji
- 不暴露技术：绝不提"图片""识别""摄像头""定位""人工智能"等词，眼前的场景信息就当是你自己亲眼所见
- 不编造史实：没有把握的具体年代、数字、人名不要写，宁可泛讲
- 不堆砌套话：问候一句带过，不喊口号，不用"绝绝子""打卡圣地"这类网络腔

【形式要求】
- 全篇口语短句，像讲解员边走边说，有现场感
- 150 到 200 字，朗读约 40 到 55 秒
- 第一句以"各位游客朋友"开头并简短问候
- 只输出讲解正文：不要标题、不要分点、不要引号包裹、不要括号、不要任何说明文字"""

# ==================== 场景模板（正常 + 降级） ====================

# 正常：位置 + 场景都可用
GUIDE_PROMPT_TEMPLATE = _GUIDE_RULES + """

【当前现场信息】
景点：{poi_name}
详细地址：{address}
你眼前看到的：{scene_desc}

请根据以上现场信息，为游客生成一段讲解。"""

# 降级：只有位置（识图失败）
GUIDE_PROMPT_POSITION_ONLY = _GUIDE_RULES + """

【当前现场信息】
景点：{poi_name}
详细地址：{address}

请仅根据以上位置信息，为游客生成一段讲解（介绍这个地方的特色与看点）。"""

# 降级：只有场景（定位失败）
GUIDE_PROMPT_SCENE_ONLY = _GUIDE_RULES + """

【当前现场信息】
你眼前看到的：{scene_desc}

请仅根据眼前的场景生成一段讲解。若判断不出具体景点名称，不要硬猜，就根据画面内容泛讲；语气依然是"我带大家看眼前这一处"。"""

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
    scene_ok = bool(scene and scene.success and scene.scene_description)

    if pos_ok and scene_ok:
        return GUIDE_PROMPT_TEMPLATE.format(
            poi_name=position.poi_name,
            address=position.address,
            scene_desc=scene.scene_description
        ).strip(), None

    if pos_ok:
        # 只有位置可用（识图失败）
        return GUIDE_PROMPT_POSITION_ONLY.format(
            poi_name=position.poi_name,
            address=position.address,
        ).strip(), None

    if scene_ok:
        # 只有场景可用（定位失败）
        return GUIDE_PROMPT_SCENE_ONLY.format(
            scene_desc=scene.scene_description,
        ).strip(), None

    # 定位、识图都失败
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
