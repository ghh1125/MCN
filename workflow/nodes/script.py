from __future__ import annotations

import json
from typing import Any

from services.llm import call_llm_json
from workflow.state import WorkflowState, utc_now_iso

SCRIPT_BRIEF_PROMPT = """
你是一名资深内容策略总监、短视频总编导、平台爆款选题顾问。请先不要直接写完整剧本，而是先根据用户原始需求产出一份“创作骨架 brief”。
只输出合法 JSON，不要输出解释，不要输出 Markdown。

用户原始创作需求：
{raw_input}

当前选题：
{topic}

达人风格：
{style}

目标平台：
{platform}

用户对上一版剧本的修改意见：
{script_feedback}

用户指定的视频时长：
{desired_duration} 秒

要求：
1. brief 必须严格服从用户原始需求，不能被选题标题带偏。
2. 如果有修改意见，brief 必须明确回应这些意见。
3. brief 要能指导后续稳定写出一版可执行剧本。
4. target_duration_seconds 必须严格等于用户指定秒数。

输出格式：
{{
  "brief_title": "这版创作骨架的工作标题",
  "creative_thesis": "这一条视频最核心的一句话创意判断",
  "target_duration_seconds": 8,
  "narrative_mode": "单人讲述/双人对话/旁白观察/群像切片等",
  "audience_promise": "观众为什么愿意看完",
  "core_conflict": "最核心的矛盾、反差或 tension",
  "creative_goals": {{
    "content_goal": "信息目标",
    "emotion_goal": "情绪目标",
    "conversion_goal": "转化目标"
  }},
  "roles": [
    {{
      "name": "角色名",
      "identity": "角色身份",
      "goal": "角色目标",
      "conflict": "角色冲突",
      "voice_style": "说话风格"
    }}
  ],
  "hook_strategy": "前3秒应该怎么抓人",
  "story_beats": [
    {{
      "beat": "开场吸引/冲突升级/解决反转/结尾收束",
      "purpose": "这一拍要完成什么",
      "emotion": "希望制造的情绪",
      "duration_hint": 2
    }}
  ],
  "visual_direction": "整体画面方向",
  "sound_direction": "整体声音/BGM方向",
  "must_keep": ["必须保留的主题约束1", "必须保留的主题约束2"],
  "must_avoid": ["必须避免的跑偏点1", "必须避免的跑偏点2"]
}}
"""


SCRIPT_DRAFT_PROMPT = """
你是一名专业短视频导演、商业广告编导、分镜设计师、AI 视频提示词设计师。请根据下面的创作骨架 brief，写出一版“导演可继续执行”的完整剧本包 JSON。
只输出合法 JSON，不要输出解释，不要输出 Markdown。

用户原始创作需求：
{raw_input}

创作骨架 brief：
{creative_brief}

要求：
1. 必须严格服从 raw_input 和 brief，不允许擅自改题。
2. target_duration_seconds 必须严格等于 brief 里的目标时长。
3. 分镜总时长要和目标时长大体匹配。
4. 输出原创可商用表达，不得出现可识别第三方 IP。
5. 剧本要尽量具体，镜头、表演、转场、字幕、声音设计都要可执行。

输出格式：
{{
  "title": "视频标题",
  "concept": "一句话概括视频创意",
  "target_duration_seconds": 8,
  "narrative_mode": "叙事方式",
  "core_conflict": "核心矛盾",
  "creative_goals": {{
    "content_goal": "信息目标",
    "emotion_goal": "情绪目标",
    "conversion_goal": "转化目标"
  }},
  "roles": [
    {{
      "name": "角色名",
      "identity": "身份",
      "goal": "目标",
      "conflict": "冲突",
      "voice_style": "说话风格"
    }}
  ],
  "opening_hook": "前3秒钩子",
  "creative_script_text": "完整创作剧本文本",
  "story_beats": [
    {{
      "beat": "阶段名",
      "purpose": "叙事目的",
      "emotion": "情绪"
    }}
  ],
  "shot_outline": [
    {{
      "scene": 1,
      "duration": 3,
      "beat": "这一镜头属于哪一拍",
      "objective": "这个镜头要完成什么任务",
      "character_focus": "聚焦角色",
      "emotion_curve": "情绪变化",
      "voiceover": "口播或对话",
      "visual_prompt": "画面描述",
      "onscreen_text": "字幕短句",
      "camera": "镜头语言",
      "transition": "转场方式",
      "sound_design": "音效/BGM/环境音",
      "performance_note": "表演注意点"
    }}
  ],
  "text_to_video_prompt": "适合直接喂给视频模型的一段式提示词",
  "visual_style": "视觉风格",
  "music_direction": "音乐方向",
  "cover_text": "封面文案",
  "cta": "结尾引导",
  "tags": ["标签1", "标签2"],
  "production_notes": ["制作要点1", "制作要点2"]
}}
"""


