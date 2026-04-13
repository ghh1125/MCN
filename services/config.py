from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "MCN Workflow"
    api_prefix: str = "/api"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_debug: bool = False

    planning_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("PLANNING_API_KEY", "LLM_API_KEY"),
    )
    planning_base_url: str = Field(
        default="",
        validation_alias=AliasChoices("PLANNING_BASE_URL", "LLM_BASE_URL"),
    )
    planning_model: str = Field(
        default="",
        validation_alias=AliasChoices("PLANNING_MODEL", "LLM_MODEL"),
    )
    planning_timeout_seconds: int = Field(
        default=60,
        validation_alias=AliasChoices("PLANNING_TIMEOUT_SECONDS", "LLM_TIMEOUT_SECONDS"),
    )
    planning_max_retries: int = Field(
        default=3,
        validation_alias=AliasChoices("PLANNING_MAX_RETRIES", "LLM_MAX_RETRIES"),
    )
    planning_temperature: float = Field(
        default=0.3,
        validation_alias=AliasChoices("PLANNING_TEMPERATURE", "LLM_TEMPERATURE"),
    )

    search_api_provider: str = Field(
        default="",
        validation_alias=AliasChoices("SEARCH_API_PROVIDER", "TIKHUB_PROVIDER"),
    )
    search_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("SEARCH_API_KEY", "TIKHUB_API_KEY", "TIKHUB_API_TOKEN"),
    )
    search_api_url: str = Field(
        default="",
        validation_alias=AliasChoices("SEARCH_API_URL", "TIKHUB_BASE_URL"),
    )
    search_api_timeout_seconds: int = Field(
        default=20,
        validation_alias=AliasChoices("SEARCH_API_TIMEOUT_SECONDS", "TIKHUB_TIMEOUT_SECONDS"),
    )
    search_api_top_k: int = Field(
        default=8,
        validation_alias=AliasChoices("SEARCH_API_TOP_K", "TIKHUB_TOP_K"),
    )
    search_debug_save_raw: bool = Field(
        default=False,
        validation_alias=AliasChoices("SEARCH_DEBUG_SAVE_RAW", "DEBUG_SEARCH"),
    )
    search_debug_output_dir: str = Field(
        default="artifacts/search_debug",
        validation_alias=AliasChoices("SEARCH_DEBUG_OUTPUT_DIR", "DEBUG_SEARCH_OUTPUT_DIR"),
    )
    search_xiaohongshu_content_mode: str = Field(
        default="search_notes",
        validation_alias=AliasChoices(
            "SEARCH_XIAOHONGSHU_CONTENT_MODE",
            "TIKHUB_XIAOHONGSHU_CONTENT_MODE",
        ),
    )

    enable_video_pipeline: bool = True
    save_video_to_disk: bool = True
    video_output_dir: str = "artifacts/videos"
    video_api_provider: str = Field(
        default="",
        validation_alias=AliasChoices("VIDEO_API_PROVIDER", "VIDEO_PROVIDER"),
    )
    video_api_url: str = ""
    video_status_api_url: str = ""
    video_api_key: str = ""
    video_model: str = Field(
        default="",
        validation_alias=AliasChoices("VIDEO_MODEL", "DASHSCOPE_VIDEO_MODEL"),
    )
    video_mode: str = Field(
        default="std",
        validation_alias=AliasChoices("VIDEO_MODE", "DASHSCOPE_VIDEO_MODE"),
    )
    video_aspect_ratio: str = Field(
        default="16:9",
        validation_alias=AliasChoices("VIDEO_ASPECT_RATIO", "DASHSCOPE_VIDEO_ASPECT_RATIO"),
    )
    video_duration_seconds: int = Field(
        default=5,
        validation_alias=AliasChoices("VIDEO_DURATION_SECONDS", "DASHSCOPE_VIDEO_DURATION"),
    )
    video_audio: bool = Field(
        default=False,
        validation_alias=AliasChoices("VIDEO_AUDIO", "DASHSCOPE_VIDEO_AUDIO"),
    )
    video_watermark: bool = Field(
        default=True,
        validation_alias=AliasChoices("VIDEO_WATERMARK", "DASHSCOPE_VIDEO_WATERMARK"),
    )
    video_poll_interval_seconds: int = 15
    video_max_poll_attempts: int = 60

    enable_publish_pipeline: bool = False
    publish_api_url: str = ""
    publish_api_key: str = ""

    mock_external_services: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def get_video_output_dir() -> Path:
    settings = get_settings()
    base_dir = Path(__file__).resolve().parent.parent
    output_dir = Path(settings.video_output_dir)
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir
    return output_dir


def get_search_debug_output_dir() -> Path:
    settings = get_settings()
    base_dir = Path(__file__).resolve().parent.parent
    output_dir = Path(settings.search_debug_output_dir)
    if not output_dir.is_absolute():
        output_dir = base_dir / output_dir
    return output_dir
