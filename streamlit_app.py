from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Callable, Awaitable
from uuid import uuid4
from datetime import datetime

import httpx
import streamlit as st

from services.config import get_settings
from services.douyin_auth import (
    build_douyin_authorize_url,
    delete_douyin_binding,
    exchange_douyin_access_token,
    fetch_douyin_user_info,
    get_douyin_binding,
    save_douyin_binding,
)
from services.douyin_publish import refresh_douyin_publish_metrics
from services.llm_trace import capture_llm_trace
from services.runtime_credentials import use_runtime_api_keys
from services.llm import call_llm_json
from services.web_search import search_web
from workflow.nodes.intent import intent_node
from workflow.nodes.publish import publish_node
from workflow.nodes.retrieval import retrieval_node
from workflow.nodes.script import script_node
from workflow.nodes.topic import topic_node
from workflow.nodes.video import video_node
from workflow.state import WorkflowState, build_initial_state


st.set_page_config(page_title="MCN Workflow Studio", page_icon="🎬", layout="wide")


NodeFunc = Callable[[WorkflowState], Awaitable[dict[str, Any]]]

PLANNING_BASE_URL_PRESETS = {
    "DashScope Compatible": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "OpenAI": "https://api.openai.com/v1",
    "自定义": "__custom__",
}
PLANNING_MODEL_OPTIONS = [
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3.5-flash",
    "glm-5",
    "自定义",
]
VIDEO_API_URL_PRESETS = {
    "DashScope Video": "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis",
    "自定义": "__custom__",
}
SEARCH_PROVIDER_OPTIONS = [
    "tikhub",
    "自定义",
]
SEARCH_API_URL_PRESETS = {
    "TikHub": "https://api.tikhub.io",
    "自定义": "__custom__",
}
VIDEO_MODEL_OPTIONS = [
    "wan2.7-t2v",
    "vidu/viduq3-pro_img2video",
    "kling/kling-v3-video-generation",
    "自定义",
]
PLATFORM_LABELS = {
    "douyin": "抖音",
    "bilibili": "B站",
    "xiaohongshu": "小红书",
}
STAGE_LABELS = {
    "input": "输入需求",
    "topic_review": "审核主题",
    "script_review": "审核剧本",
    "video_ready": "生成视频",
    "video_done": "视频结果",
}


def _current_video_model() -> str:
    return _resolve_model_value(
        st.session_state.get("ui_video_model", "kling/kling-v3-video-generation"),
        st.session_state.get("ui_video_model_custom", ""),
    )


def _current_search_provider() -> str:
    return _resolve_model_value(
        st.session_state.get("ui_search_provider", "tikhub"),
        st.session_state.get("ui_search_provider_custom", ""),
    )


def _current_video_api_url() -> str:
    return _resolve_preset_value(
        st.session_state.get("ui_video_api_url_preset", "DashScope Video"),
        st.session_state.get("ui_video_api_url_custom", ""),
        VIDEO_API_URL_PRESETS,
    )


def _current_video_provider() -> str:
    api_url = _current_video_api_url().lower()
    if "dashscope.aliyuncs.com" in api_url:
        return "dashscope"
    return "generic"


def _current_video_status_api_url() -> str:
    provider = _current_video_provider()
    if provider == "dashscope":
        return "https://dashscope.aliyuncs.com/api/v1/tasks/{job_id}"
    return ""


def _platform_label(value: str) -> str:
    return PLATFORM_LABELS.get(value, value)


def _stage_label(value: str) -> str:
    return STAGE_LABELS.get(value, value)


def _select_label_for_value(options: dict[str, str], value: str) -> str:
    for label, option_value in options.items():
        if option_value == value:
            return label
    return "自定义"


def _resolve_preset_value(selected_label: str, custom_value: str, options: dict[str, str]) -> str:
    preset_value = options.get(selected_label, "")
    if preset_value == "__custom__":
        return custom_value.strip()
    return preset_value


def _resolve_model_value(selected_value: str, custom_value: str) -> str:
    if selected_value == "自定义":
        return custom_value.strip()
    return selected_value.strip()


def _apply_runtime_settings(*, debug_search: bool) -> None:
    os.environ["SEARCH_DEBUG_SAVE_RAW"] = "true" if debug_search else "false"
    os.environ["PLANNING_BASE_URL"] = _resolve_preset_value(
        st.session_state.get("ui_planning_base_url_preset", "DashScope Compatible"),
        st.session_state.get("ui_planning_base_url_custom", ""),
        PLANNING_BASE_URL_PRESETS,
    )
    os.environ["PLANNING_MODEL"] = _resolve_model_value(
        st.session_state.get("ui_planning_model", "qwen3.5-flash"),
        st.session_state.get("ui_planning_model_custom", ""),
    )
    os.environ["VIDEO_API_URL"] = _current_video_api_url()
    os.environ["VIDEO_STATUS_API_URL"] = _current_video_status_api_url()
    os.environ["VIDEO_API_PROVIDER"] = _current_video_provider()
    os.environ["VIDEO_AUDIO"] = "true" if st.session_state.get("ui_video_audio", True) else "false"
    os.environ["VIDEO_MODEL"] = _resolve_model_value(
        st.session_state.get("ui_video_model", "kling/kling-v3-video-generation"),
        st.session_state.get("ui_video_model_custom", ""),
    )
    os.environ["SEARCH_API_PROVIDER"] = _current_search_provider()
    os.environ["SEARCH_API_URL"] = _resolve_preset_value(
        st.session_state.get("ui_search_api_url_preset", "TikHub"),
        st.session_state.get("ui_search_api_url_custom", ""),
        SEARCH_API_URL_PRESETS,
    )
    get_settings.cache_clear()


def _runtime_api_keys() -> dict[str, str]:
    return {
        "planning_api_key": st.session_state.get("ui_planning_api_key", "").strip(),
        "search_api_key": st.session_state.get("ui_search_api_key", "").strip(),
        "video_api_key": st.session_state.get("ui_video_api_key", "").strip(),
    }


def _append_llm_events(events: list[dict[str, Any]]) -> None:
    st.session_state.setdefault("ui_llm_events", [])
    st.session_state["ui_llm_events"].extend(events)


def _append_chat_message(
    role: str,
    content: str,
    *,
    full_content: str | None = None,
    expandable_label: str = "展开查看完整内容",
) -> None:
    text = str(content).strip()
    if not text:
        return
    st.session_state.setdefault("ui_chat_history", [])
    item = {"role": role, "content": text}
    if full_content and str(full_content).strip() and str(full_content).strip() != text:
        item["full_content"] = str(full_content).strip()
        item["expandable_label"] = expandable_label
    st.session_state["ui_chat_history"].append(item)


def _append_user_input_log(label: str, text: str) -> None:
    content = str(text).strip()
    if not content:
        return
    st.session_state.setdefault("ui_user_input_log", [])
    st.session_state["ui_user_input_log"].append({"label": label, "text": content})


def _clear_llm_events() -> None:
    st.session_state["ui_llm_events"] = []


def _group_llm_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []

    for event in events:
        call_id = str(event.get("call_id") or f"event-{len(ordered)}")
        if call_id not in grouped:
            grouped[call_id] = {
                "call_id": call_id,
                "trace_name": event.get("trace_name", "llm_call"),
                "timestamp": event.get("timestamp", ""),
                "base_url": event.get("base_url", ""),
                "model": event.get("model", ""),
                "attempts": [],
                "responses": [],
                "parsed_json": None,
                "errors": [],
                "final_error": "",
            }
            ordered.append(grouped[call_id])

        group = grouped[call_id]
        kind = event.get("kind")
        if kind == "request":
            group["base_url"] = event.get("base_url", "")
            group["model"] = event.get("model", "")
        elif kind == "attempt":
            group["attempts"].append(event)
        elif kind == "response":
            group["responses"].append(event)
        elif kind == "parsed_json":
            group["parsed_json"] = event.get("parsed_json")
        elif kind == "attempt_error":
            group["errors"].append(event)
        elif kind == "final_error":
            group["final_error"] = event.get("error", "")

    return ordered