SCRIPT_REVIEW_PROMPT = """
你是一名资深剧本审稿人、内容总监、执行导演、质量控制编辑。请检查下面这版剧本是否存在明显问题，并在必要时给出修正版。
只输出合法 JSON，不要输出解释，不要输出 Markdown。

用户原始创作需求：
{raw_input}

用户对这一版的修改意见：
{script_feedback}

目标时长：
{desired_duration} 秒

创作骨架 brief：
{creative_brief}

待审剧本：
{script_payload}

重点检查：
1. 是否偏离用户原始需求
2. 是否真正回应了用户修改意见
3. target_duration_seconds 与分镜时长是否基本一致
4. 角色是否足够区分，不是同质化
5. creative_script_text / story_beats / shot_outline 是否相互一致
6. text_to_video_prompt 是否和正文与分镜一致

输出格式：
{{
  "is_acceptable": true,
  "issues": ["如果有问题，列出问题"],
  "revision_summary": "对这版剧本的总体评价",
  "revised_script": {{
    "title": "...",
    "concept": "...",
    "target_duration_seconds": 8,
    "narrative_mode": "...",
    "core_conflict": "...",
    "creative_goals": {{
      "content_goal": "...",
      "emotion_goal": "...",
      "conversion_goal": "..."
    }},
    "roles": [],
    "opening_hook": "...",
    "creative_script_text": "...",
    "story_beats": [],
    "shot_outline": [],
    "text_to_video_prompt": "...",
    "visual_style": "...",
    "music_direction": "...",
    "cover_text": "...",
    "cta": "...",
    "tags": [],
    "production_notes": []
  }}
}}
"""


IP_SAFETY_REWRITE_PROMPT = """
你是一名短视频版权合规编辑、品牌安全顾问、商业化内容审校专家。请把下面的剧本 JSON 改写为原创可商用版本。

要求：
1. 不能出现可识别第三方 IP 的角色名、作品名、组织名、招式名、标志性道具名、商标词。
2. 若存在风险内容，请改写为原创泛化表达，保持原有情绪、节奏、反转和可看性。
3. 保持 JSON 字段结构一致；缺失字段补齐为空字符串或空数组。
4. target_duration_seconds 必须是 2 到 15 的整数。
5. 只输出合法 JSON，不要输出解释或 Markdown。

输入 JSON：
{script_json}
"""


def _safe_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return ""


def _safe_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    results: list[str] = []
    for item in value:
        text = _safe_text(item).strip()
        if text:
            results.append(text)
    return results


def _safe_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


