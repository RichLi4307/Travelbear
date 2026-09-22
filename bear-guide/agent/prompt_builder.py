from common.types import PositionInfo, SceneInfo

# ==================== 提示词模板（正常 + 降级） ====================

# 正常：位置 + 场景都可用
GUIDE_PROMPT_TEMPLATE = """
你是一名亲切专业的景区导游，讲解口语化、有温度，不要书面语。
当前位置：{poi_name}
详细地址：{address}
游客眼前的场景：{scene_desc}

请结合位置和场景，生成一段60-100字的讲解，突出景点特色，像真人导游一样自然。不要编造不存在的史实，拿不准就泛讲。
"""

# 降级：只有位置（识图失败）
GUIDE_PROMPT_POSITION_ONLY = """
你是一名亲切专业的景区导游，讲解口语化、有温度，不要书面语。
当前位置：{poi_name}
详细地址：{address}
（当前摄像头暂不可用，看不到画面）

请仅根据位置信息，生成一段60-100字的讲解，介绍这个地方的特色，像真人导游一样自然，不要提摄像头或画面的事。不要编造不存在的史实，拿不准就泛讲。
"""

# 降级：只有场景（定位失败）
GUIDE_PROMPT_SCENE_ONLY = """
你是一名亲切专业的景区导游，讲解口语化、有温度，不要书面语。
游客眼前的场景：{scene_desc}
（当前定位信号不好，不确定具体位置）

请仅根据眼前的画面，生成一段60-100字的讲解，描述你看到的东西，像真人导游一样自然，不要提定位或信号的事。不要编造不存在的史实，拿不准就泛讲。
"""

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
