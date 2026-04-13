from __future__ import annotations

from workflow.state import WorkflowState, utc_now_iso
from services.llm import call_llm_json

INTENT_PROMPT = """
你是 MCN 内容策略助手。请把用户的创作需求解析成结构化 JSON。
不要输出解释，不要输出 Markdown，只输出合法 JSON。

用户输入：{raw_input}
目标平台：{platform}

输出字段：
{{
  "category": "内容品类，如美妆/数码/美食",
  "audience": "目标人群描述",
  "style": "内容风格，如搞笑/干货/种草",
  "keywords": ["关键词1", "关键词2"],
  "content_type": "内容类型，如测评/教程/vlog/带货"
}}
"""


async def intent_node(state: WorkflowState) -> dict:
    prompt = INTENT_PROMPT.format(
        raw_input=state["raw_input"],
        platform=state["platform"],
    )
    payload = await call_llm_json(prompt, trace_name="intent_parse")
    return {
        "intent": {
            "category": payload.get("category", ""),
            "audience": payload.get("audience", ""),
            "style": payload.get("style", ""),
            "keywords": payload.get("keywords", []),
            "content_type": payload.get("content_type", ""),
        },
        "updated_at": utc_now_iso(),
    }
