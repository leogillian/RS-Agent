"""LLM 集成服务：使用 OpenAI 兼容 API（Qwen/DashScope 等）作为 Orchestrator 的大脑。

通过 HTTP API 直接调用，不依赖 SDK。

P1-3: prompt 全部抽取到 backend/prompts/*.yaml，代码只做加载与变量替换。
P1-5: HTTP 调用带 tenacity 指数退避重试。

封装五个高层能力：
- llm_expand_kb_queries: KB_QUERY 方案 B，扩展多条检索 query
- llm_kb_synthesize:     KB_QUERY，基于 KB 检索结果综合回答
- llm_collect:           COLLECT 阶段，产生结构化需求 + open_questions
- llm_build_draft_sections: BUILD_DRAFT 阶段，生成 system_current / system_changes 段落
- llm_confirmer_parse:   Confirmer 阶段，解析用户反馈为 5 种 status
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from backend.config import settings
from backend.prompts import load_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry-decorated internal HTTP caller (P1-5)
# ---------------------------------------------------------------------------


def _build_retry_decorator():
    """Return a tenacity retry decorator based on current settings."""
    return retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TransportError, TimeoutError)),
        stop=stop_after_attempt(max(1, settings.llm_max_retries)),
        wait=wait_exponential(
            min=max(0.1, settings.llm_retry_min_wait),
            max=max(1, settings.llm_retry_max_wait),
        ),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


async def _http_post(url: str, headers: dict, payload: dict) -> dict:
    """Execute the HTTP POST with tenacity retry (exponential backoff)."""
    _retry = _build_retry_decorator()

    @_retry
    async def _do():
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()

    return await _do()


async def _chat(messages: List[Dict[str, Any]], temperature: float = 0.2, max_tokens: int = 2048) -> str:
    """通过 httpx.AsyncClient 调用 Chat Completions，返回单条 content。

    P1-5: 失败时自动重试（指数退避），由 RS_AGENT_LLM_MAX_RETRIES 等配置控制。
    """
    if not settings.llm_api_key:
        raise RuntimeError(
            "LLM API Key 未配置。请设置环境变量 LLM_API_KEY、DASHSCOPE_API_KEY 或 OPENAI_API_KEY。"
        )
    if not settings.llm_base_url:
        raise RuntimeError(
            "LLM Base URL 未配置。请设置环境变量 RS_AGENT_LLM_BASE_URL。"
        )
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = await _http_post(url, headers, payload)
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    if content is None:
        raise RuntimeError(f"LLM API 返回格式异常: {data}")
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content)


# ---------------------------------------------------------------------------
# Public LLM functions (P1-3: prompts loaded from YAML templates)
# ---------------------------------------------------------------------------


async def llm_classify_intent(user_text: str) -> Optional[str]:
    """P1-1: LLM 意图分类 — 判断 KB_QUERY / ORCH_FLOW。

    Returns:
        "KB_QUERY" 或 "ORCH_FLOW"，若无法判断返回 None。
    """
    text = (user_text or "").strip()
    if not text:
        return None
    tpl = load_prompt("intent_classify")
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": tpl.system()},
        {"role": "user", "content": tpl.user(user_text=text)},
    ]
    raw = await _chat(messages, temperature=0.0, max_tokens=128)
    try:
        data = json.loads(raw)
        intent = data.get("intent", "").strip().upper()
        if intent in ("KB_QUERY", "ORCH_FLOW"):
            return intent
    except Exception:
        # 尝试从纯文本中提取
        if "KB_QUERY" in raw.upper():
            return "KB_QUERY"
        if "ORCH_FLOW" in raw.upper():
            return "ORCH_FLOW"
    return None


async def llm_expand_kb_queries(user_query: str, max_queries: int = 4) -> List[str]:
    """KB_QUERY 方案 B：将用户问题扩展为多条检索 query（不输出答案）。"""
    uq = (user_query or "").strip()
    if not uq:
        return []
    max_q = max(1, int(max_queries or 1))
    tpl = load_prompt("expand_kb_queries")
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": tpl.system()},
        {"role": "user", "content": tpl.user(user_query=uq, max_queries=max_q)},
    ]
    raw = await _chat(messages, temperature=0.2, max_tokens=512)
    try:
        data = json.loads(raw)
        qs = data.get("queries")
        if isinstance(qs, list):
            out: List[str] = []
            for x in qs:
                if isinstance(x, str):
                    s = x.strip()
                    if s:
                        out.append(s)
            return out[:max_q]
    except Exception:
        pass
    # 兜底：尽力从纯文本中按行解析
    lines = [ln.strip("- ").strip() for ln in str(raw).splitlines() if ln.strip()]
    return [ln for ln in lines if ln][:max_q]


async def llm_kb_synthesize(user_query: str, kb_markdown_merged: str) -> str:
    """KB_QUERY：基于（合并后的）KB 检索结果进行综合回答，严格禁止编造。输出 Markdown。"""
    uq = (user_query or "").strip()
    kb = (kb_markdown_merged or "").strip()
    tpl = load_prompt("kb_synthesize")
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": tpl.system()},
        {"role": "user", "content": tpl.user(user_query=uq, kb_markdown=kb)},
    ]
    return await _chat(messages, temperature=0.2, max_tokens=2048)


async def llm_collect(user_request: str, kb_markdown: str) -> Dict[str, Any]:
    """COLLECT 阶段：基于用户原话 + KB 文本，生成结构化需求与 open_questions。"""
    tpl = load_prompt("collect")
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": tpl.system()},
        {"role": "user", "content": tpl.user(user_request=user_request, kb_markdown=kb_markdown)},
    ]
    raw = await _chat(messages)
    data = json.loads(raw)
    demand_source = data.get("demand_source") or user_request
    product_statement = data.get("product_statement") or ""
    open_questions = data.get("open_questions") or ["请确认或补充上述需求，回复后继续。"]
    return {
        "demand_source": demand_source,
        "product_statement": product_statement,
        "open_questions": open_questions,
    }


def _image_path_to_data_url(path: str) -> Optional[str]:
    """将本地图片文件读为 base64 data URL，供多模态 API 使用。"""
    p = Path(path)
    if not p.is_file():
        return None
    raw = p.read_bytes()
    suffix = p.suffix.lower()
    mime = "image/png" if suffix in (".png",) else "image/jpeg"
    if suffix in (".jpeg", ".jpg"):
        mime = "image/jpeg"
    b64 = base64.standard_b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


async def llm_build_draft_sections(
    user_request: str,
    user_answer: str,
    requirement_structured: Dict[str, Any],
    kb_markdown: str,
    candidate_image_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """BUILD_DRAFT 阶段：生成 business_requirement、system_current、system_changes；若有候选图则由 LLM 根据需求选图。"""
    tpl = load_prompt("build_draft")
    req_json = json.dumps(requirement_structured, ensure_ascii=False)
    text_content = tpl.user(
        user_request=user_request,
        user_answer=user_answer,
        requirement_structured_json=req_json,
        kb_markdown=kb_markdown,
    )
    if candidate_image_paths:
        text_content += "\n" + tpl.get("image_instruction")
    text_content += "\n" + tpl.get("output_schema")

    # 构建 user message：无图时纯文本，有图时多模态（先文字，再按顺序每张图）
    user_content: Any = text_content.strip()
    if candidate_image_paths:
        content_parts: List[Dict[str, Any]] = [{"type": "text", "text": text_content.strip()}]
        for i, path in enumerate(candidate_image_paths):
            data_url = _image_path_to_data_url(path)
            if data_url:
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_url},
                })
        user_content = content_parts
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": tpl.system()},
        {"role": "user", "content": user_content},
    ]
    raw = await _chat(messages)
    sections = json.loads(raw)
    return sections


async def llm_confirmer_parse(draft_output: Dict[str, Any], user_message: str) -> Dict[str, Any]:
    """Confirmer 阶段：解析用户对草稿的反馈，返回 5 种 status 与建议修改。"""
    tpl = load_prompt("confirmer_parse")
    draft_json = json.dumps(draft_output, ensure_ascii=False)
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": tpl.system()},
        {"role": "user", "content": tpl.user(draft_json=draft_json, user_message=user_message)},
    ]
    raw = await _chat(messages)
    return json.loads(raw)
