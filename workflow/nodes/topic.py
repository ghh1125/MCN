from __future__ import annotations

import json

from services.llm import call_llm_json
from workflow.state import WorkflowState, utc_now_iso

TOPIC_PROMPT = """
你是短视频选题策划。请基于以下上下文生成 8 个短视频选题，返回 JSON 数组。
不要输出解释，不要输出 Markdown，只输出合法 JSON 数组。

用户原始创作需求：
{raw_input}

创作意图：
{intent}

参考内容：
{retrieved_docs}

实时热点：
{trending_topics}

用户额外要求或上轮修改意见：
{search_guidance}

用户曾否决主题的原因：
{topic_feedback}

已经否决过的主题标题：
{rejected_titles}

请避开已经被否决的方向，并明确响应用户最新反馈。
所有候选主题都必须忠于“用户原始创作需求”。
如果检索结果、热点或你自己的惯性联想与原始需求冲突，必须以原始需求为准，不要擅自更换核心主题、目标人群、场景或内容方向。

每个对象必须包含：
- title: 选题标题
- score: 爆款潜力评分，1-10 的数字
- selling_point: 核心卖点
- emotion: 触发情绪，如好奇/共鸣/焦虑/惊喜
- hook: 前 3 秒开场钩子
"""


async def topic_node(state: WorkflowState) -> dict:
    prompt = TOPIC_PROMPT.format(
        raw_input=json.dumps(state.get("raw_input", ""), ensure_ascii=False),
        intent=json.dumps(state["intent"], ensure_ascii=False),
        retrieved_docs=json.dumps(
            [doc.get("content", "") for doc in state.get("retrieved_docs", [])[:5]],
            ensure_ascii=False,
        ),
        trending_topics=json.dumps(state.get("trending_topics", []), ensure_ascii=False),
        search_guidance=json.dumps(state.get("search_guidance") or "", ensure_ascii=False),
        topic_feedback=json.dumps(state.get("topic_feedback") or "", ensure_ascii=False),
        rejected_titles=json.dumps(
            [item.get("title", "") for item in state.get("rejected_topics", []) if item.get("title")],
            ensure_ascii=False,
        ),
    )
    raw_topics = await call_llm_json(prompt, trace_name="topic_generation")
    if not isinstance(raw_topics, list):
        raise ValueError("Topic node expected a JSON array from the LLM")

    topics: list[dict] = []
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        score = item.get("score", 0)
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            numeric_score = 0.0
        topics.append(
            {
                "title": item.get("title", ""),
                "score": numeric_score,
                "selling_point": item.get("selling_point", ""),
                "emotion": item.get("emotion", ""),
                "hook": item.get("hook", ""),
            }
        )

    if not topics:
        raise ValueError("Topic node did not produce any valid topics")

    topics.sort(key=lambda item: item["score"], reverse=True)
    top_topic = topics[0]

    return {
        "topic_report": topics,
        "selected_topic": top_topic,
        "human_review_required": False,
        "updated_at": utc_now_iso(),
    }
