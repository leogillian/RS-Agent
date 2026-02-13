"""HTTP API router for /api/agent."""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.__version__ import __version__
from backend.config import settings
from backend.db import add_message, create_conversation, get_conversation, list_conversations, update_conversation_status
from backend.services.intent_router import Intent, detect_intent
from backend.services import orchestrator_controller as orch
from backend.services.trading_kb_service import KBQueryError, query_kb
from backend.services.kb_query_enhanced import enhanced_kb_query
from backend.services.confirmer_service import get_display as confirmer_get_display
from backend.services.confirmer_service import parse_feedback as confirmer_parse_feedback
from backend.services.defender_service import check_draft
from backend.services.editor_service import render_final

# 上传 ID -> 服务器本地路径，供 /api/agent 解析 imageIds 为路径传给 KB
_UPLOAD_STORE: Dict[str, str] = {}

router = APIRouter()


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
        _UPLOAD_STORE[uid] = str(path.resolve())
        ids.append(uid)
    return {"imageIds": ids}


def _resolve_image_paths(image_ids: Optional[List[str]]) -> List[str]:
    """将上传返回的 imageIds 解析为本地路径，供 query_kb 使用。"""
    if not image_ids:
        return []
    return [_UPLOAD_STORE[i] for i in image_ids if i in _UPLOAD_STORE]


def _kb_image_paths_to_urls(paths: List[str]) -> List[str]:
    """将 KB 导出的本地图片路径转为前端可访问的 URL（/api/kb-images/文件名）。"""
    return [f"/api/kb-images/{os.path.basename(p)}" for p in paths]


