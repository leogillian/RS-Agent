"""AgentPipeline – unified business logic for ``/api/agent`` endpoints.

Both the streaming (SSE) and non-streaming JSON endpoints share this single
pipeline.  The pipeline is exposed as a **generator** that yields event dicts:

* ``{"type": "trace", "data": {...}}``  – execution trace steps
* ``{"type": "final", "data": {...}}``  – final response payload
* ``{"type": "error", "data": {"message": ..., "status_code": ...}}``  – error

Usage (streaming)::

    for event in AgentPipeline().process(text, session_id, image_paths):
        yield _sse(event["type"], event["data"])

Usage (non-streaming)::

    result = AgentPipeline().run(text, session_id, image_paths)
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator, Dict, List, Optional
from urllib.parse import urlparse

from backend.config import settings
from backend.db import (
    add_message,
    create_conversation,
    update_conversation_status,
)
from backend.services.confirmer_service import get_display as confirmer_get_display
from backend.services.confirmer_service import parse_feedback as confirmer_parse_feedback
from backend.services.defender_service import check_draft
from backend.services.editor_service import render_final
from backend.services.intent_router import Intent, detect_intent
from backend.services.kb_query_enhanced import enhanced_kb_query
from backend.services import orchestrator_controller as orch
from backend.services.trading_kb_service import KBQueryError


# ---------------------------------------------------------------------------
# Custom exception for non-streaming callers
# ---------------------------------------------------------------------------

class PipelineError(Exception):
    """Raised by :meth:`AgentPipeline.run` when the pipeline yields an error event."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# Type alias
# ---------------------------------------------------------------------------

PipelineEvent = Dict[str, object]
"""``{"type": "trace" | "final" | "error", "data": dict}``"""


# ---------------------------------------------------------------------------
# Internal helpers (moved from routers/agent.py)
# ---------------------------------------------------------------------------

def _trace_step(phase: str, title: str, detail: str | None = None, level: str = "info") -> dict:
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "phase": phase,
        "title": title,
        "detail": detail or "",
        "level": level,
    }


def _short_url(url: str) -> str:
    try:
        p = urlparse(url)
        host = p.netloc or ""
        path = p.path or ""
        return f"{host}{path}" if host and path else url
    except Exception:
        return url


def _kv_detail(**kvs: object) -> str:
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


def kb_image_paths_to_urls(paths: List[str]) -> List[str]:
    """Convert KB-exported local image paths to frontend-accessible URLs."""
    return [f"/api/kb-images/{os.path.basename(p)}" for p in paths]


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

