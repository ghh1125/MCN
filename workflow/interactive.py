from __future__ import annotations

import asyncio
import json
from typing import Any

from services.config import get_settings
from workflow.nodes.intent import intent_node
from workflow.nodes.publish import publish_node
from workflow.nodes.retrieval import retrieval_node
from workflow.nodes.script import script_node
from workflow.nodes.topic import topic_node
from workflow.nodes.video import video_node
from workflow.state import WorkflowState, build_initial_state, utc_now_iso


def _merge_state(state: WorkflowState, updates: dict[str, Any]) -> None:
    state.update(updates)


def _prompt_binary(question: str) -> str:
    while True:
        answer = input(question).strip()
        if answer in {"0", "1"}:
            return answer
        print("请输入 1 或 0。")


def _prompt_required(question: str, default: str | None = None) -> str:
    while True:
        raw = input(question).strip()
        if raw:
            return raw
        if default is not None:
            return default
        print("这一项不能为空，请重新输入。")


def _print_json_block(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _print_topic_preview(state: WorkflowState) -> None:
    selected_topic = state.get("selected_topic", {})
    topic_report = state.get("topic_report", [])

    print("\n=== 当前推荐主题 ===")
    print(json.dumps(selected_topic, ensure_ascii=False, indent=2))

    if topic_report:
        print("\n=== 候选主题 Top 3 ===")
        for index, item in enumerate(topic_report[:3], start=1):
            print(f"{index}. {item.get('title', '')} | score={item.get('score', '')}")
            print(f"   卖点: {item.get('selling_point', '')}")
            print(f"   钩子: {item.get('hook', '')}")


def _print_script_preview(state: WorkflowState) -> None:
    script = state.get("script", {})
    preview = {
        "title": script.get("title", ""),
        "concept": script.get("concept", ""),
        "opening_hook": script.get("opening_hook", ""),
        "creative_script_text": script.get("creative_script_text", ""),
        "text_to_video_prompt": script.get("text_to_video_prompt", ""),
        "shot_outline": script.get("shot_outline", [])[:3],
        "production_notes": script.get("production_notes", []),
    }
    _print_json_block("当前剧本", preview)


async def run_interactive_workflow(
    raw_input: str,
    creator_id: str,
    platform: str,
) -> WorkflowState:
    settings = get_settings()
    state = build_initial_state(
        raw_input=raw_input,
        creator_id=creator_id,
        platform=platform,
    )

    print("\n[1/5] 正在解析创作意图...")
    _merge_state(state, await intent_node(state))
    _print_json_block("解析后的意图", state.get("intent", {}))

    topic_round = 1
    while True:
        print(f"\n[2/5] 第 {topic_round} 轮：正在联网检索并生成主题...")
        _merge_state(state, await retrieval_node(state))
        _merge_state(state, await topic_node(state))
        _print_topic_preview(state)

        answer = _prompt_binary("\n是否同意当前主题？输入 1 同意，输入 0 不同意：")
        if answer == "1":
            state["human_review_required"] = False
            state["topic_feedback"] = None
            break

        reason = _prompt_required("请说明不同意的原因：")
        if state.get("selected_topic"):
            state.setdefault("rejected_topics", []).append(dict(state["selected_topic"]))
        state.setdefault("topic_feedback_history", []).append(reason)
        state["topic_feedback"] = reason
        state["search_guidance"] = reason
        topic_round += 1

    script_round = 1
    while True:
        print(f"\n[3/5] 第 {script_round} 轮：正在生成创作剧本...")
        _merge_state(state, await script_node(state))
        _print_script_preview(state)

        answer = _prompt_binary("\n是否同意当前剧本？输入 1 同意，输入 0 不同意：")
        if answer == "1":
            state["script_feedback"] = None
            break

        reason = _prompt_required("请说明不同意的原因：")
        state.setdefault("script_feedback_history", []).append(reason)
        state["script_feedback"] = reason
        script_round += 1

    if settings.enable_video_pipeline:
        print("\n[4/5] 正在调用视频 API 并保存到本地...")
        _merge_state(state, await video_node(state))
        _print_json_block(
            "视频结果",
            {
                "video_status": state.get("video_status"),
                "video_url": state.get("video_url"),
                "local_video_path": state.get("local_video_path"),
                "error": state.get("error"),
            },
        )
    else:
        print("\n[4/5] 已跳过视频生成。")

    if settings.enable_publish_pipeline and state.get("video_status") == "done":
        print("\n[5/5] 正在发布视频...")
        _merge_state(state, await publish_node(state))
        _print_json_block("发布结果", state.get("publish_result", {}))
    else:
        print("\n[5/5] 已跳过发布。")

    state["updated_at"] = utc_now_iso()
    return state


def prompt_interactive_inputs(
    raw_input: str | None,
    creator_id: str | None,
    platform: str | None,
) -> tuple[str, str, str]:
    resolved_raw_input = raw_input or _prompt_required("请输入创作方向/需求：")
    resolved_creator_id = creator_id or _prompt_required("请输入 creator_id（默认 creator_001）：", default="creator_001")
    resolved_platform = platform or _prompt_required("请输入平台，如 xiaohongshu / douyin：")
    return resolved_raw_input, resolved_creator_id, resolved_platform


def run_interactive_cli(
    raw_input: str | None,
    creator_id: str | None,
    platform: str | None,
) -> None:
    resolved_raw_input, resolved_creator_id, resolved_platform = prompt_interactive_inputs(
        raw_input=raw_input,
        creator_id=creator_id,
        platform=platform,
    )
    result = asyncio.run(
        run_interactive_workflow(
            raw_input=resolved_raw_input,
            creator_id=resolved_creator_id,
            platform=resolved_platform,
        )
    )
    _print_json_block("最终结果", result)
