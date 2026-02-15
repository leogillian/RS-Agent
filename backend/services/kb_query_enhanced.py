"""Enhanced KB_QUERY flow (方案 B): LLM expands queries -> multi KB retrieval -> LLM synthesis.

This module is designed to be reusable later by ORCH_FLOW (shared retrieve function).
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional, Tuple

from backend.config import settings
from backend.services.trading_kb_service import KBQueryError, query_kb
from backend.services.llm_service import llm_expand_kb_queries, llm_kb_synthesize

logger = logging.getLogger(__name__)


def _normalize_query(q: str) -> str:
    return " ".join((q or "").strip().split())


def _dedup_keep_order(items: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for it in items:
        s = (it or "").strip()
        if not s:
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _merge_kb_markdown(results: List[Tuple[str, str]]) -> str:
    """Merge per-query KB markdown into one markdown, with lightweight separators."""
    if len(results) == 1:
        # 保持与旧版 KB_QUERY 一致：单次检索时不额外包一层标题
        _q, _md = results[0]
        return (_md or "").strip()
    parts: List[str] = []
    for idx, (q, md) in enumerate(results):
        md = (md or "").strip()
        if not md:
            continue
        q = (q or "").strip()
        header = f"### 检索子问题 {idx + 1}\n\n- query: {q}\n"
        parts.append(header + "\n" + md)
    return "\n\n---\n\n".join(parts).strip()


async def enhanced_kb_query(
    user_query: str,
    image_paths: Optional[List[str]] = None,
) -> Dict[str, object]:
    """Run enhanced KB query and return a structured result.

    Returns:
      {
        "final_markdown": str,          # final answer (LLM synthesized when enabled)
        "raw_markdown": str,            # merged KB results (for debugging / fallback)
        "sub_queries": List[str],       # queries used to retrieve KB
        "used_llm": bool,               # whether synthesis succeeded
        "kb_runs": List[dict],          # per sub-query stats
        "image_paths": List[str],       # merged image paths from KB
      }
    """
    q0 = _normalize_query(user_query)
    if not q0:
        return {
            "final_markdown": "",
            "raw_markdown": "",
            "sub_queries": [],
            "used_llm": False,
            "kb_runs": [],
            "image_paths": [],
        }

    # 1) Expand multi queries via LLM (if configured and enabled)
    sub_queries: List[str] = [q0]
    kb_runs: List[dict] = []
    t_expand = time.time()
    try:
        if getattr(settings, "kb_query_llm_enabled", True) and settings.llm_api_key and settings.llm_base_url:
            expanded = await llm_expand_kb_queries(q0, max_queries=getattr(settings, "kb_query_max_subqueries", 4))
            expanded = [_normalize_query(x) for x in expanded]
            sub_queries = _dedup_keep_order([q0, *expanded])[: max(1, int(getattr(settings, "kb_query_max_subqueries", 4)))]
    except Exception as e:
        logger.warning("KB_QUERY expand queries failed, fallback to single query: %s", e)
    kb_runs.append(
        {
            "stage": "expand_queries",
            "duration_ms": int((time.time() - t_expand) * 1000),
            "queries": list(sub_queries),
        }
    )

    # 2) Multi retrieval
    seen_md: set[str] = set()
    per_query_results: List[Tuple[str, str]] = []
    merged_images: List[str] = []
    had_success = False
    for i, sq in enumerate(sub_queries):
        t_kb = time.time()
        try:
            md, imgs = await query_kb(sq, image_paths if (i == 0 and image_paths) else None)
            had_success = True
        except KBQueryError as exc:
            kb_runs.append(
                {
                    "stage": "kb_retrieve",
                    "query": sq,
                    "ok": False,
                    "error": str(exc),
                    "duration_ms": int((time.time() - t_kb) * 1000),
                }
            )
            continue

        md_norm = (md or "").strip()
        if md_norm and md_norm not in seen_md:
            seen_md.add(md_norm)
            per_query_results.append((sq, md_norm))

        if imgs:
            merged_images.extend([p for p in imgs if isinstance(p, str) and p.strip()])

        kb_runs.append(
            {
                "stage": "kb_retrieve",
                "query": sq,
                "ok": True,
                "chars": len(md_norm),
                "images": len(imgs or []),
                "duration_ms": int((time.time() - t_kb) * 1000),
            }
        )

    if not had_success:
        # Keep behavior: surface KB errors to caller by raising (so router returns 500).
        raise KBQueryError("KB 查询失败：所有检索子问题均未成功返回结果。")

    merged_images = _dedup_keep_order(merged_images)
    raw_markdown = _merge_kb_markdown(per_query_results)
    if not raw_markdown:
        raw_markdown = "[空结果]"

    # 3) LLM synthesis (strictly based on raw_markdown). If fails, fallback to raw_markdown.
    used_llm = False
    final_markdown = raw_markdown
    t_syn = time.time()
    try:
        if getattr(settings, "kb_query_llm_enabled", True) and settings.llm_api_key and settings.llm_base_url:
            limit = int(getattr(settings, "kb_query_max_merged_chars", 12000))
            kb_for_llm = raw_markdown if len(raw_markdown) <= limit else (raw_markdown[:limit] + "\n\n（已截断：KB 合并结果过长）")
            final_markdown = (await llm_kb_synthesize(q0, kb_for_llm)).strip() or raw_markdown
            used_llm = True
    except Exception as e:
        logger.warning("KB_QUERY synthesis failed, fallback to raw markdown: %s", e)
        final_markdown = raw_markdown
        used_llm = False
    kb_runs.append(
        {
            "stage": "synthesize",
            "used_llm": used_llm,
            "duration_ms": int((time.time() - t_syn) * 1000),
        }
    )

    return {
        "final_markdown": final_markdown,
        "raw_markdown": raw_markdown,
        "sub_queries": list(sub_queries),
        "used_llm": used_llm,
        "kb_runs": kb_runs,
        "image_paths": merged_images,
    }