def _collect_user_inputs_for_trace(state: WorkflowState | None) -> list[dict[str, str]]:
    logged_inputs = [
        {"label": str(item.get("label", "")), "text": str(item.get("text", ""))}
        for item in st.session_state.get("ui_user_input_log", [])
        if str(item.get("text", "")).strip()
    ]
    if logged_inputs:
        return logged_inputs

    if not state:
        raw_input = st.session_state.get("ui_raw_input", "").strip()
        return [{"label": "初始创作需求", "text": raw_input}] if raw_input else []

    items: list[dict[str, str]] = []
    raw_input = state.get("raw_input", "").strip()
    if raw_input:
        items.append({"label": "初始创作需求", "text": raw_input})

    for index, feedback in enumerate(state.get("topic_feedback_history", []), start=1):
        text = str(feedback).strip()
        if text:
            items.append({"label": f"主题修改意见 {index}", "text": text})

    for index, feedback in enumerate(state.get("script_feedback_history", []), start=1):
        text = str(feedback).strip()
        if text:
            items.append({"label": f"剧本修改意见 {index}", "text": text})

    return items


def _related_user_inputs(item: dict[str, Any], user_inputs: list[dict[str, str]]) -> list[dict[str, str]]:
    trace_name = str(item.get("trace_name", ""))
    if trace_name == "intent_parse":
        return [entry for entry in user_inputs if entry["label"] == "初始创作需求"]
    if trace_name == "topic_generation":
        return [
            entry
            for entry in user_inputs
            if entry["label"] == "初始创作需求" or entry["label"].startswith("主题修改意见")
        ]
    if trace_name in {"script_generation", "script_ip_safety_rewrite"}:
        return [
            entry
            for entry in user_inputs
            if entry["label"] == "初始创作需求" or entry["label"].startswith("剧本修改意见")
        ]
    return user_inputs


def _render_model_reply(item: dict[str, Any]) -> None:
    parsed_json = item.get("parsed_json")
    if parsed_json is not None:
        st.markdown("**模型回复**")
        st.json(parsed_json)
        return

    responses = item.get("responses", [])
    if responses:
        latest = responses[-1]
        st.markdown("**模型回复**")
        st.code(latest.get("raw_text", ""), language="text")
        return

    st.info("模型回复暂未返回。")


def _trace_name_label(trace_name: str) -> str:
    labels = {
        "intent_parse": "意图解析",
        "topic_generation": "主题生成",
        "script_brief_generation": "创作骨架",
        "script_generation": "剧本生成",
        "script_quality_review": "剧本质检",
        "script_ip_safety_rewrite": "合规改写",
        "connectivity_check": "连通性测试",
    }
    return labels.get(trace_name, trace_name)


def _truncate_text(value: Any, limit: int = 120) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}..."


def _format_list_text(values: Any, *, limit: int = 3) -> str:
    if not isinstance(values, list):
        return ""
    items = [str(item).strip() for item in values if str(item).strip()]
    return "、".join(items[:limit])


def _assistant_message_from_group(item: dict[str, Any]) -> dict[str, str]:
    label = _trace_name_label(str(item.get("trace_name", "")))
    if item.get("final_error"):
        text = f"{label}失败：{item['final_error']}"
        return {"content": text}

    parsed = item.get("parsed_json")
    full_content: str | None = None
    if isinstance(parsed, dict):
        if item.get("trace_name") == "intent_parse":
            category = _truncate_text(parsed.get("category", ""), 36)
            audience = _truncate_text(parsed.get("audience", ""), 48)
            style = _truncate_text(parsed.get("style", ""), 24)
            content_type = _truncate_text(parsed.get("content_type", ""), 24)
            keywords = _format_list_text(parsed.get("keywords", []), limit=4)
            lines = [
                "我先把你的需求收成了一个更明确的创作方向。",
                f"这次会更偏向“{category}”方向，主要面向“{audience}”，整体风格是“{style}”，内容形态更接近“{content_type}”。",
            ]
            if keywords:
                lines.append(f"我会围绕这些关键词继续往下检索和创作：{keywords}。")
            return {"content": "\n\n".join(lines)}
        if item.get("trace_name") == "topic_generation":
            title = _truncate_text(parsed.get("title", ""), 48)
            selling_point = _truncate_text(parsed.get("selling_point", ""), 80)
            emotion = _truncate_text(parsed.get("emotion", ""), 24)
            hook = _truncate_text(parsed.get("hook", ""), 80)
            score = parsed.get("score", "")
            lines = [
                f"我重新整理了一轮选题，当前最推荐的是“{title}”。",
            ]
            if selling_point:
                lines.append(f"它的核心吸引点是：{selling_point}")
            if emotion or score:
                lines.append(f"这条更容易触发的感受是“{emotion}”，当前综合判断分数大概是 {score}。")
            if hook:
                lines.append(f"我还给它配了一个更抓人的开场方向：{hook}")
            return {"content": "\n\n".join(lines), "full_content": json.dumps(parsed, ensure_ascii=False, indent=2)}
        if item.get("trace_name") == "script_brief_generation":
            title = _truncate_text(parsed.get("brief_title", ""), 48)
            thesis = _truncate_text(parsed.get("creative_thesis", ""), 90)
            conflict = _truncate_text(parsed.get("core_conflict", ""), 90)
            goals = parsed.get("creative_goals", {})
            goal_summary = ""
            if isinstance(goals, dict):
                goal_summary = "；".join(
                    _truncate_text(goals.get(key, ""), 30)
                    for key in ["content_goal", "emotion_goal", "conversion_goal"]
                    if str(goals.get(key, "")).strip()
                )
            lines = [
                f"我先搭了一版创作骨架，主题主轴暂时定成“{title}”。",
                f"这一版最核心的表达判断是：{thesis}",
                f"我刻意保留的戏剧张力在于：{conflict}",
            ]
            if goal_summary:
                lines.append(f"创作目标会同时兼顾这几层：{goal_summary}")
            return {"content": "\n\n".join(lines), "full_content": json.dumps(parsed, ensure_ascii=False, indent=2)}
        if item.get("trace_name") == "script_generation":
            title = _truncate_text(parsed.get("title", ""), 48)
            opening_hook = _truncate_text(parsed.get("opening_hook", ""), 90)
            concept = _truncate_text(parsed.get("concept", ""), 90)
            visual_style = _truncate_text(parsed.get("visual_style", ""), 60)
            lines = [
                f"我已经把剧本扩成了一版更完整的执行稿，当前标题是“{title}”。",
            ]
            if opening_hook:
                lines.append(f"开场我会先用这一下把观众拉住：{opening_hook}")
            if concept:
                lines.append(f"这一版的核心创意表达是：{concept}")
            if visual_style:
                lines.append(f"画面上我会往这个方向收：{visual_style}")
            return {"content": "\n\n".join(lines), "full_content": json.dumps(parsed, ensure_ascii=False, indent=2)}
        if item.get("trace_name") == "script_quality_review":
            revision_summary = _truncate_text(parsed.get("revision_summary", ""), 120)
            issues = parsed.get("issues", [])
            issue_text = _format_list_text(issues, limit=3)
            acceptable = parsed.get("is_acceptable")
            lines = []
            if acceptable:
                lines.append("我已经做过一轮剧本质检，这版整体是可继续往下走的。")
            else:
                lines.append("我刚检查过这版剧本，里面还有一些需要修的地方，所以我做了进一步调整。")
            if revision_summary:
                lines.append(f"这轮我主要收了这些问题：{revision_summary}")
            if issue_text:
                lines.append(f"重点关注的是：{issue_text}")
            return {"content": "\n\n".join(lines), "full_content": json.dumps(parsed, ensure_ascii=False, indent=2)}
        if item.get("trace_name") == "script_ip_safety_rewrite":
            title = _truncate_text(parsed.get("title", ""), 48)
            concept = _truncate_text(parsed.get("concept", ""), 90)
            return {
                "content": (
                    f"我又顺手做了一轮表达收口，把可能过于敏感、撞 IP 或太像现成作品的地方往原创方向拉了一下。"
                    f"\n\n当前保留下来的核心还是“{title}”，整体创意主轴没有变：{concept}"
                ),
                "full_content": json.dumps(parsed, ensure_ascii=False, indent=2),
            }
        return {
            "content": f"{label}这一步已经完成，我把结构化结果同步到中间工作区了，你可以直接在那边继续看细节。",
            "full_content": json.dumps(parsed, ensure_ascii=False, indent=2),
        }
    if isinstance(parsed, list):
        if item.get("trace_name") == "topic_generation":
            topics = [entry for entry in parsed if isinstance(entry, dict)]
            if topics:
                primary = topics[0]
                alternatives = [str(entry.get("title", "")).strip() for entry in topics[1:3] if str(entry.get("title", "")).strip()]
                lines = [f"我先整理出一组选题，当前最推荐的是“{_truncate_text(primary.get('title', ''), 48)}”。"]
                selling_point = _truncate_text(primary.get("selling_point", ""), 80)
                hook = _truncate_text(primary.get("hook", ""), 80)
                if selling_point:
                    lines.append(f"它的核心吸引点是：{selling_point}")
                if hook:
                    lines.append(f"开场我会优先往这个方向抓人：{hook}")
                if alternatives:
                    lines.append(f"另外我还保留了这些备选方向：{'、'.join(alternatives)}。")
                return {
                    "content": "\n\n".join(lines),
                    "full_content": json.dumps(parsed, ensure_ascii=False, indent=2),
                }
        return {
            "content": f"{label}这一步已经完成，我先给你显示摘要，完整结构你可以展开看。",
            "full_content": json.dumps(parsed, ensure_ascii=False, indent=2),
        }

    responses = item.get("responses", [])
    if responses:
        raw_full_text = str(responses[-1].get("raw_text", "")).strip()
        raw_text = _truncate_text(raw_full_text, 220)
        return {
            "content": f"{label}完成了。这一轮模型的原始回复我已经收到了，核心内容是：{raw_text}",
            "full_content": raw_full_text,
        }
    return {}