def _sse(event: str, data: dict) -> str:
    """Encode one Server-Sent Events message."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _trace_step(phase: str, title: str, detail: str | None = None, level: str = "info") -> dict:
    step = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "phase": phase,
        "title": title,
        "detail": detail or "",
        "level": level,
    }
    return step


def _short_url(url: str) -> str:
    """简化 URL 展示：scheme/query/fragment 不重要时尽量省略。"""
    try:
        p = urlparse(url)
        host = p.netloc or ""
        path = p.path or ""
        if host and path:
            return f"{host}{path}"
        return url
    except Exception:
        return url


def _kv_detail(**kvs: object) -> str:
    """将 detail 标准化为 key=value | key=value ... 形式，方便前端扫读。"""
    parts: list[str] = []
    for k, v in kvs.items():
        if v is None:
            continue
        if isinstance(v, str):
            vv = v.strip()
            if not vv:
                continue
            vv = vv.replace("\n", " ").replace("\r", " ")
        elif isinstance(v, bool):
            vv = "true" if v else "false"
        else:
            vv = str(v)
        parts.append(f"{k}={vv}")
    return " | ".join(parts)


@router.post("/agent/stream")
def agent_stream_endpoint(req: AgentRequest):
    """流式版本的 /api/agent：以 SSE 输出 trace + final，前端可实时展示“思考过程”。

    约定事件：
    - event=trace: 逐步输出执行轨迹（不包含模型内部推理原文）
    - event=final: 最终结果，数据结构与 AgentResponse 对齐
    - event=error: 出错时输出，随后结束流
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    def gen():
        trace_steps: List[dict] = []
        conv_id_for_trace: Optional[str] = None
        llm_chat_short = (
            _short_url(f"{settings.llm_base_url.rstrip('/')}/chat/completions")
            if (getattr(settings, "llm_base_url", "") or "").strip()
            else "/chat/completions"
        )
        img_gen_short = _short_url(getattr(settings, "image_gen_url", "") or "")

        def emit_trace(phase: str, title: str, detail: str | None = None, level: str = "info"):
            step = _trace_step(phase=phase, title=title, detail=detail, level=level)
            trace_steps.append(step)
            return _sse("trace", step)

        try:
            t0 = time.time()
            yield emit_trace(
                "INTENT",
                "routers.agent.agent_stream_endpoint · 收到请求",
                _kv_detail(session_id=("none" if req.sessionId is None else req.sessionId)),
            )

            # 新会话：自动检测意图
            if req.sessionId is None:
                t_intent = time.time()
                intent = detect_intent(text)
                yield emit_trace(
                    "INTENT",
                    "services.intent_router.detect_intent · 完成",
                    _kv_detail(intent=intent.value, duration_ms=int((time.time() - t_intent) * 1000)),
                )

                if intent is Intent.KB_QUERY:
                    image_paths = _resolve_image_paths(req.imageIds)
                    try:
                        # 方案 B：LLM 扩展多 query -> 多次 KB 检索合并 -> LLM 综合输出（失败则回退 raw）
                        yield emit_trace(
                            "KB",
                            "services.kb_query_enhanced.enhanced_kb_query · 开始",
                            _kv_detail(
                                target="internal",
                                has_query_image=bool(req.imageIds),
                                llm_enabled=bool(getattr(settings, "kb_query_llm_enabled", True)),
                                max_subqueries=getattr(settings, "kb_query_max_subqueries", 4),
                            ),
                        )
                        t_kb = time.time()
                        result = enhanced_kb_query(text, image_paths or None)
                    except KBQueryError as exc:
                        yield _sse("error", {"message": str(exc)})
                        return
                    final_markdown = str(result.get("final_markdown") or "")
                    raw_markdown = str(result.get("raw_markdown") or "")
                    sub_queries = result.get("sub_queries") or []
                    used_llm = bool(result.get("used_llm"))
                    image_urls = _kb_image_paths_to_urls(list(result.get("image_paths") or []))
                    yield emit_trace(
                        "KB",
                        "services.kb_query_enhanced.enhanced_kb_query · 完成",
                        _kv_detail(
                            images=len(image_urls),
                            used_llm=used_llm,
                            subqueries=(len(sub_queries) if isinstance(sub_queries, list) else 0),
                            duration_ms=int((time.time() - t_kb) * 1000),
                        ),
                    )

                    conv_id = __import__("uuid").uuid4().hex
                    conv_id_for_trace = conv_id
                    create_conversation(conv_id, intent=intent.value, status="done")
                    add_message(conv_id, role="user", payload_type="USER_QUERY", content=text)

                    # 先落 trace，后落最终回答，便于历史回放时附着到下一条助手消息
                    add_message(conv_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                    add_message(
                        conv_id,
                        role="assistant",
                        payload_type="KB_ANSWER",
                        content=final_markdown or "[空结果]",
                    )
                    content = {
                        "markdown": final_markdown,
                        "images": image_urls,
                        # 额外信息（前端可选择性展示/调试）
                        "usedLLM": used_llm,
                        "subQueries": sub_queries,
                        "rawMarkdown": raw_markdown,
                        "kbRuns": result.get("kb_runs") or [],
                    }
                    final = {
                        "sessionId": None,
                        "intent": intent.value,
                        "payloadType": "KB_ANSWER",
                        "content": content,
                    }
                    yield _sse("final", final)
                    return

                # ORCH_FLOW：创建 orchestrator 会话并返回 open_questions
                yield emit_trace(
                    "COLLECT",
                    "services.orchestrator_controller.create_session · 创建会话",
                )
                sess = orch.create_session(user_request=text)
                conv_id_for_trace = sess.session_id
                llm_hint = (
                    f"http:POST {llm_chat_short}"
                    if (getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", ""))
                    else "LLM 未配置则回退规则版"
                )
                yield emit_trace(
                    "COLLECT",
                    "services.orchestrator_controller.get_open_questions · 开始",
                    _kv_detail(
                        target="internal",
                        calls_hint="kb:subprocess run_all_sources.py, llm_collect",
                        llm_configured=bool(getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", "")),
                        llm_endpoint=(llm_hint if "http:POST" in llm_hint else ""),
                        llm_model=(settings.llm_model if (getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", "")) else ""),
                    ),
                )
                t_collect = time.time()
                questions = orch.get_open_questions(sess)
                yield emit_trace(
                    "COLLECT",
                    "services.orchestrator_controller.get_open_questions · 完成",
                    _kv_detail(questions=len(questions), duration_ms=int((time.time() - t_collect) * 1000)),
                )
                create_conversation(sess.session_id, intent=intent.value, status="active")
                add_message(sess.session_id, role="user", payload_type="USER_REQUEST", content=text)
                # 先落 trace，后落本轮助手内容，便于历史回放时将 trace 附着到后续助手消息
                add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                if questions:
                    joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
                    add_message(
                        sess.session_id,
                        role="assistant",
                        payload_type="OPEN_QUESTIONS",
                        content=joined,
                    )
                final = {
                    "sessionId": sess.session_id,
                    "intent": intent.value,
                    "payloadType": "OPEN_QUESTIONS",
                    "content": {"questions": questions},
                }
                yield _sse("final", final)
                return

            # 已有会话：根据 orchestrator state 推进流程
            yield emit_trace(
                "INTENT",
                "services.orchestrator_controller.get_session · 加载会话",
                _kv_detail(session_id=req.sessionId),
            )
            sess = orch.get_session(req.sessionId)
            if not sess:
                yield _sse("error", {"message": "session 不存在或已过期"})
                return
            conv_id_for_trace = sess.session_id
            yield emit_trace(
                "INTENT",
                "services.orchestrator_controller · 会话状态",
                _kv_detail(state=sess.state),
            )

            # 用户回答 COLLECT 阶段 open_questions，生成草稿后仅展示草稿 + 提示确认
            if sess.state in ("WAITING_ANSWERS", "COLLECT"):
                llm_hint = (
                    f"http:POST {llm_chat_short}"
                    if (getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", ""))
                    else "LLM 未配置则回退规则版"
                )
                img_hint = (
                    f"http:POST {img_gen_short}"
                    if (img_gen_short and getattr(settings, "image_gen_enabled", True))
                    else ""
                )
                yield emit_trace(
                    "BUILD_DRAFT",
                    "services.orchestrator_controller.answer_questions · 开始",
                    _kv_detail(
                        target="internal",
                        calls_hint="kb:subprocess run_all_sources.py, llm_build_draft_sections, image_gen(optional)",
                        llm_configured=bool(getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", "")),
                        llm_endpoint=(llm_hint if "http:POST" in llm_hint else ""),
                        llm_model=(settings.llm_model if (getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", "")) else ""),
                        image_gen_enabled=bool(getattr(settings, "image_gen_enabled", True)),
                        image_gen_endpoint=(img_hint if "http:POST" in img_hint else ""),
                        image_gen_model=(getattr(settings, "image_gen_model", "") if (img_hint and "http:POST" in img_hint) else ""),
                    ),
                )
                add_message(sess.session_id, role="user", payload_type="USER_ANSWER", content=text)
                t_build = time.time()
                sess, _ = orch.answer_questions(sess.session_id, text)
                display_result = confirmer_get_display(sess.draft_struct)
                add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                add_message(
                    sess.session_id,
                    role="assistant",
                    payload_type="DRAFT",
                    content=display_result.display_content,
                )
                yield emit_trace(
                    "BUILD_DRAFT",
                    "services.orchestrator_controller.answer_questions · 完成",
                    _kv_detail(duration_ms=int((time.time() - t_build) * 1000)),
                )
                final = {
                    "sessionId": sess.session_id,
                    "intent": Intent.ORCH_FLOW.value,
                    "payloadType": "DRAFT",
                    "content": {
                        "markdown": display_result.display_content,
                        "prompt_to_user": display_result.prompt_to_user,
                    },
                }
                yield _sse("final", final)
                return

            # 用户对草稿的反馈：Confirmer 解析 status，再进入 DEFEND 或追问/生成终稿
            if sess.state in ("DRAFT_READY", "CONFIRMING"):
                llm_hint = (
                    f"http:POST {llm_chat_short}"
                    if (getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", ""))
                    else "LLM 未配置则走关键词规则"
                )
                yield emit_trace(
                    "CONFIRM",
                    "services.confirmer_service.parse_feedback · 开始",
                    _kv_detail(
                        target="internal",
                        calls_hint="llm_confirmer_parse(optional) or keyword_fallback",
                        llm_configured=bool(getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", "")),
                        llm_endpoint=(llm_hint if "http:POST" in llm_hint else ""),
                        llm_model=(settings.llm_model if (getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", "")) else ""),
                    ),
                )
                add_message(sess.session_id, role="user", payload_type="USER_FEEDBACK", content=text)
                t_confirm = time.time()
                parse_result = confirmer_parse_feedback(sess.draft_struct, text)
                yield emit_trace(
                    "CONFIRM",
                    "services.confirmer_service.parse_feedback · 完成",
                    _kv_detail(status=parse_result.status, duration_ms=int((time.time() - t_confirm) * 1000)),
                )

                if parse_result.status == "needs_clarification" and parse_result.clarification_question:
                    add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                    add_message(
                        sess.session_id,
                        role="assistant",
                        payload_type="OPEN_QUESTIONS",
                        content=parse_result.clarification_question,
                    )
                    final = {
                        "sessionId": sess.session_id,
                        "intent": Intent.ORCH_FLOW.value,
                        "payloadType": "OPEN_QUESTIONS",
                        "content": {"questions": [parse_result.clarification_question]},
                    }
                    yield _sse("final", final)
                    return
                if parse_result.status in ("request_redo_partial", "request_redo_full"):
                    add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                    add_message(
                        sess.session_id,
                        role="assistant",
                        payload_type="INFO",
                        content="已记录您的重做要求；当前版本将先进入完整性检查并生成文档。后续版本将支持按块/整体重做。若需继续，请回复「确认」。",
                    )
                    final = {
                        "sessionId": sess.session_id,
                        "intent": Intent.ORCH_FLOW.value,
                        "payloadType": "INFO",
                        "content": {"message": "已记录重做要求，请回复「确认」继续。"},
                    }
                    yield _sse("final", final)
                    return
                if parse_result.status == "revised" and parse_result.suggested_draft_updates:
                    br = sess.draft_struct.setdefault("business_requirement", {})
                    for key, val in (parse_result.suggested_draft_updates.get("business_requirement") or {}).items():
                        if isinstance(val, str):
                            br[key] = val
                sess.state = "DEFENDING"

                yield emit_trace(
                    "DEFEND",
                    "services.defender_service.check_draft · 开始",
                )
                t_defend = time.time()
                result = check_draft(sess.draft_struct)
                yield emit_trace(
                    "DEFEND",
                    "services.defender_service.check_draft · 完成",
                    _kv_detail(is_complete=result.is_complete, duration_ms=int((time.time() - t_defend) * 1000)),
                )
                if not result.is_complete and result.questions:
                    sess.last_defend_questions = result.questions
                    joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(result.questions))
                    add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                    add_message(
                        sess.session_id,
                        role="assistant",
                        payload_type="OPEN_QUESTIONS",
                        content=joined,
                    )
                    yield emit_trace(
                        "DEFEND",
                        "services.defender_service.check_draft · 待补充信息",
                        _kv_detail(questions=len(result.questions)),
                        level="warn",
                    )
                    final = {
                        "sessionId": sess.session_id,
                        "intent": Intent.ORCH_FLOW.value,
                        "payloadType": "OPEN_QUESTIONS",
                        "content": {"questions": result.questions},
                    }
                    yield _sse("final", final)
                    return

                yield emit_trace(
                    "EDITOR",
                    "services.editor_service.render_final · 开始",
                )
                t_edit = time.time()
                final_md = render_final(sess.draft_struct)
                yield emit_trace(
                    "EDITOR",
                    "services.editor_service.render_final · 完成",
                    _kv_detail(duration_ms=int((time.time() - t_edit) * 1000)),
                )
                add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                add_message(
                    sess.session_id,
                    role="assistant",
                    payload_type="FINAL_DOC",
                    content=final_md,
                )
                update_conversation_status(sess.session_id, status="done")
                final = {
                    "sessionId": sess.session_id,
                    "intent": Intent.ORCH_FLOW.value,
                    "payloadType": "FINAL_DOC",
                    "content": {"markdown": final_md},
                }
                yield _sse("final", final)
                return

            # DEFEND 阶段用户补充说明后，再次检查并尝试生成 FINAL_DOC
            if sess.state == "DEFENDING":
                yield emit_trace(
                    "DEFEND",
                    "services.orchestrator_controller.apply_defend_answers · 应用补充说明",
                    _kv_detail(target="internal"),
                )
                add_message(sess.session_id, role="user", payload_type="USER_DEFEND", content=text)
                sess = orch.apply_defend_answers(sess.session_id, text)
                yield emit_trace(
                    "DEFEND",
                    "services.defender_service.check_draft · 开始",
                    _kv_detail(target="internal"),
                )
                t_defend2 = time.time()
                result = check_draft(sess.draft_struct)
                yield emit_trace(
                    "DEFEND",
                    "services.defender_service.check_draft · 完成",
                    _kv_detail(is_complete=result.is_complete, duration_ms=int((time.time() - t_defend2) * 1000)),
                )
                if not result.is_complete and result.questions:
                    sess.last_defend_questions = result.questions
                    joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(result.questions))
                    add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                    add_message(
                        sess.session_id,
                        role="assistant",
                        payload_type="OPEN_QUESTIONS",
                        content=joined,
                    )
                    yield emit_trace(
                        "DEFEND",
                        "services.defender_service.check_draft · 待补充信息",
                        _kv_detail(questions=len(result.questions)),
                        level="warn",
                    )
                    final = {
                        "sessionId": sess.session_id,
                        "intent": Intent.ORCH_FLOW.value,
                        "payloadType": "OPEN_QUESTIONS",
                        "content": {"questions": result.questions},
                    }
                    yield _sse("final", final)
                    return

                yield emit_trace(
                    "EDITOR",
                    "services.editor_service.render_final · 开始",
                    _kv_detail(target="internal"),
                )
                t_edit2 = time.time()
                final_md = render_final(sess.draft_struct)
                yield emit_trace(
                    "EDITOR",
                    "services.editor_service.render_final · 完成",
                    _kv_detail(duration_ms=int((time.time() - t_edit2) * 1000)),
                )
                add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
                add_message(
                    sess.session_id,
                    role="assistant",
                    payload_type="FINAL_DOC",
                    content=final_md,
                )
                update_conversation_status(sess.session_id, status="done")
                final = {
                    "sessionId": sess.session_id,
                    "intent": Intent.ORCH_FLOW.value,
                    "payloadType": "FINAL_DOC",
                    "content": {"markdown": final_md},
                }
                yield _sse("final", final)
                return

            # 其他状态：视为已完成
            yield emit_trace(
                "INTENT",
                "services.orchestrator_controller · 会话已完成",
                _kv_detail(target="internal"),
                level="warn",
            )
            add_message(sess.session_id, role="assistant", payload_type="TRACE", content=json.dumps(trace_steps, ensure_ascii=False))
            add_message(
                sess.session_id,
                role="assistant",
                payload_type="INFO",
                content="会话已完成，更多能力将在后续版本中提供。",
            )
            final = {
                "sessionId": sess.session_id,
                "intent": Intent.ORCH_FLOW.value,
                "payloadType": "INFO",
                "content": {"message": "会话已完成，更多能力将在后续版本中提供。"},
            }
            yield _sse("final", final)
        except Exception as e:
            yield _sse("error", {"message": str(e)})
            # 尽力落 trace（不影响主流程）
            try:
                if conv_id_for_trace:
                    add_message(
                        conv_id_for_trace,
                        role="assistant",
                        payload_type="TRACE",
                        content=json.dumps(trace_steps, ensure_ascii=False),
                    )
            except Exception:
                pass

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/agent", response_model=AgentResponse)
def agent_endpoint(req: AgentRequest) -> AgentResponse:
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    # 新会话：自动检测意图
    if req.sessionId is None:
        intent = detect_intent(text)
        if intent is Intent.KB_QUERY:
            image_paths = _resolve_image_paths(req.imageIds)
            try:
                result = enhanced_kb_query(text, image_paths or None)
            except KBQueryError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            final_markdown = str(result.get("final_markdown") or "")
            raw_markdown = str(result.get("raw_markdown") or "")
            sub_queries = result.get("sub_queries") or []
            used_llm = bool(result.get("used_llm"))
            image_urls = _kb_image_paths_to_urls(list(result.get("image_paths") or []))
            conv_id = __import__("uuid").uuid4().hex
            create_conversation(conv_id, intent=intent.value, status="done")
            add_message(conv_id, role="user", payload_type="USER_QUERY", content=text)
            add_message(
                conv_id,
                role="assistant",
                payload_type="KB_ANSWER",
                content=final_markdown or "[空结果]",
            )
            content = {
                "markdown": final_markdown,
                "images": image_urls,
                "usedLLM": used_llm,
                "subQueries": sub_queries,
                "rawMarkdown": raw_markdown,
                "kbRuns": result.get("kb_runs") or [],
            }
            return AgentResponse(
                sessionId=None,
                intent=intent,
                payloadType="KB_ANSWER",
                content=content,
            )

        # ORCH_FLOW：创建 orchestrator 会话并返回 open_questions
        sess = orch.create_session(user_request=text)
        questions = orch.get_open_questions(sess)
        create_conversation(sess.session_id, intent=intent.value, status="active")
        add_message(sess.session_id, role="user", payload_type="USER_REQUEST", content=text)
        # 记录助手提出的问题
        if questions:
            joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
            add_message(
                sess.session_id,
                role="assistant",
                payload_type="OPEN_QUESTIONS",
                content=joined,
            )
        return AgentResponse(
            sessionId=sess.session_id,
            intent=intent,
            payloadType="OPEN_QUESTIONS",
            content={"questions": questions},
        )

    # 已有会话：根据 orchestrator state 推进流程
    sess = orch.get_session(req.sessionId)
    if not sess:
        raise HTTPException(status_code=404, detail="session 不存在或已过期")

    # 用户回答 COLLECT 阶段 open_questions，生成草稿后仅展示草稿 + 提示确认（P2：Confirmer 展示，不在此轮 DEFEND/Editor）
    if sess.state in ("WAITING_ANSWERS", "COLLECT"):
        add_message(sess.session_id, role="user", payload_type="USER_ANSWER", content=text)
        sess, _ = orch.answer_questions(sess.session_id, text)
        display_result = confirmer_get_display(sess.draft_struct)
        add_message(
            sess.session_id,
            role="assistant",
            payload_type="DRAFT",
            content=display_result.display_content,
        )
        return AgentResponse(
            sessionId=sess.session_id,
            intent=Intent.ORCH_FLOW,
            payloadType="DRAFT",
            content={
                "markdown": display_result.display_content,
                "prompt_to_user": display_result.prompt_to_user,
            },
        )

    # 用户对草稿的反馈：Confirmer 解析 status，再进入 DEFEND 或追问/重做分支（P2）
    if sess.state in ("DRAFT_READY", "CONFIRMING"):
        add_message(sess.session_id, role="user", payload_type="USER_FEEDBACK", content=text)
        parse_result = confirmer_parse_feedback(sess.draft_struct, text)

        if parse_result.status == "needs_clarification" and parse_result.clarification_question:
            add_message(
                sess.session_id,
                role="assistant",
                payload_type="OPEN_QUESTIONS",
                content=parse_result.clarification_question,
            )
            return AgentResponse(
                sessionId=sess.session_id,
                intent=Intent.ORCH_FLOW,
                payloadType="OPEN_QUESTIONS",
                content={"questions": [parse_result.clarification_question]},
            )
        if parse_result.status in ("request_redo_partial", "request_redo_full"):
            add_message(
                sess.session_id,
                role="assistant",
                payload_type="INFO",
                content="已记录您的重做要求；当前版本将先进入完整性检查并生成文档。后续版本将支持按块/整体重做。若需继续，请回复「确认」。",
            )
            return AgentResponse(
                sessionId=sess.session_id,
                intent=Intent.ORCH_FLOW,
                payloadType="INFO",
                content={"message": "已记录重做要求，请回复「确认」继续。"},
            )
        if parse_result.status == "revised" and parse_result.suggested_draft_updates:
            br = sess.draft_struct.setdefault("business_requirement", {})
            for key, val in (parse_result.suggested_draft_updates.get("business_requirement") or {}).items():
                if isinstance(val, str):
                    br[key] = val
        sess.state = "DEFENDING"

        result = check_draft(sess.draft_struct)
        if not result.is_complete and result.questions:
            sess.last_defend_questions = result.questions
            joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(result.questions))
            add_message(
                sess.session_id,
                role="assistant",
                payload_type="OPEN_QUESTIONS",
                content=joined,
            )
            return AgentResponse(
                sessionId=sess.session_id,
                intent=Intent.ORCH_FLOW,
                payloadType="OPEN_QUESTIONS",
                content={"questions": result.questions},
            )

        final_md = render_final(sess.draft_struct)
        add_message(
            sess.session_id,
            role="assistant",
            payload_type="FINAL_DOC",
            content=final_md,
        )
        update_conversation_status(sess.session_id, status="done")
        return AgentResponse(
            sessionId=sess.session_id,
            intent=Intent.ORCH_FLOW,
            payloadType="FINAL_DOC",
            content={"markdown": final_md},
        )

    # DEFEND 阶段用户补充说明后，再次检查并尝试生成 FINAL_DOC
    if sess.state == "DEFENDING":
        add_message(sess.session_id, role="user", payload_type="USER_DEFEND", content=text)
        sess = orch.apply_defend_answers(sess.session_id, text)
        result = check_draft(sess.draft_struct)
        if not result.is_complete and result.questions:
            sess.last_defend_questions = result.questions
            joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(result.questions))
            add_message(
                sess.session_id,
                role="assistant",
                payload_type="OPEN_QUESTIONS",
                content=joined,
            )
            return AgentResponse(
                sessionId=sess.session_id,
                intent=Intent.ORCH_FLOW,
                payloadType="OPEN_QUESTIONS",
                content={"questions": result.questions},
            )

        final_md = render_final(sess.draft_struct)
        add_message(
            sess.session_id,
            role="assistant",
            payload_type="FINAL_DOC",
            content=final_md,
        )
        update_conversation_status(sess.session_id, status="done")
        return AgentResponse(
            sessionId=sess.session_id,
            intent=Intent.ORCH_FLOW,
            payloadType="FINAL_DOC",
            content={"markdown": final_md},
        )

    # 其他状态：视为已完成
    return AgentResponse(
        sessionId=sess.session_id,
        intent=Intent.ORCH_FLOW,
        payloadType="INFO",
        content={"message": "会话已完成，更多能力将在后续版本中提供。"},
    )

