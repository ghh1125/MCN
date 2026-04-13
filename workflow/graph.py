from __future__ import annotations

from langgraph.graph import END, StateGraph

from services.config import get_settings
from workflow.nodes.intent import intent_node
from workflow.nodes.publish import publish_node
from workflow.nodes.retrieval import retrieval_node
from workflow.nodes.script import script_node
from workflow.nodes.topic import topic_node
from workflow.nodes.video import video_node
from workflow.state import WorkflowState, utc_now_iso


def _route_after_script(state: WorkflowState) -> str:
    settings = get_settings()
    return "video" if settings.enable_video_pipeline else END


def _route_after_video(state: WorkflowState) -> str:
    settings = get_settings()
    if settings.enable_publish_pipeline and state.get("video_status") == "done":
        return "publish"
    return END


def build_graph():
    graph = StateGraph(WorkflowState)
    graph.add_node("intent", intent_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("topic", topic_node)
    graph.add_node("script", script_node)
    graph.add_node("video", video_node)
    graph.add_node("publish", publish_node)

    graph.set_entry_point("intent")
    graph.add_edge("intent", "retrieval")
    graph.add_edge("retrieval", "topic")
    graph.add_edge("topic", "script")
    graph.add_conditional_edges("script", _route_after_script)
    graph.add_conditional_edges("video", _route_after_video)
    graph.add_edge("publish", END)
    return graph.compile()


workflow = build_graph()


async def run_workflow(initial_state: WorkflowState) -> WorkflowState:
    final_state = await workflow.ainvoke(initial_state)
    final_state["updated_at"] = utc_now_iso()
    return final_state