def _append_assistant_messages_from_trace(events: list[dict[str, Any]]) -> None:
    for item in _group_llm_events(events):
        message = _assistant_message_from_group(item)
        if message.get("content"):
            _append_chat_message(
                "assistant",
                message["content"],
                full_content=message.get("full_content"),
            )


def _render_llm_trace_panel(
    events: list[dict[str, Any]],
    *,
    state: WorkflowState | None = None,
    placeholder: Any | None = None,
) -> None:
    target = placeholder.container() if placeholder is not None else st.container()
    grouped_events = _group_llm_events(events)
    user_inputs = _collect_user_inputs_for_trace(state)
    with target:
        st.subheader("LLM 交互日志")
        if not events:
            st.info("当前还没有 LLM 调用记录。")
            return

        if user_inputs:
            st.markdown("**用户输入**")
            for entry in user_inputs:
                st.write(f"{entry['label']}：{entry['text']}")
        else:
            st.info("当前还没有记录到用户输入。")

        for index, item in enumerate(grouped_events, start=1):
            status = "done"
            if item.get("final_error"):
                status = "error"
            elif not item.get("parsed_json") and not item.get("responses"):
                status = "running"

            with st.expander(
                f"{index}. {item.get('trace_name', 'llm_call')} | {item.get('model', '')} | {status}",
                expanded=index == len(grouped_events),
            ):
                st.json(
                    {
                        "timestamp": item.get("timestamp", ""),
                        "base_url": item.get("base_url", ""),
                        "model": item.get("model", ""),
                        "attempt_count": len(item.get("attempts", [])),
                    }
                )
                related_inputs = _related_user_inputs(item, user_inputs)
                if related_inputs:
                    st.markdown("**本轮参考的用户输入**")
                    for entry in related_inputs:
                        st.write(f"{entry['label']}：{entry['text']}")

                _render_model_reply(item)

                for error in item.get("errors", []):
                    st.error(f"Attempt {error.get('attempt', '?')}: {error.get('error', '')}")
                if item.get("final_error"):
                    st.error(item["final_error"])


def _run_node(
    node_func: NodeFunc,
    state: WorkflowState,
    *,
    trace_placeholder: Any | None = None,
) -> dict[str, Any]:
    async def _runner() -> dict[str, Any]:
        return await node_func(state)

    def _sink(events: list[dict[str, Any]]) -> None:
        historical = list(st.session_state.get("ui_llm_events", []))
        _render_llm_trace_panel(historical + events, state=state, placeholder=trace_placeholder)

    with capture_llm_trace(_sink if trace_placeholder is not None else None) as trace_events:
        with use_runtime_api_keys(**_runtime_api_keys()):
            updates = asyncio.run(_runner())
    _append_llm_events(list(trace_events))
    _append_assistant_messages_from_trace(list(trace_events))
    state.update(updates)
    return updates