def _safe_roles(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    results: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            results.append(
                {
                    "name": _safe_text(item.get("name", "")),
                    "identity": _safe_text(item.get("identity", "")),
                    "goal": _safe_text(item.get("goal", "")),
                    "conflict": _safe_text(item.get("conflict", "")),
                    "voice_style": _safe_text(item.get("voice_style", "")),
                }
            )
    return results


def _safe_story_beats(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    results: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            results.append(
                {
                    "beat": _safe_text(item.get("beat", "")),
                    "purpose": _safe_text(item.get("purpose", "")),
                    "emotion": _safe_text(item.get("emotion", "")),
                }
            )
    return results


def _safe_shot_outline(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    results: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            results.append(item)
    return results


def _safe_creative_brief(value: Any, desired_duration: int) -> dict[str, Any]:
    payload = _safe_dict(value)
    goals = _safe_dict(payload.get("creative_goals", {}))
    story_beats = payload.get("story_beats", [])
    brief_beats: list[dict[str, Any]] = []
    if isinstance(story_beats, list):
        for item in story_beats:
            if isinstance(item, dict):
                brief_beats.append(
                    {
                        "beat": _safe_text(item.get("beat", "")),
                        "purpose": _safe_text(item.get("purpose", "")),
                        "emotion": _safe_text(item.get("emotion", "")),
                        "duration_hint": item.get("duration_hint", 0),
                    }
                )

    return {
        "brief_title": _safe_text(payload.get("brief_title", "")),
        "creative_thesis": _safe_text(payload.get("creative_thesis", "")),
        "target_duration_seconds": desired_duration,
        "narrative_mode": _safe_text(payload.get("narrative_mode", "")),
        "audience_promise": _safe_text(payload.get("audience_promise", "")),
        "core_conflict": _safe_text(payload.get("core_conflict", "")),
        "creative_goals": {
            "content_goal": _safe_text(goals.get("content_goal", "")),
            "emotion_goal": _safe_text(goals.get("emotion_goal", "")),
            "conversion_goal": _safe_text(goals.get("conversion_goal", "")),
        },
        "roles": _safe_roles(payload.get("roles", [])),
        "hook_strategy": _safe_text(payload.get("hook_strategy", "")),
        "story_beats": brief_beats,
        "visual_direction": _safe_text(payload.get("visual_direction", "")),
        "sound_direction": _safe_text(payload.get("sound_direction", "")),
        "must_keep": _safe_text_list(payload.get("must_keep", [])),
        "must_avoid": _safe_text_list(payload.get("must_avoid", [])),
    }


def _safe_script_payload(value: Any, desired_duration: int) -> dict[str, Any]:
    payload = _safe_dict(value)
    goals = _safe_dict(payload.get("creative_goals", {}))
    return {
        "title": _safe_text(payload.get("title", "")),
        "concept": _safe_text(payload.get("concept", "")),
        "target_duration_seconds": desired_duration,
        "narrative_mode": _safe_text(payload.get("narrative_mode", "")),
        "core_conflict": _safe_text(payload.get("core_conflict", "")),
        "creative_goals": {
            "content_goal": _safe_text(goals.get("content_goal", "")),
            "emotion_goal": _safe_text(goals.get("emotion_goal", "")),
            "conversion_goal": _safe_text(goals.get("conversion_goal", "")),
        },
        "roles": _safe_roles(payload.get("roles", [])),
        "opening_hook": _safe_text(payload.get("opening_hook", "")),
        "creative_script_text": _safe_text(payload.get("creative_script_text", "")),
        "story_beats": _safe_story_beats(payload.get("story_beats", [])),
        "shot_outline": _safe_shot_outline(payload.get("shot_outline", [])),
        "text_to_video_prompt": _safe_text(payload.get("text_to_video_prompt", "")),
        "visual_style": _safe_text(payload.get("visual_style", "")),
        "music_direction": _safe_text(payload.get("music_direction", "")),
        "cover_text": _safe_text(payload.get("cover_text", "")),
        "cta": _safe_text(payload.get("cta", "")),
        "tags": _safe_text_list(payload.get("tags", [])),
        "production_notes": _safe_text_list(payload.get("production_notes", [])),
    }


async def _rewrite_ip_safe_script(payload: dict[str, Any]) -> dict[str, Any]:
    prompt = IP_SAFETY_REWRITE_PROMPT.format(script_json=json.dumps(payload, ensure_ascii=False))
    try:
        rewritten = await call_llm_json(prompt, trace_name="script_ip_safety_rewrite")
    except Exception:
        return payload
    return _safe_dict(rewritten) or payload


async def _generate_creative_brief(state: WorkflowState, desired_duration: int) -> dict[str, Any]:
    prompt = SCRIPT_BRIEF_PROMPT.format(
        raw_input=json.dumps(state.get("raw_input", ""), ensure_ascii=False),
        topic=json.dumps(state.get("selected_topic", {}), ensure_ascii=False),
        style=state.get("intent", {}).get("style", ""),
        platform=state.get("platform", ""),
        script_feedback=json.dumps(state.get("script_feedback") or "", ensure_ascii=False),
        desired_duration=desired_duration,
    )
    raw_payload = await call_llm_json(prompt, trace_name="script_brief_generation")
    return _safe_creative_brief(raw_payload, desired_duration)


async def _generate_script_from_brief(
    state: WorkflowState,
    creative_brief: dict[str, Any],
    desired_duration: int,
) -> dict[str, Any]:
    prompt = SCRIPT_DRAFT_PROMPT.format(
        raw_input=json.dumps(state.get("raw_input", ""), ensure_ascii=False),
        creative_brief=json.dumps(creative_brief, ensure_ascii=False),
    )
    raw_payload = await call_llm_json(prompt, trace_name="script_generation")
    return _safe_script_payload(raw_payload, desired_duration)


async def _review_script_quality(
    state: WorkflowState,
    creative_brief: dict[str, Any],
    script_payload: dict[str, Any],
    desired_duration: int,
) -> dict[str, Any]:
    prompt = SCRIPT_REVIEW_PROMPT.format(
        raw_input=json.dumps(state.get("raw_input", ""), ensure_ascii=False),
        script_feedback=json.dumps(state.get("script_feedback") or "", ensure_ascii=False),
        desired_duration=desired_duration,
        creative_brief=json.dumps(creative_brief, ensure_ascii=False),
        script_payload=json.dumps(script_payload, ensure_ascii=False),
    )
    raw_review = await call_llm_json(prompt, trace_name="script_quality_review")
    review = _safe_dict(raw_review)
    revised_script = _safe_script_payload(review.get("revised_script", {}), desired_duration)
    return {
        "is_acceptable": bool(review.get("is_acceptable", True)),
        "issues": _safe_text_list(review.get("issues", [])),
        "revision_summary": _safe_text(review.get("revision_summary", "")),
        "revised_script": revised_script,
    }


async def script_node(state: WorkflowState) -> dict[str, Any]:
    desired_duration = state.get("desired_video_duration_seconds", 8)
    try:
        desired_duration = int(desired_duration)
    except (TypeError, ValueError):
        desired_duration = 8
    desired_duration = max(2, min(15, desired_duration))

    creative_brief = await _generate_creative_brief(state, desired_duration)
    drafted_script = await _generate_script_from_brief(state, creative_brief, desired_duration)
    quality_review = await _review_script_quality(state, creative_brief, drafted_script, desired_duration)

    final_script = drafted_script
    if quality_review["issues"] and quality_review["revised_script"].get("creative_script_text"):
        final_script = quality_review["revised_script"]

    final_script = _safe_script_payload(await _rewrite_ip_safe_script(final_script), desired_duration)
    final_script["creative_brief"] = creative_brief
    final_script["quality_review"] = {
        "is_acceptable": quality_review["is_acceptable"],
        "issues": quality_review["issues"],
        "revision_summary": quality_review["revision_summary"],
    }

    return {
        "script": final_script,
        "desired_video_duration_seconds": desired_duration,
        "updated_at": utc_now_iso(),
    }
