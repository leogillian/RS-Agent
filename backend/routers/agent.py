"""HTTP API router for /api/agent.

After P0-1 refactoring this file is a **thin protocol-conversion layer**.
All business logic lives in :mod:`backend.services.agent_pipeline`.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.__version__ import __version__
from backend.auth import require_api_key
from backend.config import settings
from backend.db import get_conversation, list_conversations
from backend.services.agent_pipeline import AgentPipeline, PipelineError
from backend.services.intent_router import Intent

# ---------------------------------------------------------------------------
# Upload store (stays in router – protocol/IO concern)
# ---------------------------------------------------------------------------

_UPLOAD_STORE: Dict[str, tuple[str, float]] = {}
UPLOAD_MAX_AGE_SECONDS = 3600  # 1 小时


def _cleanup_old_uploads() -> None:
    """删除超过 UPLOAD_MAX_AGE_SECONDS 的上传条目及对应磁盘文件。"""
    now = time.time()
    to_remove = [
        uid for uid, (_, created) in _UPLOAD_STORE.items()
        if now - created > UPLOAD_MAX_AGE_SECONDS
    ]
    for uid in to_remove:
        path_str = _UPLOAD_STORE[uid][0]
        try:
            Path(path_str).unlink(missing_ok=True)
        except OSError:
            pass
        del _UPLOAD_STORE[uid]


def _resolve_image_paths(image_ids: Optional[List[str]]) -> List[str]:
    """将上传返回的 imageIds 解析为本地路径，供 pipeline 使用。"""
    if not image_ids:
        return []
    _cleanup_old_uploads()
    return [_UPLOAD_STORE[i][0] for i in image_ids if i in _UPLOAD_STORE]


def _sse(event: str, data: dict) -> str:
    """Encode one Server-Sent Events message."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# Router & models
# ---------------------------------------------------------------------------

router = APIRouter(dependencies=[Depends(require_api_key)])


class AgentRequest(BaseModel):
    sessionId: Optional[str] = None
    text: str
    imageIds: Optional[List[str]] = None


class AgentResponse(BaseModel):
    sessionId: Optional[str]
    intent: Intent
    payloadType: str
    content: dict


class ConversationSummary(BaseModel):
    id: str
    intent: str
    status: str
    created_at: str
    updated_at: str
    first_user_text: Optional[str] = None


class ConversationDetail(BaseModel):
    id: str
    intent: str
    status: str
    created_at: str
    updated_at: str
    messages: list[dict]


# ---------------------------------------------------------------------------
# Simple CRUD / utility endpoints
# ---------------------------------------------------------------------------

@router.get("/version")
def get_version() -> dict:
    return {"version": __version__}


@router.get("/conversations", response_model=list[ConversationSummary])
def get_conversations(limit: int = 20, offset: int = 0) -> list[ConversationSummary]:
    rows = list_conversations(limit=limit, offset=offset)
    return [ConversationSummary(**r) for r in rows]


@router.get("/conversations/{conv_id}", response_model=ConversationDetail)
def get_conversation_detail(conv_id: str) -> ConversationDetail:
    conv = get_conversation(conv_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    return ConversationDetail(**conv)


@router.post("/upload")
def upload_images(files: List[UploadFile] = File(...)) -> dict:
    """上传图片，返回 imageIds，供 /api/agent 请求体中的 imageIds 使用。"""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    ids: List[str] = []
    for f in files:
        if not f.content_type or not f.content_type.startswith("image/"):
            continue
        ext = os.path.splitext(f.filename or "")[1] or ".png"
        uid = str(uuid.uuid4())
        path = settings.upload_dir / f"{uid}{ext}"
        content = f.file.read()
        path.write_bytes(content)
        _UPLOAD_STORE[uid] = (str(path.resolve()), time.time())
        ids.append(uid)
    _cleanup_old_uploads()
    return {"imageIds": ids}


# ---------------------------------------------------------------------------
# Agent endpoints – delegate to AgentPipeline
# ---------------------------------------------------------------------------

@router.post("/agent/stream")
async def agent_stream_endpoint(req: AgentRequest):
    """流式版本：以 SSE 输出 trace + final 事件。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    async def gen():
        image_paths = _resolve_image_paths(req.imageIds)
        pipeline = AgentPipeline()
        async for event in pipeline.process(text, req.sessionId, image_paths or None):
            yield _sse(event["type"], event["data"])

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent", response_model=AgentResponse)
async def agent_endpoint(req: AgentRequest) -> AgentResponse:
    """非流式版本：直接返回 JSON 结果。"""
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    image_paths = _resolve_image_paths(req.imageIds)
    try:
        result = await AgentPipeline().run(text, req.sessionId, image_paths or None)
    except PipelineError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return AgentResponse(**result)