def _ensure_session_defaults() -> None:
    settings = get_settings()
    defaults: dict[str, Any] = {
        "ui_stage": "input",
        "ui_workflow_state": None,
        "ui_error_message": "",
        "ui_topic_feedback_input": "",
        "ui_script_feedback_input": "",
        "ui_clear_topic_feedback_input": False,
        "ui_clear_script_feedback_input": False,
        "ui_planning_api_key": "",
        "ui_search_api_key": "",
        "ui_video_api_key": "",
        "ui_creator_id": "creator_001",
        "ui_publish_platform": "bilibili",
        "ui_search_platforms": ["bilibili"],
        "ui_raw_input": "",
        "ui_target_duration_seconds": max(2, min(15, int(settings.video_duration_seconds))),
        "ui_video_retry_duration_seconds": max(2, min(15, int(settings.video_duration_seconds))),
        "ui_video_source_image_url": "",
        "ui_video_audio": True,
        "ui_debug_search": False,
        "ui_connectivity_results": None,
        "ui_llm_events": [],
        "ui_script_editor_signature": "",
        "ui_script_editor_title": "",
        "ui_script_editor_hook": "",
        "ui_script_editor_text": "",
        "ui_script_editor_prompt": "",
        "ui_script_editor_cover_text": "",
        "ui_chat_history": [],
        "ui_user_input_log": [],
        "ui_douyin_client_key": "",
        "ui_douyin_client_secret": "",
        "ui_douyin_redirect_uri": "http://localhost:8501",
        "ui_douyin_auth_state": "",
        "ui_douyin_auth_message": "",
        "ui_monitor_auto_refresh": False,
        "ui_monitor_last_refreshed_at": "",
        "ui_search_provider": "tikhub",
        "ui_search_provider_custom": "",
        "ui_search_api_url_preset": "TikHub",
        "ui_search_api_url_custom": "",
        "ui_planning_base_url_preset": "DashScope Compatible",
        "ui_planning_base_url_custom": "",
        "ui_planning_model": "qwen3.5-flash",
        "ui_planning_model_custom": "",
        "ui_video_api_url_preset": "DashScope Video",
        "ui_video_api_url_custom": "",
        "ui_video_model": "kling/kling-v3-video-generation",
        "ui_video_model_custom": "",
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _set_error(message: str) -> None:
    st.session_state["ui_error_message"] = message


def _clear_error() -> None:
    st.session_state["ui_error_message"] = ""


def _reset_workflow() -> None:
    settings = get_settings()
    st.session_state["ui_stage"] = "input"
    st.session_state["ui_workflow_state"] = None
    st.session_state["ui_error_message"] = ""
    st.session_state["ui_topic_feedback_input"] = ""
    st.session_state["ui_script_feedback_input"] = ""
    st.session_state["ui_clear_topic_feedback_input"] = False
    st.session_state["ui_clear_script_feedback_input"] = False
    st.session_state["ui_connectivity_results"] = None
    st.session_state["ui_llm_events"] = []
    st.session_state["ui_script_editor_signature"] = ""
    st.session_state["ui_script_editor_title"] = ""
    st.session_state["ui_script_editor_hook"] = ""
    st.session_state["ui_script_editor_text"] = ""
    st.session_state["ui_script_editor_prompt"] = ""
    st.session_state["ui_script_editor_cover_text"] = ""
    st.session_state["ui_chat_history"] = []
    st.session_state["ui_user_input_log"] = []
    st.session_state["ui_douyin_auth_message"] = ""
    st.session_state["ui_monitor_auto_refresh"] = False
    st.session_state["ui_monitor_last_refreshed_at"] = ""
    st.session_state["ui_target_duration_seconds"] = max(2, min(15, int(settings.video_duration_seconds)))
    st.session_state["ui_video_retry_duration_seconds"] = max(2, min(15, int(settings.video_duration_seconds)))


def _prepare_feedback_input(field_key: str, clear_flag_key: str) -> None:
    if st.session_state.pop(clear_flag_key, False):
        st.session_state[field_key] = ""


def _script_editor_signature(script: dict[str, Any]) -> str:
    return "|".join(
        [
            str(script.get("title", "")),
            str(script.get("opening_hook", "")),
            str(script.get("creative_script_text", "")),
            str(script.get("text_to_video_prompt", "")),
            str(script.get("cover_text", "")),
        ]
    )


def _sync_script_editor(script: dict[str, Any]) -> None:
    signature = _script_editor_signature(script)
    if st.session_state.get("ui_script_editor_signature") == signature:
        return
    st.session_state["ui_script_editor_signature"] = signature
    st.session_state["ui_script_editor_title"] = str(script.get("title", ""))
    st.session_state["ui_script_editor_hook"] = str(script.get("opening_hook", ""))
    st.session_state["ui_script_editor_text"] = str(script.get("creative_script_text", ""))
    st.session_state["ui_script_editor_prompt"] = str(script.get("text_to_video_prompt", ""))
    st.session_state["ui_script_editor_cover_text"] = str(script.get("cover_text", ""))


def _apply_script_editor_to_state(state: WorkflowState) -> None:
    script = state.get("script", {})
    script["title"] = st.session_state.get("ui_script_editor_title", "").strip()
    script["opening_hook"] = st.session_state.get("ui_script_editor_hook", "").strip()
    script["creative_script_text"] = st.session_state.get("ui_script_editor_text", "").strip()
    script["text_to_video_prompt"] = st.session_state.get("ui_script_editor_prompt", "").strip()
    script["cover_text"] = st.session_state.get("ui_script_editor_cover_text", "").strip()
    state["script"] = script
    st.session_state["ui_script_editor_signature"] = _script_editor_signature(script)


def _require_keys(*required: str) -> bool:
    labels = {
        "planning": "策划大模型 API Key",
        "search": "搜索 API Key",
        "video": "视频 API Key",
    }
    missing = []
    values = {
        "planning": st.session_state.get("ui_planning_api_key", "").strip(),
        "search": st.session_state.get("ui_search_api_key", "").strip(),
        "video": st.session_state.get("ui_video_api_key", "").strip(),
    }
    for item in required:
        if not values.get(item):
            missing.append(labels[item])
    if missing:
        _set_error(f"请先在左侧填写：{'、'.join(missing)}")
        return False
    return True


def _render_stage_guide(stage: str) -> None:
    steps = [
        ("input", "1. 输入需求"),
        ("topic_review", "2. 主题审核"),
        ("script_review", "3. 剧本审核"),
        ("video_ready", "4. 视频生成"),
        ("video_done", "5. 视频结果"),
    ]
    current_index = 0
    for index, (key, _) in enumerate(steps):
        if key == stage:
            current_index = index
            break
    cols = st.columns(len(steps))
    for index, ((_, label), col) in enumerate(zip(steps, cols)):
        prefix = "●" if index <= current_index else "○"
        col.markdown(f"**{prefix} {label}**")


def _reset_video_outputs(state: WorkflowState) -> None:
    state["video_job_id"] = None
    state["video_url"] = None
    state["local_video_path"] = None
    state["video_status"] = "pending" if get_settings().enable_video_pipeline else "skipped"
    state["video_raw"] = {}
    state["error"] = None


def _rollback_to_stage(state: WorkflowState, target_stage: str) -> None:
    _clear_error()
    if target_stage == "topic_review":
        state["script"] = {}
        state["script_feedback"] = None
        _reset_video_outputs(state)
    elif target_stage in {"script_review", "video_ready"}:
        _reset_video_outputs(state)
    st.session_state["ui_workflow_state"] = state
    st.session_state["ui_stage"] = target_stage


def _available_rollback_options(stage: str) -> list[tuple[str, str]]:
    if stage == "script_review":
        return [("topic_review", "退回到主题审核"), ("script_review", "留在剧本审核")]
    if stage in {"video_ready", "video_done"}:
        return [
            ("topic_review", "退回到主题审核"),
            ("script_review", "退回到剧本审核"),
            ("video_ready", "退回到视频生成前"),
        ]
    return []


def _render_rollback_controls(state: WorkflowState, stage: str) -> None:
    options = _available_rollback_options(stage)
    if not options:
        return

    st.markdown("**快速回退**")
    labels = [label for _, label in options]
    value_to_label = {value: label for value, label in options}
    label_to_value = {label: value for value, label in options}
    current_target = st.session_state.get("ui_rollback_target", options[0][0])
    current_label = value_to_label.get(current_target, labels[0])
    selected_label = st.selectbox(
        "如果你想返回上一步或更早的步骤，可以直接在这里选择",
        options=labels,
        index=labels.index(current_label) if current_label in labels else 0,
        key="ui_rollback_target_label",
    )
    target_stage = label_to_value[selected_label]
    st.session_state["ui_rollback_target"] = target_stage

    if st.button("回到这个步骤", use_container_width=False):
        _rollback_to_stage(state, target_stage)
        st.rerun()


def _render_workflow_overview() -> None:
    with st.expander("工作流总览", expanded=False):
        st.info(
            "输入创作需求 → AI 解析意图与检索参考内容 → 生成候选主题 → 你确认主题 → 生成剧本 → 你确认剧本 → 生成视频并保存到本地"
        )
        workflow_cols = st.columns(4)
        workflow_cols[0].markdown(
            """
**1. 输入需求**

- 输入创作方向
- 选择搜索平台
- 选择目标发布平台
"""
        )
        workflow_cols[1].markdown(
            """
**2. 检索与出题**

- 解析你的需求
- 到勾选平台搜索参考内容
- 生成候选主题
"""
        )
        workflow_cols[2].markdown(
            """
**3. 剧本打磨**

- 确定目标秒数
- 生成细化剧本
- 不满意可继续重写
"""
        )
        workflow_cols[3].markdown(
            """
**4. 视频生成**

- 剧本确认后生成视频
- 秒数不合适会回到剧本
- 成功后保存到本地
"""
        )
        st.markdown(
            """
```text
输入需求
  ↓
检索参考内容与生成主题
  ↓
主题确认
  ├─ 不满意 → 输入修改意见 → 回到“检索参考内容与生成主题”
  ↓
生成剧本
  ↓
剧本确认
  ├─ 不满意 → 输入修改意见 / 调整秒数 → 回到“生成剧本”
  ↓
生成视频
  ├─ 秒数相关报错 → 调整秒数 → 回到“生成剧本”
  ↓
本地保存与查看结果
```
"""
        )


def _chat_placeholder_for_stage(stage: str) -> str:
    if stage == "input":
        return "在这里输入你的创作需求，比如“做一条 B 站春日出行 vlog 选题，偏真实分享”"
    if stage == "topic_review":
        return "如果主题不满意，直接在这里说你想怎么改；满意的话在中间点“同意当前主题”"
    if stage == "script_review":
        return "如果剧本要重写，就在这里说修改意见；如果只是微调，也可以先在中间直接手改剧本"
    return "当前阶段暂时不需要文字输入。如果想继续调整，可以先用中间的快速回退。"


def _render_chat_panel(*, trace_placeholder: Any | None = None) -> None:
    stage = st.session_state.get("ui_stage", "input")
    state = st.session_state.get("ui_workflow_state")

    st.subheader("对话协作")
    st.caption("右侧是持续对话区。你的输入会自动进入当前步骤，模型回复也会在这里持续展示。")

    history = st.session_state.get("ui_chat_history", [])
    if not history and stage == "input":
        st.info("先在下面输入你的创作需求，我们就从第一轮选题开始。")

    chat_container = st.container(height=680, border=True)
    with chat_container:
        for item in history:
            with st.chat_message(item.get("role", "assistant")):
                st.markdown(str(item.get("content", "")))
                full_content = str(item.get("full_content", "")).strip()
                if full_content:
                    with st.expander(str(item.get("expandable_label", "展开查看完整内容")), expanded=False):
                        st.code(full_content, language="text")

    if state is not None and stage != "input":
        with st.container(border=True):
            st.markdown(f"**当前操作：{_stage_label(stage)}**")

            if stage == "topic_review":
                current_duration = int(
                    state.get(
                        "desired_video_duration_seconds",
                        st.session_state.get("ui_target_duration_seconds", 5),
                    )
                )
                selected_duration = st.number_input(
                    "目标视频秒数",
                    min_value=2,
                    max_value=15,
                    value=max(2, min(15, current_duration)),
                    step=1,
                    key="ui_target_duration_seconds",
                    help="先定秒数，再进入剧本创作。",
                )
                state["desired_video_duration_seconds"] = int(selected_duration)
                st.caption("如果主题不满意，直接在下面对话框输入修改意见；如果满意，就点继续。")
                if st.button("同意当前主题", use_container_width=True, key="ui_chat_approve_topic"):
                    _clear_error()
                    state["topic_feedback"] = None
                    state["desired_video_duration_seconds"] = int(
                        st.session_state.get("ui_target_duration_seconds", selected_duration)
                    )
                    with st.spinner("正在生成剧本..."):
                        _run_node(script_node, state, trace_placeholder=trace_placeholder)
                    st.session_state["ui_stage"] = "script_review"
                    st.rerun()

            elif stage == "script_review":
                desired_duration = int(
                    state.get(
                        "desired_video_duration_seconds",
                        state.get("script", {}).get("target_duration_seconds", 5),
                    )
                )
                revised_duration = st.number_input(
                    "剧本目标秒数",
                    min_value=2,
                    max_value=15,
                    value=max(2, min(15, desired_duration)),
                    step=1,
                    key="ui_target_duration_seconds",
                    help="如果想改时长，可以先改这里，再在下方对话框里让系统重写剧本。",
                )
                if _current_video_model() == "vidu/viduq3-pro_img2video":
                    st.text_input(
                        "首帧图片 URL",
                        key="ui_video_source_image_url",
                        placeholder="请输入一张公开可访问的图片 URL，供 img2video 使用。",
                    )
                    state["video_source_image_url"] = st.session_state.get("ui_video_source_image_url", "").strip()
                st.caption("如果剧本需要 AI 重写，直接在下面对话框输入修改意见；如果满意，就点继续。")
                if st.button("同意当前剧本", use_container_width=True, key="ui_chat_approve_script"):
                    _clear_error()
                    if _current_video_model() == "vidu/viduq3-pro_img2video" and not st.session_state.get(
                        "ui_video_source_image_url", ""
                    ).strip():
                        _set_error("当前选的是 img2video 模型，请先填写首帧图片 URL。")
                        st.rerun()
                    _apply_script_editor_to_state(state)
                    state["script_feedback"] = None
                    state["desired_video_duration_seconds"] = int(revised_duration)
                    state["video_source_image_url"] = st.session_state.get("ui_video_source_image_url", "").strip()
                    st.session_state["ui_stage"] = "video_ready"
                    st.rerun()

            elif stage == "video_ready":
                st.caption("剧本已确认，现在可以直接生成视频。")
                if _current_video_model() == "vidu/viduq3-pro_img2video":
                    st.info("当前视频模型是 img2video，会使用你填写的首帧图片 URL 作为输入。")
                if st.button("生成视频并保存到本地", type="primary", use_container_width=True, key="ui_chat_generate_video"):
                    settings = get_settings()
                    if settings.enable_video_pipeline and not _require_keys("video"):
                        st.rerun()
                    if _current_video_model() == "vidu/viduq3-pro_img2video" and not state.get("video_source_image_url"):
                        _set_error("当前视频模型需要图片输入，请先回到剧本审核填写首帧图片 URL。")
                        st.rerun()
                    _clear_error()
                    with st.spinner("正在调用视频 API 并等待结果..."):
                        _run_node(video_node, state, trace_placeholder=trace_placeholder)
                    st.session_state["ui_stage"] = "video_done"
                    st.rerun()

    prompt = _chat_placeholder_for_stage(stage)
    disabled = stage not in {"input", "topic_review", "script_review"}
    user_message = st.chat_input(prompt, disabled=disabled)
    if not user_message:
        return

    user_message = user_message.strip()
    if not user_message:
        return

    _append_chat_message("user", user_message)
    _clear_error()
    with chat_container:
        with st.chat_message("user"):
            st.markdown(user_message)
        with st.chat_message("assistant"):
            st.markdown("正在处理这一轮输入，请稍等...")

    try:
        if stage == "input" or state is None:
            st.session_state["ui_raw_input"] = user_message
            _append_user_input_log("初始创作需求", user_message)
            _start_workflow()
            st.rerun()

        if stage == "topic_review":
            state = st.session_state["ui_workflow_state"]
            feedback_index = len(state.get("topic_feedback_history", [])) + 1
            if state.get("selected_topic"):
                state.setdefault("rejected_topics", []).append(dict(state["selected_topic"]))
            state.setdefault("topic_feedback_history", []).append(user_message)
            state["topic_feedback"] = user_message
            state["search_guidance"] = user_message
            _append_user_input_log(f"主题修改意见 {feedback_index}", user_message)
            with st.spinner("正在根据你的反馈重新检索并生成主题..."):
                _run_node(retrieval_node, state, trace_placeholder=trace_placeholder)
                _run_node(topic_node, state, trace_placeholder=trace_placeholder)
            st.session_state["ui_stage"] = "topic_review"
            st.rerun()

        if stage == "script_review":
            state = st.session_state["ui_workflow_state"]
            feedback_index = len(state.get("script_feedback_history", [])) + 1
            state["desired_video_duration_seconds"] = int(
                st.session_state.get("ui_target_duration_seconds", state.get("desired_video_duration_seconds", 5))
            )
            state["video_source_image_url"] = st.session_state.get("ui_video_source_image_url", "").strip()
            state.setdefault("script_feedback_history", []).append(user_message)
            state["script_feedback"] = user_message
            _append_user_input_log(f"剧本修改意见 {feedback_index}", user_message)
            with st.spinner("正在根据你的反馈重写剧本..."):
                _run_node(script_node, state, trace_placeholder=trace_placeholder)
            st.session_state["ui_stage"] = "script_review"
            st.rerun()
    except Exception as exc:  # pragma: no cover - runtime integration path
        _set_error(str(exc))
        _append_chat_message("assistant", f"这一步执行失败了：{exc}")
        st.rerun()


def _render_stat_card(title: str, value: Any, tone: str = "blue") -> None:
    palette = {
        "blue": ("#eff6ff", "#1d4ed8"),
        "green": ("#ecfdf5", "#047857"),
        "orange": ("#fff7ed", "#c2410c"),
        "pink": ("#fdf2f8", "#be185d"),
        "gray": ("#f8fafc", "#475569"),
    }
    background, accent = palette.get(tone, palette["blue"])
    st.markdown(
        f"""
<div style="
    border: 1px solid #e5e7eb;
    border-radius: 14px;
    padding: 16px 14px;
    background: {background};
    min-height: 96px;
">
  <div style="font-size: 13px; color: #64748b; margin-bottom: 10px;">{title}</div>
  <div style="font-size: 28px; font-weight: 700; color: {accent}; line-height: 1.1;">{value}</div>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_status_badge(label: str, ok: bool) -> None:
    background = "#ecfdf5" if ok else "#fff7ed"
    color = "#047857" if ok else "#c2410c"
    st.markdown(
        f"""
<div style="
    display: inline-block;
    padding: 6px 12px;
    border-radius: 999px;
    background: {background};
    color: {color};
    font-weight: 600;
    font-size: 13px;
">
  {label}
</div>
""",
        unsafe_allow_html=True,
    )


def _render_sidebar() -> None:
    settings = get_settings()
    with st.sidebar:
        st.header("工作台配置")
        st.caption("这些配置只作用于当前网页会话，不会写回 `.env`。")

        with st.expander("1. 策划大模型", expanded=True):
            st.text_input("策划大模型 API Key", key="ui_planning_api_key", type="password")
            st.selectbox(
                "规划模型 URL 预设",
                options=list(PLANNING_BASE_URL_PRESETS.keys()),
                key="ui_planning_base_url_preset",
            )
            if st.session_state.get("ui_planning_base_url_preset") == "自定义":
                st.text_input("自定义规划模型 URL", key="ui_planning_base_url_custom")
            else:
                st.caption(
                    _resolve_preset_value(
                        st.session_state.get("ui_planning_base_url_preset", "DashScope Compatible"),
                        st.session_state.get("ui_planning_base_url_custom", ""),
                        PLANNING_BASE_URL_PRESETS,
                    )
                )
            st.selectbox("规划模型", options=PLANNING_MODEL_OPTIONS, key="ui_planning_model")
            if st.session_state.get("ui_planning_model") == "自定义":
                st.text_input("自定义规划模型名", key="ui_planning_model_custom")

        with st.expander("2. 搜索", expanded=False):
            st.text_input("搜索 API Key", key="ui_search_api_key", type="password")
            st.selectbox("搜索 Provider", options=SEARCH_PROVIDER_OPTIONS, key="ui_search_provider")
            if st.session_state.get("ui_search_provider") == "自定义":
                st.text_input("自定义搜索 Provider", key="ui_search_provider_custom")
            st.selectbox(
                "搜索 API URL 预设",
                options=list(SEARCH_API_URL_PRESETS.keys()),
                key="ui_search_api_url_preset",
            )
            if st.session_state.get("ui_search_api_url_preset") == "自定义":
                st.text_input("自定义搜索 API URL", key="ui_search_api_url_custom")
            else:
                st.caption(
                    _resolve_preset_value(
                        st.session_state.get("ui_search_api_url_preset", "TikHub"),
                        st.session_state.get("ui_search_api_url_custom", ""),
                        SEARCH_API_URL_PRESETS,
                    )
                )

        with st.expander("3. 视频生成", expanded=False):
            st.text_input("视频 API Key", key="ui_video_api_key", type="password")
            st.selectbox(
                "视频提交 URL 预设",
                options=list(VIDEO_API_URL_PRESETS.keys()),
                key="ui_video_api_url_preset",
            )
            if st.session_state.get("ui_video_api_url_preset") == "自定义":
                st.text_input("自定义视频提交 URL", key="ui_video_api_url_custom")
            else:
                st.caption(
                    _resolve_preset_value(
                        st.session_state.get("ui_video_api_url_preset", "DashScope Video"),
                        st.session_state.get("ui_video_api_url_custom", ""),
                        VIDEO_API_URL_PRESETS,
                    )
                )
            status_api_url = _current_video_status_api_url()
            if status_api_url:
                st.caption(f"状态查询接口会自动使用：{status_api_url}")
            else:
                st.caption("当前未识别到可自动推导的状态查询接口，只有在服务本身同步返回结果时才能直接使用。")
            st.selectbox("视频模型", options=VIDEO_MODEL_OPTIONS, key="ui_video_model")
            if st.session_state.get("ui_video_model") == "自定义":
                st.text_input("自定义视频模型名", key="ui_video_model_custom")
            st.checkbox("生成音频", key="ui_video_audio", help="对支持音频的模型会传入 audio=true。")

        st.checkbox("保存 TikHub 原始搜索响应", key="ui_debug_search")

        if st.button("重置当前流程", use_container_width=True):
            _reset_workflow()
            st.rerun()

        if st.button("测试 API 连通性", use_container_width=True):
            _clear_error()
            _clear_llm_events()
            with st.spinner("正在测试大模型、搜索和视频接口连通性..."):
                st.session_state["ui_connectivity_results"] = _run_connectivity_checks()
            st.rerun()

        st.divider()
        st.caption("当前会话生效配置")
        st.write(
            {
                "planning_base_url": _resolve_preset_value(
                    st.session_state.get("ui_planning_base_url_preset", "DashScope Compatible"),
                    st.session_state.get("ui_planning_base_url_custom", ""),
                    PLANNING_BASE_URL_PRESETS,
                ),
                "planning_model": _resolve_model_value(
                    st.session_state.get("ui_planning_model", "qwen3.5-flash"),
                    st.session_state.get("ui_planning_model_custom", ""),
                ),
                "search_provider": _current_search_provider(),
                "search_api_url": _resolve_preset_value(
                    st.session_state.get("ui_search_api_url_preset", "TikHub"),
                    st.session_state.get("ui_search_api_url_custom", ""),
                    SEARCH_API_URL_PRESETS,
                ),
                "search_xiaohongshu_content_mode": settings.search_xiaohongshu_content_mode,
                "video_provider": _current_video_provider(),
                "video_api_url": _current_video_api_url(),
                "video_status_api_url": _current_video_status_api_url(),
                "video_model": _current_video_model(),
                "video_audio": st.session_state.get("ui_video_audio", True),
            }
        )

        results = st.session_state.get("ui_connectivity_results")
        if results:
            st.divider()
            st.caption("连通性测试结果")
            st.json(results)


def _render_intent_and_search(state: WorkflowState) -> None:
    st.subheader("步骤 1：意图解析")
    intent = state.get("intent", {})
    intent_cols = st.columns(4)
    intent_cols[0].metric("品类", intent.get("category", ""))
    intent_cols[1].metric("人群", intent.get("audience", ""))
    intent_cols[2].metric("风格", intent.get("style", ""))
    intent_cols[3].metric("内容类型", intent.get("content_type", ""))
    keywords = intent.get("keywords", [])
    if keywords:
        st.caption(f"关键词：{' / '.join(str(item) for item in keywords)}")
    with st.expander("查看完整意图解析", expanded=False):
        st.json(intent)

    st.subheader("步骤 2：检索结果")
    docs = state.get("retrieved_docs", [])
    if state.get("platform") == "xiaohongshu" and any(
        item.get("source") == "tikhub:xiaohongshu:hot-fallback" for item in docs
    ):
        st.info("当前小红书内容检索使用的是“热榜衍生检索”模式：先抓平台热榜，再按你的需求做相关性筛选。")
    elif state.get("platform") == "douyin":
        st.info("当前抖音会优先走 TikHub 的站内视频搜索和热榜接口，是这版工作流里最稳的检索路径。")
    if state.get("search_warnings"):
        for warning in state.get("search_warnings", []):
            st.warning(warning)

    left, right = st.columns([2, 1])
    with left:
        if docs:
            for index, item in enumerate(docs, start=1):
                with st.expander(f"参考内容 {index}: {item.get('title', '未命名结果')}"):
                    st.write(item.get("content", ""))
                    st.caption(item.get("url", ""))
                    st.caption(
                        f"来源：{_platform_label(str(item.get('source', '')).split(':')[1]) if ':' in str(item.get('source', '')) else item.get('source', '')} | 热度分：{item.get('score', '')}"
                    )
        else:
            st.info("当前还没有检索到参考内容。")

    with right:
        st.markdown("**热点列表**")
        trending_topics = state.get("trending_topics", [])
        if trending_topics:
            for index, item in enumerate(trending_topics, start=1):
                st.write(f"{index}. {item}")
        else:
            st.info("当前还没有热点结果。")


def _render_topic_review(state: WorkflowState, *, allow_actions: bool) -> None:
    st.subheader("步骤 3：主题审核")
    selected_topic = state.get("selected_topic", {})
    st.markdown("**当前推荐主题**")
    summary_cols = st.columns(3)
    summary_cols[0].metric("推荐标题", selected_topic.get("title", ""))
    summary_cols[1].metric("潜力评分", str(selected_topic.get("score", "")))
    summary_cols[2].metric("情绪触发", selected_topic.get("emotion", ""))
    if selected_topic.get("selling_point"):
        st.write(f"核心卖点：{selected_topic.get('selling_point', '')}")
    if selected_topic.get("hook"):
        st.write(f"前 3 秒钩子：{selected_topic.get('hook', '')}")
    with st.expander("查看推荐主题详情", expanded=False):
        st.json(selected_topic)

    topic_report = state.get("topic_report", [])
    if topic_report:
        st.markdown("**候选主题**")
        for index, item in enumerate(topic_report[:8], start=1):
            with st.expander(f"{index}. {item.get('title', '')} | score={item.get('score', '')}"):
                st.write(f"卖点：{item.get('selling_point', '')}")
                st.write(f"情绪：{item.get('emotion', '')}")
                st.write(f"钩子：{item.get('hook', '')}")

    if allow_actions:
        st.info("这一轮的操作按钮已经放到右侧对话区了。这里专注看候选主题和推荐结果就好。")


def _render_script_review(state: WorkflowState, *, allow_actions: bool) -> None:
    st.subheader("步骤 4：剧本审核")
    script = state.get("script", {})
    desired_duration = int(state.get("desired_video_duration_seconds", script.get("target_duration_seconds", 5)))
    _sync_script_editor(script)

    st.markdown("**剧本概览**")
    st.json(
        {
            "title": script.get("title", ""),
            "concept": script.get("concept", ""),
            "desired_video_duration_seconds": desired_duration,
            "narrative_mode": script.get("narrative_mode", ""),
            "core_conflict": script.get("core_conflict", ""),
            "creative_goals": script.get("creative_goals", {}),
            "roles": script.get("roles", []),
            "opening_hook": script.get("opening_hook", ""),
            "text_to_video_prompt": script.get("text_to_video_prompt", ""),
            "visual_style": script.get("visual_style", ""),
            "music_direction": script.get("music_direction", ""),
            "cover_text": script.get("cover_text", ""),
            "tags": script.get("tags", []),
        }
    )

    creative_brief = script.get("creative_brief", {})
    if creative_brief:
        with st.expander("查看创作骨架 brief", expanded=False):
            st.json(creative_brief)

    quality_review = script.get("quality_review", {})
    if quality_review:
        with st.expander("查看剧本质检结果", expanded=False):
            st.json(quality_review)

    story_beats = script.get("story_beats", [])
    if story_beats:
        st.markdown("**故事节拍**")
        st.json(story_beats)

    st.markdown("**直接编辑当前剧本**")
    st.caption("如果你只想微调现有内容，可以直接在下面改，不需要回退重写。")
    editor_left, editor_right = st.columns(2)
    editor_left.text_input("剧本标题", key="ui_script_editor_title")
    editor_right.text_input("开场钩子", key="ui_script_editor_hook")
    st.text_area(
        "创作剧本文本",
        key="ui_script_editor_text",
        height=260,
    )
    st.text_area(
        "Text-to-Video 提示词",
        key="ui_script_editor_prompt",
        height=160,
    )
    st.text_input("封面文案", key="ui_script_editor_cover_text")
    if st.button("保存我手动编辑的剧本", use_container_width=False):
        _clear_error()
        _apply_script_editor_to_state(state)
        st.success("已保存当前手动编辑内容。接下来你可以直接确认剧本，或继续修改。")

    shot_outline = script.get("shot_outline", [])
    if shot_outline:
        st.markdown("**分镜大纲**")
        st.json(shot_outline)

    if allow_actions:
        st.info("剧本确认和重写入口已经移到右侧对话区。这里可以专心看内容，或者直接手动编辑剧本。")


def _render_video_panel(state: WorkflowState) -> None:
    st.subheader("步骤 5：视频生成")
    settings = get_settings()

    if not settings.enable_video_pipeline:
        st.info("当前配置已关闭视频生成。")
        return

    if st.session_state["ui_stage"] == "video_ready":
        st.info("剧本已确认。生成按钮已经移动到右侧对话区，这里会在生成后显示结果。")

    if st.session_state["ui_stage"] == "video_done":
        st.json(
            {
                "video_status": state.get("video_status"),
                "video_url": state.get("video_url"),
                "local_video_path": state.get("local_video_path"),
                "error": state.get("error"),
                "desired_video_duration_seconds": state.get("desired_video_duration_seconds"),
                "script_target_duration_seconds": state.get("script", {}).get("target_duration_seconds"),
            }
        )
        if state.get("video_status") == "failed" and not state.get("error"):
            st.warning("视频任务失败了，但当前响应里没有提取到明确错误信息。下面的原始响应可以帮助继续定位。")

        video_raw = state.get("video_raw", {})
        if video_raw:
            with st.expander("视频接口原始响应", expanded=state.get("video_status") == "failed"):
                st.json(video_raw)

        local_video_path = state.get("local_video_path")
        if local_video_path:
            file_path = Path(local_video_path)
            if file_path.exists():
                video_bytes = file_path.read_bytes()
                st.video(video_bytes)
                st.download_button(
                    "下载视频文件",
                    data=video_bytes,
                    file_name=file_path.name,
                    mime="video/mp4",
                    use_container_width=True,
                )

        if state.get("video_status") == "failed" and _is_duration_related_video_error(state):
            st.warning("当前视频生成失败和秒数有关。你可以先修改秒数，系统会重写对应长度的剧本，再回到剧本审核。")
            retry_duration = st.number_input(
                "重新创作的目标秒数",
                min_value=2,
                max_value=15,
                value=max(2, min(15, int(state.get("desired_video_duration_seconds", 5)))),
                step=1,
                key="ui_video_retry_duration_seconds",
            )
            if st.button("按新秒数重写剧本", use_container_width=True):
                _clear_error()
                state["desired_video_duration_seconds"] = int(retry_duration)
                state.setdefault("script_feedback_history", []).append(
                    f"请把剧本严格改为 {int(retry_duration)} 秒，并确保镜头节奏和文案长度与这个时长匹配。"
                )
                state["script_feedback"] = (
                    f"请把剧本严格改为 {int(retry_duration)} 秒，并确保镜头节奏和文案长度与这个时长匹配。"
                )
                state["video_status"] = "pending"
                state["video_url"] = None
                state["local_video_path"] = None
                state["video_raw"] = {}
                state["error"] = None
                trace_placeholder = st.empty()
                with st.spinner("正在按新秒数重写剧本..."):
                    _run_node(script_node, state, trace_placeholder=trace_placeholder)
                st.session_state["ui_stage"] = "script_review"
                st.rerun()



def _start_workflow(*, trace_placeholder: Any | None = None) -> None:
    if not _require_keys("planning", "search"):
        return

    raw_input = st.session_state.get("ui_raw_input", "").strip()
    creator_id = st.session_state.get("ui_creator_id", "").strip()
    platform = st.session_state.get("ui_publish_platform", "").strip()
    search_platforms = st.session_state.get("ui_search_platforms", [])

    if not raw_input:
        _set_error("请输入创作方向或需求。")
        return
    if not creator_id:
        _set_error("请输入 creator_id。")
        return
    if not platform:
        _set_error("请选择目标发布平台。")
        return
    if not search_platforms:
        _set_error("请至少勾选一个搜索平台。")
        return

    _clear_error()
    _clear_llm_events()
    state = build_initial_state(
        raw_input=raw_input,
        creator_id=creator_id,
        platform=platform,
        search_platforms=search_platforms,
    )
    state["desired_video_duration_seconds"] = int(st.session_state.get("ui_target_duration_seconds", state.get("desired_video_duration_seconds", 5)))
    with st.spinner("正在解析意图、联网检索并生成主题..."):
        _run_node(intent_node, state, trace_placeholder=trace_placeholder)
        _run_node(retrieval_node, state, trace_placeholder=trace_placeholder)
        _run_node(topic_node, state, trace_placeholder=trace_placeholder)

    st.session_state["ui_workflow_state"] = state
    st.session_state["ui_stage"] = "topic_review"


def _probe_planning_llm() -> dict[str, Any]:
    async def _runner() -> dict[str, Any]:
        payload = await call_llm_json('请直接返回 JSON：{"ok": true}', trace_name="connectivity_check")
        return {
            "status": "ok",
            "result": payload,
        }

    try:
        with capture_llm_trace() as trace_events:
            with use_runtime_api_keys(**_runtime_api_keys()):
                result = asyncio.run(_runner())
        _append_llm_events(list(trace_events))
        return result
    except Exception as exc:  # pragma: no cover - runtime integration path
        return {
            "status": "error",
            "detail": str(exc),
        }


def _probe_search_api() -> dict[str, Any]:
    settings = get_settings()
    search_api_key = st.session_state.get("ui_search_api_key", "").strip() or settings.search_api_key
    selected_platforms = st.session_state.get("ui_search_platforms", []) or [
        st.session_state.get("ui_publish_platform", "bilibili") or "bilibili"
    ]

    async def _probe_one(platform: str) -> dict[str, Any]:
        results = await search_web(
            query="春日出行",
            top_k=1,
            platform=platform,
            search_kind="content",
        )
        first = results[0] if results else {}
        return {
            "status": "ok",
            "result_count": len(results),
            "sample_title": first.get("title", ""),
            "sample_source": first.get("source", ""),
            "sample_url": first.get("url", ""),
        }

    async def _runner() -> dict[str, Any]:
        raw_results = await asyncio.gather(
            *[_probe_one(platform) for platform in selected_platforms],
            return_exceptions=True,
        )
        per_platform: dict[str, Any] = {}
        success_count = 0
        for platform, item in zip(selected_platforms, raw_results):
            if isinstance(item, Exception):
                per_platform[platform] = {
                    "status": "error",
                    "detail": str(item),
                }
            else:
                per_platform[platform] = item
                success_count += 1

        overall_status = "ok"
        if success_count == 0:
            overall_status = "error"
        elif success_count < len(selected_platforms):
            overall_status = "partial"

        return {
            "status": overall_status,
            "platform_results": per_platform,
            "mock_external_services": settings.mock_external_services,
            "search_key_present": bool(search_api_key),
            "search_provider": settings.search_api_provider,
            "search_xiaohongshu_content_mode": settings.search_xiaohongshu_content_mode,
        }

    try:
        with use_runtime_api_keys(**_runtime_api_keys()):
            return asyncio.run(_runner())
    except Exception as exc:  # pragma: no cover - runtime integration path
        return {
            "status": "error",
            "detail": str(exc),
            "mock_external_services": settings.mock_external_services,
            "search_key_present": bool(search_api_key),
            "search_provider": settings.search_api_provider,
            "search_xiaohongshu_content_mode": settings.search_xiaohongshu_content_mode,
        }


def _probe_video_api() -> dict[str, Any]:
    settings = get_settings()
    provider = settings.video_api_provider.lower().strip()
    api_key = st.session_state.get("ui_video_api_key", "").strip() or settings.video_api_key

    if not settings.enable_video_pipeline:
        return {"status": "skipped", "detail": "video pipeline disabled"}
    if not settings.video_status_api_url:
        return {"status": "skipped", "detail": "VIDEO_STATUS_API_URL is empty"}
    if not api_key:
        return {"status": "error", "detail": "视频 API Key 未填写"}

    headers = {"Authorization": f"Bearer {api_key}"}
    probe_url = settings.video_status_api_url.format(job_id="connectivity-check")

    try:
        response = httpx.get(probe_url, headers=headers, timeout=20)
        detail = response.text.strip()
        if len(detail) > 300:
            detail = f"{detail[:300]}..."

        if response.status_code in {200, 400, 404, 405}:
            return {
                "status": "ok",
                "provider": provider,
                "http_status": response.status_code,
                "detail": detail or "endpoint reachable",
            }

        return {
            "status": "error",
            "provider": provider,
            "http_status": response.status_code,
            "detail": detail or response.reason_phrase,
        }
    except Exception as exc:  # pragma: no cover - runtime integration path
        return {
            "status": "error",
            "provider": provider,
            "detail": str(exc),
        }


def _run_connectivity_checks() -> dict[str, Any]:
    return {
        "planning_llm": _probe_planning_llm(),
        "search_api": _probe_search_api(),
        "video_api": _probe_video_api(),
    }


def _is_duration_related_video_error(state: WorkflowState) -> bool:
    error_text = str(state.get("error") or "").lower()
    raw_text = str(state.get("video_raw") or "").lower()
    combined = f"{error_text} {raw_text}"
    return "duration" in combined or "seconds" in combined


def _maybe_auto_refresh_monitoring(state: WorkflowState) -> None:
    return


def _handle_douyin_oauth_callback() -> None:
    query_params = st.query_params
    code = str(query_params.get("code", "")).strip()
    state_param = str(query_params.get("state", "")).strip()
    error = str(query_params.get("error", "")).strip()
    if not any([code, state_param, error]):
        return

    try:
        if error:
            st.session_state["ui_douyin_auth_message"] = f"抖音授权失败：{error}"
            return

        expected_state = st.session_state.get("ui_douyin_auth_state", "").strip()
        if expected_state and state_param and state_param != expected_state:
            st.session_state["ui_douyin_auth_message"] = "抖音授权回调 state 不匹配，请重新发起绑定。"
            return

        creator_id = st.session_state.get("ui_creator_id", "").strip()
        client_key = st.session_state.get("ui_douyin_client_key", "").strip()
        client_secret = st.session_state.get("ui_douyin_client_secret", "").strip()
        if not creator_id:
            st.session_state["ui_douyin_auth_message"] = "请先填写 creator_id，再重新绑定抖音账号。"
            return
        if not client_key or not client_secret:
            st.session_state["ui_douyin_auth_message"] = "请先填写抖音 Client Key 和 Client Secret，再重新绑定。"
            return
        if not code:
            st.session_state["ui_douyin_auth_message"] = "授权回调中没有拿到 code。"
            return

        token_payload = exchange_douyin_access_token(
            client_key=client_key,
            client_secret=client_secret,
            code=code,
        )
        token_data = token_payload.get("data") if isinstance(token_payload.get("data"), dict) else token_payload
        access_token = str(token_data.get("access_token", "")).strip()
        open_id = str(token_data.get("open_id", "")).strip()
        user_info_payload = None
        if access_token and open_id:
            try:
                user_info_payload = fetch_douyin_user_info(access_token, open_id)
            except Exception:
                user_info_payload = None
        binding = save_douyin_binding(
            creator_id=creator_id,
            token_payload=token_payload,
            user_info_payload=user_info_payload,
        )
        nickname = binding.get("nickname") or binding.get("open_id") or "未命名账号"
        st.session_state["ui_douyin_auth_message"] = f"抖音账号绑定成功：{nickname}"
    except Exception as exc:  # pragma: no cover - runtime integration path
        st.session_state["ui_douyin_auth_message"] = f"抖音绑定失败：{exc}"
    finally:
        st.query_params.clear()


def main() -> None:
    _ensure_session_defaults()
    _handle_douyin_oauth_callback()
    _apply_runtime_settings(debug_search=st.session_state["ui_debug_search"])
    _render_sidebar()

    st.title("MCN Workflow Studio")
    st.caption("按步骤完成选题、剧本审核和视频生成，API Key 只在当前网页会话中使用。")
    main_col, chat_col = st.columns([1.65, 1.2], gap="large")

    with main_col:
        with st.container(border=True):
            _render_workflow_overview()
            _render_stage_guide(st.session_state.get("ui_stage", "input"))

            if st.session_state.get("ui_error_message"):
                st.error(st.session_state["ui_error_message"])

            with st.expander("LLM 实时交互日志", expanded=bool(st.session_state.get("ui_llm_events"))):
                _render_llm_trace_panel(
                    st.session_state.get("ui_llm_events", []),
                    state=st.session_state.get("ui_workflow_state"),
                )

            trace_placeholder = st.empty()

            if st.session_state["ui_stage"] == "input" or st.session_state["ui_workflow_state"] is None:
                st.subheader("步骤 0：输入创作需求")
                st.info("请在右侧对话区输入你的创作需求。这里保留平台和工作流选项，不再放文本输入框。")
                if st.session_state.get("ui_raw_input", "").strip():
                    st.markdown("**当前需求**")
                    st.write(st.session_state.get("ui_raw_input", "").strip())
                left, right = st.columns(2)
                left.text_input("creator_id", key="ui_creator_id")
                right.selectbox(
                    "目标发布平台 / 封面投放平台",
                    options=["douyin", "bilibili", "xiaohongshu"],
                    key="ui_publish_platform",
                )
                st.multiselect(
                    "搜索平台（可多选）",
                    options=["douyin", "bilibili", "xiaohongshu"],
                    key="ui_search_platforms",
                )
                selected_search_platforms = st.session_state.get("ui_search_platforms", [])
                if "douyin" in selected_search_platforms:
                    st.caption("已勾选抖音：会走 TikHub 的站内搜索接口。")
                if "bilibili" in selected_search_platforms:
                    st.caption("已勾选 B站：会走 TikHub 的综合搜索接口。")
                if "xiaohongshu" in selected_search_platforms:
                    st.caption("已勾选小红书：会尝试走 TikHub 的笔记搜索接口；如果账号没有该接口权限，会在检索结果里给出提示，但不会拖垮其他平台。")
            else:
                stage = st.session_state["ui_stage"]
                state = st.session_state["ui_workflow_state"]
                _maybe_auto_refresh_monitoring(state)
                _render_rollback_controls(state, stage)
                _render_intent_and_search(state)

                try:
                    if stage == "topic_review":
                        _render_topic_review(state, allow_actions=True)
                    elif stage == "script_review":
                        _render_topic_review(state, allow_actions=False)
                        st.divider()
                        _render_script_review(state, allow_actions=True)
                    elif stage in {"video_ready", "video_done"}:
                        _render_topic_review(state, allow_actions=False)
                        st.divider()
                        _render_script_review(state, allow_actions=False)
                        st.divider()
                        _render_video_panel(state)
                except Exception as exc:  # pragma: no cover - runtime integration path
                    _set_error(str(exc))
                    st.error(str(exc))

    with chat_col:
        with st.container(border=True):
            _render_chat_panel(trace_placeholder=trace_placeholder)


if __name__ == "__main__":
    main()