class AgentPipeline:
    """Encapsulates all business logic for the ``/api/agent`` endpoints.

    A new instance should be created **per-request**.
    """

    def __init__(self) -> None:
        self._trace_steps: List[dict] = []
        self._llm_chat_short = (
            _short_url(f"{settings.llm_base_url.rstrip('/')}/chat/completions")
            if (getattr(settings, "llm_base_url", "") or "").strip()
            else "/chat/completions"
        )
        self._img_gen_short = _short_url(getattr(settings, "image_gen_url", "") or "")

    # -- helpers --------------------------------------------------------

    def _emit(self, phase: str, title: str, detail: str | None = None, level: str = "info") -> PipelineEvent:
        step = _trace_step(phase, title, detail, level)
        self._trace_steps.append(step)
        return {"type": "trace", "data": step}

    def _save_trace(self, conv_id: str) -> None:
        add_message(
            conv_id,
            role="assistant",
            payload_type="TRACE",
            content=json.dumps(self._trace_steps, ensure_ascii=False),
        )

    def _llm_configured(self) -> bool:
        return bool(getattr(settings, "llm_api_key", "") and getattr(settings, "llm_base_url", ""))

    def _llm_hint(self) -> str:
        if self._llm_configured():
            return f"http:POST {self._llm_chat_short}"
        return "LLM 未配置则回退规则版"

    def _img_hint(self) -> str:
        if self._img_gen_short and getattr(settings, "image_gen_enabled", True):
            return f"http:POST {self._img_gen_short}"
        return ""

    # -- main entry points -----------------------------------------------

    async def process(
        self,
        text: str,
        session_id: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
    ) -> AsyncGenerator[PipelineEvent, None]:
        """Async generator yielding pipeline events (trace / final / error)."""
        conv_id_for_trace: Optional[str] = None

        try:
            yield self._emit(
                "INTENT",
                "pipeline · 收到请求",
                _kv_detail(session_id=("none" if session_id is None else session_id)),
            )

            if session_id is None:
                async for event in self._new_conversation(text, image_paths):
                    yield event
                return

            # Existing session: load and advance
            yield self._emit(
                "INTENT",
                "services.orchestrator_controller.get_session · 加载会话",
                _kv_detail(session_id=session_id),
            )
            sess = orch.get_session(session_id)
            if not sess:
                yield {"type": "error", "data": {"message": "session 不存在或已过期", "status_code": 404}}
                return
            conv_id_for_trace = sess.session_id
            yield self._emit(
                "INTENT",
                "services.orchestrator_controller · 会话状态",
                _kv_detail(state=sess.state),
            )

            if sess.state in ("WAITING_ANSWERS", "COLLECT"):
                async for event in self._handle_answer(sess, text):
                    yield event
            elif sess.state in ("DRAFT_READY", "CONFIRMING"):
                async for event in self._handle_confirm(sess, text):
                    yield event
            elif sess.state == "DEFENDING":
                async for event in self._handle_defend(sess, text):
                    yield event
            else:
                yield self._emit("INTENT", "services.orchestrator_controller · 会话已完成", level="warn")
                self._save_trace(sess.session_id)
                add_message(sess.session_id, role="assistant", payload_type="INFO",
                            content="会话已完成，更多能力将在后续版本中提供。")
                yield {"type": "final", "data": {
                    "sessionId": sess.session_id,
                    "intent": Intent.ORCH_FLOW.value,
                    "payloadType": "INFO",
                    "content": {"message": "会话已完成，更多能力将在后续版本中提供。"},
                }}

        except Exception as e:
            yield {"type": "error", "data": {"message": str(e), "status_code": 500}}
            try:
                if conv_id_for_trace:
                    self._save_trace(conv_id_for_trace)
            except Exception:
                pass

    async def run(
        self,
        text: str,
        session_id: Optional[str] = None,
        image_paths: Optional[List[str]] = None,
    ) -> dict:
        """Non-streaming convenience: run pipeline and return final result.

        Raises :class:`PipelineError` on error events.
        """
        result: Optional[dict] = None
        async for event in self.process(text, session_id, image_paths):
            if event["type"] == "final":
                result = event["data"]
            elif event["type"] == "error":
                data = event.get("data") or {}
                msg = data.get("message", "Pipeline error") if isinstance(data, dict) else str(data)
                status = data.get("status_code", 500) if isinstance(data, dict) else 500
                raise PipelineError(str(msg), status_code=int(status))
        if result is None:
            raise PipelineError("Pipeline produced no final event")
        return result

    # -- sub-flows -------------------------------------------------------

    async def _new_conversation(
        self,
        text: str,
        image_paths: Optional[List[str]],
    ) -> AsyncGenerator[PipelineEvent, None]:
        t_intent = time.time()
        intent = detect_intent(text)
        yield self._emit(
            "INTENT",
            "services.intent_router.detect_intent · 完成",
            _kv_detail(intent=intent.value, duration_ms=int((time.time() - t_intent) * 1000)),
        )

        if intent is Intent.KB_QUERY:
            async for event in self._handle_kb_query(text, image_paths, intent):
                yield event
        else:
            async for event in self._handle_new_orch(text, intent):
                yield event

    async def _handle_kb_query(
        self,
        text: str,
        image_paths: Optional[List[str]],
        intent: Intent,
    ) -> AsyncGenerator[PipelineEvent, None]:
        yield self._emit(
            "KB",
            "services.kb_query_enhanced.enhanced_kb_query · 开始",
            _kv_detail(
                target="internal",
                has_query_image=bool(image_paths),
                llm_enabled=bool(getattr(settings, "kb_query_llm_enabled", True)),
                max_subqueries=getattr(settings, "kb_query_max_subqueries", 4),
            ),
        )
        t_kb = time.time()
        try:
            result = await enhanced_kb_query(text, image_paths or None)
        except KBQueryError as exc:
            yield {"type": "error", "data": {"message": str(exc), "status_code": 500}}
            return

        final_markdown = str(result.get("final_markdown") or "")
        raw_markdown = str(result.get("raw_markdown") or "")
        sub_queries = result.get("sub_queries") or []
        used_llm = bool(result.get("used_llm"))
        image_urls = kb_image_paths_to_urls(list(result.get("image_paths") or []))

        yield self._emit(
            "KB",
            "services.kb_query_enhanced.enhanced_kb_query · 完成",
            _kv_detail(
                images=len(image_urls),
                used_llm=used_llm,
                subqueries=(len(sub_queries) if isinstance(sub_queries, list) else 0),
                duration_ms=int((time.time() - t_kb) * 1000),
            ),
        )

        conv_id = uuid.uuid4().hex
        create_conversation(conv_id, intent=intent.value, status="done")
        add_message(conv_id, role="user", payload_type="USER_QUERY", content=text)
        self._save_trace(conv_id)
        add_message(conv_id, role="assistant", payload_type="KB_ANSWER",
                    content=final_markdown or "[空结果]")

        content = {
            "markdown": final_markdown,
            "images": image_urls,
            "usedLLM": used_llm,
            "subQueries": sub_queries,
            "rawMarkdown": raw_markdown,
            "kbRuns": result.get("kb_runs") or [],
        }
        yield {"type": "final", "data": {
            "sessionId": None,
            "intent": intent.value,
            "payloadType": "KB_ANSWER",
            "content": content,
        }}

    async def _handle_new_orch(self, text: str, intent: Intent) -> AsyncGenerator[PipelineEvent, None]:
        yield self._emit("COLLECT", "services.orchestrator_controller.create_session · 创建会话")
        sess = orch.create_session(user_request=text)
        lh = self._llm_hint()
        yield self._emit(
            "COLLECT",
            "services.orchestrator_controller.get_open_questions · 开始",
            _kv_detail(
                target="internal",
                calls_hint="kb:subprocess run_all_sources.py, llm_collect",
                llm_configured=self._llm_configured(),
                llm_endpoint=(lh if "http:POST" in lh else ""),
                llm_model=(settings.llm_model if self._llm_configured() else ""),
            ),
        )
        t_collect = time.time()
        questions = await orch.get_open_questions(sess)
        yield self._emit(
            "COLLECT",
            "services.orchestrator_controller.get_open_questions · 完成",
            _kv_detail(questions=len(questions), duration_ms=int((time.time() - t_collect) * 1000)),
        )

        create_conversation(sess.session_id, intent=intent.value, status="active")
        add_message(sess.session_id, role="user", payload_type="USER_REQUEST", content=text)
        self._save_trace(sess.session_id)
        if questions:
            joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
            add_message(sess.session_id, role="assistant", payload_type="OPEN_QUESTIONS", content=joined)

        yield {"type": "final", "data": {
            "sessionId": sess.session_id,
            "intent": intent.value,
            "payloadType": "OPEN_QUESTIONS",
            "content": {"questions": questions},
        }}

    async def _handle_answer(self, sess: orch.OrchestratorSession, text: str) -> AsyncGenerator[PipelineEvent, None]:
        lh = self._llm_hint()
        ih = self._img_hint()
        yield self._emit(
            "BUILD_DRAFT",
            "services.orchestrator_controller.answer_questions · 开始",
            _kv_detail(
                target="internal",
                calls_hint="kb:subprocess run_all_sources.py, llm_build_draft_sections, image_gen(optional)",
                llm_configured=self._llm_configured(),
                llm_endpoint=(lh if "http:POST" in lh else ""),
                llm_model=(settings.llm_model if self._llm_configured() else ""),
                image_gen_enabled=bool(getattr(settings, "image_gen_enabled", True)),
                image_gen_endpoint=(ih if "http:POST" in ih else ""),
                image_gen_model=(getattr(settings, "image_gen_model", "") if (ih and "http:POST" in ih) else ""),
            ),
        )
        add_message(sess.session_id, role="user", payload_type="USER_ANSWER", content=text)
        t_build = time.time()
        sess, _ = await orch.answer_questions(sess.session_id, text)
        display_result = confirmer_get_display(sess.draft_struct)
        yield self._emit(
            "BUILD_DRAFT",
            "services.orchestrator_controller.answer_questions · 完成",
            _kv_detail(duration_ms=int((time.time() - t_build) * 1000)),
        )
        self._save_trace(sess.session_id)
        add_message(sess.session_id, role="assistant", payload_type="DRAFT", content=display_result.display_content)

        yield {"type": "final", "data": {
            "sessionId": sess.session_id,
            "intent": Intent.ORCH_FLOW.value,
            "payloadType": "DRAFT",
            "content": {
                "markdown": display_result.display_content,
                "prompt_to_user": display_result.prompt_to_user,
            },
        }}

    async def _handle_confirm(self, sess: orch.OrchestratorSession, text: str) -> AsyncGenerator[PipelineEvent, None]:
        lh = self._llm_hint()
        yield self._emit(
            "CONFIRM",
            "services.confirmer_service.parse_feedback · 开始",
            _kv_detail(
                target="internal",
                calls_hint="llm_confirmer_parse(optional) or keyword_fallback",
                llm_configured=self._llm_configured(),
                llm_endpoint=(lh if "http:POST" in lh else ""),
                llm_model=(settings.llm_model if self._llm_configured() else ""),
            ),
        )
        add_message(sess.session_id, role="user", payload_type="USER_FEEDBACK", content=text)
        t_confirm = time.time()
        parse_result = await confirmer_parse_feedback(sess.draft_struct, text)
        yield self._emit(
            "CONFIRM",
            "services.confirmer_service.parse_feedback · 完成",
            _kv_detail(status=parse_result.status, duration_ms=int((time.time() - t_confirm) * 1000)),
        )

        if parse_result.status == "needs_clarification" and parse_result.clarification_question:
            self._save_trace(sess.session_id)
            add_message(sess.session_id, role="assistant", payload_type="OPEN_QUESTIONS",
                        content=parse_result.clarification_question)
            yield {"type": "final", "data": {
                "sessionId": sess.session_id,
                "intent": Intent.ORCH_FLOW.value,
                "payloadType": "OPEN_QUESTIONS",
                "content": {"questions": [parse_result.clarification_question]},
            }}
            return

        if parse_result.status in ("request_redo_partial", "request_redo_full"):
            self._save_trace(sess.session_id)
            msg = "已记录您的重做要求；当前版本将先进入完整性检查并生成文档。后续版本将支持按块/整体重做。若需继续，请回复「确认」。"
            add_message(sess.session_id, role="assistant", payload_type="INFO", content=msg)
            yield {"type": "final", "data": {
                "sessionId": sess.session_id,
                "intent": Intent.ORCH_FLOW.value,
                "payloadType": "INFO",
                "content": {"message": "已记录重做要求，请回复「确认」继续。"},
            }}
            return

        if parse_result.status == "revised" and parse_result.suggested_draft_updates:
            br = sess.draft_struct.setdefault("business_requirement", {})
            for key, val in (parse_result.suggested_draft_updates.get("business_requirement") or {}).items():
                if isinstance(val, str):
                    br[key] = val
        sess.state = "DEFENDING"
        orch.persist_session(sess)

        async for event in self._defend_and_maybe_finalize(sess):
            yield event

    async def _handle_defend(self, sess: orch.OrchestratorSession, text: str) -> AsyncGenerator[PipelineEvent, None]:
        yield self._emit(
            "DEFEND",
            "services.orchestrator_controller.apply_defend_answers · 应用补充说明",
            _kv_detail(target="internal"),
        )
        add_message(sess.session_id, role="user", payload_type="USER_DEFEND", content=text)
        sess = orch.apply_defend_answers(sess.session_id, text)
        async for event in self._defend_and_maybe_finalize(sess):
            yield event

    async def _defend_and_maybe_finalize(self, sess: orch.OrchestratorSession) -> AsyncGenerator[PipelineEvent, None]:
        yield self._emit("DEFEND", "services.defender_service.check_draft · 开始")
        t_defend = time.time()
        result = check_draft(sess.draft_struct)
        yield self._emit(
            "DEFEND",
            "services.defender_service.check_draft · 完成",
            _kv_detail(is_complete=result.is_complete, duration_ms=int((time.time() - t_defend) * 1000)),
        )

        if not result.is_complete and result.questions:
            sess.last_defend_questions = result.questions
            orch.persist_session(sess)
            joined = "\n".join(f"{i+1}. {q}" for i, q in enumerate(result.questions))
            self._save_trace(sess.session_id)
            add_message(sess.session_id, role="assistant", payload_type="OPEN_QUESTIONS", content=joined)
            yield self._emit(
                "DEFEND",
                "services.defender_service.check_draft · 待补充信息",
                _kv_detail(questions=len(result.questions)),
                level="warn",
            )
            yield {"type": "final", "data": {
                "sessionId": sess.session_id,
                "intent": Intent.ORCH_FLOW.value,
                "payloadType": "OPEN_QUESTIONS",
                "content": {"questions": result.questions},
            }}
            return

        yield self._emit("EDITOR", "services.editor_service.render_final · 开始")
        t_edit = time.time()
        final_md = render_final(sess.draft_struct)
        yield self._emit(
            "EDITOR",
            "services.editor_service.render_final · 完成",
            _kv_detail(duration_ms=int((time.time() - t_edit) * 1000)),
        )
        self._save_trace(sess.session_id)
        add_message(sess.session_id, role="assistant", payload_type="FINAL_DOC", content=final_md)
        update_conversation_status(sess.session_id, status="done")
        sess.state = "DONE"
        orch.persist_session(sess)

        yield {"type": "final", "data": {
            "sessionId": sess.session_id,
            "intent": Intent.ORCH_FLOW.value,
            "payloadType": "FINAL_DOC",
            "content": {"markdown": final_md},
        }}
