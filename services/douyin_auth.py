from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


DOUYIN_AUTHORIZE_URL = "https://open.douyin.com/platform/oauth/connect/"
DOUYIN_ACCESS_TOKEN_URL = "https://open.douyin.com/oauth/access_token/"
DOUYIN_USER_INFO_URL = "https://open.douyin.com/oauth/userinfo/"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auth_store_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent
    path = base_dir / "artifacts" / "auth" / "douyin_bindings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_store() -> dict[str, Any]:
    path = _auth_store_path()
    if not path.exists():
        return {"bindings": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"bindings": {}}


def _save_store(payload: dict[str, Any]) -> None:
    _auth_store_path().write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_douyin_authorize_url(
    *,
    client_key: str,
    redirect_uri: str,
    state: str,
    scopes: list[str] | None = None,
) -> str:
    resolved_scopes = scopes or ["user_info", "video.create"]
    query = urlencode(
        {
            "client_key": client_key,
            "response_type": "code",
            "scope": ",".join(resolved_scopes),
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"{DOUYIN_AUTHORIZE_URL}?{query}"


def exchange_douyin_access_token(
    *,
    client_key: str,
    client_secret: str,
    code: str,
) -> dict[str, Any]:
    response = httpx.post(
        DOUYIN_ACCESS_TOKEN_URL,
        data={
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def fetch_douyin_user_info(access_token: str, open_id: str) -> dict[str, Any]:
    response = httpx.get(
        DOUYIN_USER_INFO_URL,
        params={
            "access_token": access_token,
            "open_id": open_id,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def save_douyin_binding(
    *,
    creator_id: str,
    token_payload: dict[str, Any],
    user_info_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    data = token_payload.get("data") if isinstance(token_payload.get("data"), dict) else token_payload
    access_token = str(data.get("access_token", "")).strip()
    refresh_token = str(data.get("refresh_token", "")).strip()
    open_id = str(data.get("open_id", "")).strip()
    expires_in = int(data.get("expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_expires_in") or 0)
    now = datetime.now(timezone.utc)

    user_data = {}
    if isinstance(user_info_payload, dict):
        user_data = user_info_payload.get("data") if isinstance(user_info_payload.get("data"), dict) else user_info_payload

    binding = {
        "platform": "douyin",
        "creator_id": creator_id,
        "open_id": open_id,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat() if expires_in else "",
        "refresh_expires_at": (now + timedelta(seconds=refresh_expires_in)).isoformat() if refresh_expires_in else "",
        "nickname": str(user_data.get("nickname", "")).strip(),
        "avatar": str(user_data.get("avatar", "")).strip(),
        "union_id": str(data.get("union_id", "")).strip(),
        "scope": data.get("scope", ""),
        "updated_at": _utc_now_iso(),
        "token_raw": token_payload,
        "user_raw": user_info_payload or {},
    }

    store = _load_store()
    bindings = store.setdefault("bindings", {})
    bindings[creator_id] = binding
    _save_store(store)
    return binding


def get_douyin_binding(creator_id: str) -> dict[str, Any] | None:
    store = _load_store()
    bindings = store.get("bindings", {})
    binding = bindings.get(creator_id)
    return binding if isinstance(binding, dict) else None


def delete_douyin_binding(creator_id: str) -> None:
    store = _load_store()
    bindings = store.get("bindings", {})
    if creator_id in bindings:
        del bindings[creator_id]
        _save_store(store)
