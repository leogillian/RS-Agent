"""Simplified orchestrator controller, gradually aligning with demand_analysis_doc_v1.

当前版本实现：
- COLLECT（调用 TradingKBService 做一次知识库检索）；
- 一轮 open_questions；
- 用户回答后，生成与 demand_analysis_doc_v1 结构对齐的简化草稿。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from backend.config import settings
from backend.services.trading_kb_service import query_kb
from backend.services.llm_service import llm_build_draft_sections, llm_collect
from backend.services.kb_artifacts import (
    extract_best_images,
    extract_image_refs,
)

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorSession:
    """In-memory session for orchestrator flow."""

    session_id: str
    user_request: str
    state: str  # "COLLECT" | "WAITING_ANSWERS" | "DRAFT_READY" | "CONFIRMING" | "DEFENDING" | "DONE"
    open_questions: List[str] = field(default_factory=list)
    user_answers: List[str] = field(default_factory=list)
    knowledge_markdown: str = ""
    kb_image_urls: List[str] = field(default_factory=list)  # 知识库导出的图片，前端 URL 如 /api/kb-images/检索图_1.png
    draft_struct: dict = field(default_factory=dict)
    last_defend_questions: List[str] = field(default_factory=list)  # DEFEND 轮追问的问题，用于写入 clarification_log
    requirement_structured: dict = field(default_factory=dict)  # P4: 结构化需求，COLLECT/用户回复后更新


_SESSIONS: Dict[str, OrchestratorSession] = {}


def create_session(user_request: str) -> OrchestratorSession:
    """Create a new orchestrator session in COLLECT state."""
    session_id = str(uuid.uuid4())
    sess = OrchestratorSession(
        session_id=session_id,
        user_request=user_request,
        state="COLLECT",
    )
    _SESSIONS[session_id] = sess
    return sess


def get_session(session_id: str) -> Optional[OrchestratorSession]:
    return _SESSIONS.get(session_id)


def _extract_kb_mentions(kb_text: str, max_mentions: int = 5) -> List[str]:
    """从 KB 返回的 markdown 中提取与需求相关的关键提及（页面名、流程、文档主题等），用于生成针对性追问。"""
    if not (kb_text or "").strip():
        return []
    seen: set[str] = set()
    out: List[str] = []
    keywords = ("页面", "流程", "调仓", "定投", "确认", "方案", "明细", "入口", "弹框", "浮层", "比例", "金额", "规则", "追加", "申购", "赎回")

    def _is_meta_line(line: str) -> bool:
        s = line.strip()
        return s.startswith("---") or s.startswith("===") or "source=" in s or "distance=" in s or not s

    # 1) 提取【】「」内的短语
    for part in re.findall(r"[「『【][^」』】]{2,24}[」』】]", kb_text):
        p = part.strip()
        if p and p not in seen and 2 <= len(p) <= 28:
            seen.add(p)
            out.append(p)
            if len(out) >= max_mentions:
                return out
    # 2) 取非元数据、含关键词的短行
    for line in kb_text.splitlines():
        line = line.strip()
        if _is_meta_line(line) or len(line) < 4 or len(line) > 55:
            continue
        if any(k in line for k in keywords) and line not in seen:
            seen.add(line)
            out.append(line[:48] + "…" if len(line) > 48 else line)
            if len(out) >= max_mentions:
                return out
    # 3) 从正文中截取含关键概念的片段
    if len(out) < max_mentions:
        for m in re.finditer(r"[^\n]{0,25}(?:确认调仓|调仓明细|定投|追加资金|调仓方式|前端|页面|流程)[^\n]{0,15}", kb_text):
            p = m.group(0).strip()
            if p and p not in seen and 4 <= len(p) <= 45:
                seen.add(p)
                out.append(p)
                if len(out) >= max_mentions:
                    break
    return out


def _derive_open_questions(user_request: str, knowledge_markdown: str) -> List[str]:
    """根据 KB 返回结果做分析，生成与检索内容相关的 open_questions，而非固定文案。"""
    q = (user_request or "").strip()
    kb = (knowledge_markdown or "").strip()
    mentions = _extract_kb_mentions(kb)
    mention_str = "、".join(mentions[:4]) if mentions else ""

    # 若 KB 返回中包含可识别的结构化片段（如后续脚本输出 JSON 块），可在此解析并优先使用
    # 当前 run_all_sources 仅输出 markdown，此处基于提取的 mentions 与需求关键词生成追问

    if "系统改动点" in q or "改动点" in q:
        if mention_str:
            return [f"根据知识库检索结果，与您需求相关的内容涉及：{mention_str}。请确认该需求涉及的具体页面/模块与是否仅前端调整、不改后端逻辑（或补充说明），回复后继续。"]
        if "定投" in q or "定投" in kb:
            return ["根据检索结果，当前与定投相关描述较多。请确认该需求涉及的具体场景（如定投失败补扣、执行时间等）与约束，回复后继续。"]
        return ["请确认该需求涉及的具体页面/模块与约束（或补充说明），回复后继续。"]
    if "定投" in q or "调仓" in q or "三分法" in q or "投顾" in q:
        if not kb or len(kb) < 100:
            return ["知识库命中较少，请用 1～2 句话补充该需求的业务背景或目标，回复后继续。"]
        if mention_str:
            return [f"根据检索结果，相关内容涉及：{mention_str}。请用 1～2 句话补充该需求的业务背景或目标，回复后继续。"]
        return ["请用 1～2 句话补充该需求的业务背景或目标，回复后继续。"]
    if mention_str:
        return [f"根据知识库检索到：{mention_str}。请确认或补充上述需求，回复后继续。"]
    return ["请确认或补充上述需求，回复后继续。"]


def _ensure_collect(sess: OrchestratorSession) -> None:
    """Run a minimal COLLECT step: 调一次 KB，生成 open_questions，并落 requirement_structured（P0/P4）。

    优先调用 Qwen（llm_collect）生成结构化需求与 open_questions；若 LLM 不可用或报错，则回退到规则版。
    """
    if sess.knowledge_markdown:
        return
    kb_text, exported_paths = query_kb(sess.user_request, [])
    sess.knowledge_markdown = kb_text

    # 更可靠的候选图：从 KB markdown 的 path/page 引用中抽取 PDF 页最大图（更像流程图），
    # 若提取失败则回退到 KB 脚本已导出的图片路径
    refs = extract_image_refs(kb_text)
    best_paths = extract_best_images(
        refs,
        getattr(settings, "images_output_dir_abs", None) or settings.images_output_dir,
        max_images=8,
    )
    image_paths = best_paths or (exported_paths or [])
    sess.kb_image_urls = [f"/api/kb-images/{os.path.basename(p)}" for p in image_paths]
    try:
        collect = llm_collect(sess.user_request, kb_text)
        sess.requirement_structured = {
            "demand_source": collect.get("demand_source") or sess.user_request,
            "product_statement": collect.get("product_statement") or "",
            "open_questions": list(collect.get("open_questions") or []),
        }
        sess.open_questions = list(sess.requirement_structured.get("open_questions") or [])
    except Exception as e:
        # 回退：仍使用规则版 open_questions 与最简 requirement_structured
        logger.warning("LLM llm_collect 调用失败，已回退到规则版: %s", e)
        sess.open_questions = _derive_open_questions(sess.user_request, kb_text)
        sess.requirement_structured = {
            "demand_source": sess.user_request,
            "product_statement": "",
            "open_questions": list(sess.open_questions),
        }
    sess.state = "WAITING_ANSWERS"


def get_open_questions(sess: OrchestratorSession) -> List[str]:
    """Ensure COLLECT/RETRIEVE has run and return open questions."""
    _ensure_collect(sess)
    return list(sess.open_questions)


def _derive_system_current_from_kb(kb_text: str) -> tuple[str, str, str]:
    """从 KB 文本推导 system_current 的 frontend/backend/notification 描述。"""
    if not (kb_text or "").strip():
        return (
            "（知识库暂无命中，待补充前端现状）",
            "（知识库暂无命中，待补充后端现状）",
            "（知识库暂无命中，待补充通知现状）",
        )
    lines = [ln.strip() for ln in kb_text.splitlines() if ln.strip()]
    frontend_parts: List[str] = []
    backend_parts: List[str] = []
    notification_parts: List[str] = []
    for ln in lines:
        if any(k in ln for k in ("页面", "前端", "展示", "交互", "浮层", "弹框", "调仓明细", "确认调仓", "持仓", "占比")):
            frontend_parts.append(ln)
        elif any(k in ln for k in ("接口", "拆单", "流程", "订单", "垫资", "申购", "赎回", "后端", "中台")):
            backend_parts.append(ln)
        elif any(k in ln for k in ("通知", "消息", "模板", "触发", "到账")):
            notification_parts.append(ln)
    fe = " ".join(frontend_parts[:8]) if frontend_parts else "（知识库中与前端/页面相关描述较少，可结合上方业务规则补充）"
    be = " ".join(backend_parts[:8]) if backend_parts else "（知识库中与后端/流程相关描述较少，可结合上方业务规则补充）"
    nt = " ".join(notification_parts[:5]) if notification_parts else "当前知识库片段未单独描述通知，沿用现有逻辑。"
    if len(fe) > 500:
        fe = fe[:497] + "..."
    if len(be) > 500:
        be = be[:497] + "..."
    if len(nt) > 300:
        nt = nt[:297] + "..."
    return (fe or "（待补充）", be or "（待补充）", nt or "（待补充）")


def _derive_system_changes_from_user(user_request: str, answer_text: str) -> tuple[str, str, str, str]:
    """从用户需求与回复推导 system_changes 各字段。"""
    combined = f"{user_request or ''} {answer_text or ''}".strip()
    req_lower = (user_request or "").lower()
    ans_lower = (answer_text or "").lower()

    # 改动总览
    overview = f"根据需求「{user_request}」与用户补充：{answer_text}。" if combined else "（待基于需求与知识库进一步梳理改动总览）"
    if len(overview) > 400:
        overview = overview[:397] + "..."

    # 前端改动：需求/回复中常涉及页面、展示、隐藏、去掉等
    if any(k in combined for k in ("前端", "页面", "展示", "隐藏", "去掉", "文案", "弹框", "调仓明细", "确认调仓")):
        frontend_desc = f"根据需求与用户补充：{answer_text or user_request}。涉及前端展示或交互调整。"
    else:
        frontend_desc = "（若仅后端或配置改动则可为无；否则请结合需求补充）"
    if len(frontend_desc) > 350:
        frontend_desc = frontend_desc[:347] + "..."

    # 后端改动：用户明确说不改后端则填无
    if any(k in combined for k in ("不改后端", "不改后端逻辑", "无后端", "后端无", "仅前端")):
        backend_desc = "无。不修改后端接口与逻辑。"
    elif any(k in combined for k in ("后端", "接口", "拆单", "流程", "逻辑")):
        backend_desc = f"根据需求与用户补充待进一步确认：{answer_text or user_request}。"
    else:
        backend_desc = "（若需求仅涉及前端展示则填无）"
    if len(backend_desc) > 350:
        backend_desc = backend_desc[:347] + "..."

    # 通知改动
    if any(k in combined for k in ("通知", "消息", "推送")):
        notification_desc = f"根据需求与用户补充：{answer_text or user_request}。"
    else:
        notification_desc = "无。"
    if len(notification_desc) > 200:
        notification_desc = notification_desc[:197] + "..."
    return (overview, frontend_desc, backend_desc, notification_desc)


def answer_questions(session_id: str, answer_text: str) -> Tuple[OrchestratorSession, str]:
    """Consume user's answer and build a draft aligned with demand_analysis_doc_v1."""
    sess = _SESSIONS.get(session_id)
    if not sess:
        raise KeyError(f"session {session_id} not found")

    _ensure_collect(sess)
    sess.user_answers.append(answer_text)
    background = sess.user_answers[-1] if sess.user_answers else sess.user_request

    # P4：用户回复后再调 KB（再 COLLECT / RETRIEVE），用返回更新 knowledge 与图片列表
    requery = f"{sess.user_request} {answer_text}".strip()
    extra_kb, extra_paths = query_kb(requery, [])
    if extra_kb:
        sess.knowledge_markdown = (sess.knowledge_markdown or "") + "\n\n--- 根据用户补充检索 ---\n\n" + extra_kb
    if extra_paths:
        seen_basenames = {os.path.basename(u) for u in sess.kb_image_urls}
        for p in extra_paths:
            url = f"/api/kb-images/{os.path.basename(p)}"
            if os.path.basename(p) not in seen_basenames:
                seen_basenames.add(os.path.basename(p))
                sess.kb_image_urls.append(url)

    # 额外用 KB 返回的 path/page 再抽一版“更像流程图”的候选图，避免 PDF 页第一张图是 logo/装饰导致选图为空
    extra_refs = extract_image_refs(extra_kb)
    if extra_refs:
        images_dir = getattr(settings, "images_output_dir_abs", None) or settings.images_output_dir
        best_extra = extract_best_images(extra_refs, images_dir, max_images=8)
        if best_extra:
            seen_basenames = {os.path.basename(u) for u in sess.kb_image_urls}
            for p in best_extra:
                bn = os.path.basename(p)
                if bn in seen_basenames:
                    continue
                seen_basenames.add(bn)
                sess.kb_image_urls.append(f"/api/kb-images/{bn}")
    sess.requirement_structured = {
        "demand_source": sess.user_request,
        "product_statement": background,
        "open_questions": list(sess.open_questions),
    }

    # 待澄清项：COLLECT 轮的问题与用户答案
    clarification_items = [
        {"question": "\n".join(sess.open_questions), "answer": answer_text, "source": "collect"}
    ]

    kb_text = sess.knowledge_markdown or ""
    # 候选图片本地路径（供 LLM 多模态选图）：根据 kb_image_urls 的 basename 拼出绝对路径，仅保留存在的文件；顺序与 url_by_candidate_index 一致
    images_dir = getattr(settings, "images_output_dir_abs", None) or settings.images_output_dir
    images_dir = Path(images_dir) if not isinstance(images_dir, Path) else images_dir
    candidate_image_paths: List[str] = []
    url_by_candidate_index: List[str] = []
    for url in sess.kb_image_urls:
        path = images_dir / os.path.basename(url)
        if path.exists():
            candidate_image_paths.append(str(path))
            url_by_candidate_index.append(url)

    # 优先使用 Qwen 生成 business_requirement（产品化表述）+ system_current + system_changes；若有候选图则由 LLM 根据需求选图
    draft_system_current: Dict[str, any]
    draft_system_changes: Dict[str, any]
    draft_demand_source: str = sess.user_request
    draft_product_statement: str = background
    try:
        sections = llm_build_draft_sections(
            user_request=sess.user_request,
            user_answer=answer_text,
            requirement_structured=sess.requirement_structured,
            kb_markdown=kb_text,
            candidate_image_paths=candidate_image_paths if candidate_image_paths else None,
        )
        draft_system_current = sections.get("system_current") or {}
        draft_system_changes = sections.get("system_changes") or {}
        br_llm = sections.get("business_requirement") or {}
        if isinstance(br_llm, dict):
            if (ps := (br_llm.get("product_statement") or "").strip()):
                draft_product_statement = ps
                sess.requirement_structured["product_statement"] = ps
            if (ds := (br_llm.get("demand_source") or "").strip()):
                draft_demand_source = ds
                sess.requirement_structured["demand_source"] = ds
        # 由 LLM 返回的 selected_image_indices 筛选要展示的图片（序号对应 candidate 顺序）；若无该字段或非法则展示全部
        selected_indices = draft_system_current.get("selected_image_indices")
        if isinstance(selected_indices, list) and url_by_candidate_index:
            selected_urls = [
                url_by_candidate_index[i]
                for i in selected_indices
                if isinstance(i, int) and 0 <= i < len(url_by_candidate_index)
            ]
        else:
            selected_urls = list(sess.kb_image_urls)
        draft_system_current["image_urls"] = selected_urls
        draft_system_current.pop("selected_image_indices", None)
    except Exception as e:
        logger.warning("LLM llm_build_draft_sections 调用失败，已回退到规则版: %s", e)
        fe_cur, be_cur, nt_cur = _derive_system_current_from_kb(kb_text)
        ch_overview, ch_fe, ch_be, ch_nt = _derive_system_changes_from_user(sess.user_request, answer_text)
        draft_system_current = {
            "business_rules": kb_text or "（待从知识库补充当前业务规则）",
            "frontend_current": {"description": fe_cur},
            "backend_current": {"description": be_cur},
            "notification_current": nt_cur,
            "image_urls": list(sess.kb_image_urls),
        }
        draft_system_changes = {
            "change_overview": ch_overview,
            "frontend_changes": {"description": ch_fe},
            "backend_changes": {
                "overview": ch_be,
                "steps_text": "",
                "flow_mermaid": "",
            },
            "notification_changes": {
                "description": ch_nt,
                "table_markdown": "",
            },
        }

    draft = {
        "template_name": "demand_analysis_doc_v1",
        "business_requirement": {
            "demand_source": draft_demand_source,
            "product_statement": draft_product_statement,
            "open_questions": sess.open_questions,
            "clarification_log": {"items": clarification_items},
        },
        "system_current": draft_system_current,
        "system_changes": draft_system_changes,
    }

    sess.draft_struct = draft
    # 生成草稿后进入 CONFIRMING 流程，由调用方决定是直接确认还是带修改意见
    sess.state = "DRAFT_READY"

    # 根据结构生成 Markdown 草稿（后续可交给 EditorService 做更丰富排版）
    kb_excerpt = sess.knowledge_markdown[:800] + ("..." if len(sess.knowledge_markdown) > 800 else "")
    draft_md = f"""# 需求分析文档草稿（对齐 demand_analysis_doc_v1）

## 一、业务需求

- 需求来源：{draft['business_requirement']['demand_source']}
- 产品化表述：{draft['business_requirement']['product_statement']}

---

## 二、系统现状（初稿）

### 业务规则与逻辑

{kb_excerpt or "（知识库暂无命中，待补充）"}

### 前端现状（待补充）
- {draft['system_current']['frontend_current']['description']}

### 后端现状（待补充）
- {draft['system_current']['backend_current']['description']}

### 通知/消息现状（待补充）
- {draft['system_current']['notification_current'] if isinstance(draft['system_current']['notification_current'], str) else (draft['system_current']['notification_current'].get('description') or '')}

---

## 三、系统改动点（占位）

- 改动总览：{draft['system_changes']['change_overview']}
- 前端改动点：{draft['system_changes']['frontend_changes']['description']}
- 后端改动点：{draft['system_changes']['backend_changes'].get('description_display') or draft['system_changes']['backend_changes'].get('description') or ''}
- 通知改动点：{draft['system_changes']['notification_changes'] if isinstance(draft['system_changes']['notification_changes'], str) else (draft['system_changes']['notification_changes'].get('description') or '')}
"""
    image_urls = draft["system_current"].get("image_urls") or []
    if image_urls:
        img_block = "\n\n### 附图（来自知识库）\n" + "\n".join(f"![检索图]({url})" for url in image_urls)
        # 二、系统现状末尾插入附图
        draft_md = draft_md.replace("\n---\n\n## 三、系统改动点", img_block + "\n\n---\n\n## 三、系统改动点")
        # 三、系统改动点末尾也插入附图，便于在改动点（含后端改动）部分查看
        draft_md = draft_md + img_block
    return sess, draft_md.strip()


def confirm_draft(session_id: str, feedback: str) -> Tuple[OrchestratorSession, str]:
    """Handle user feedback on draft: 简单区分 confirmed / revised."""
    sess = _SESSIONS.get(session_id)
    if not sess:
        raise KeyError(f"session {session_id} not found")

    fb = (feedback or "").strip()
    if not fb:
        status = "confirmed"
    elif any(k in fb for k in ("确认", "没问题", "OK", "ok", "好", "可以")):
        status = "confirmed"
    else:
        # 认为是修改意见，简单附加到 product_statement 末尾
        br = sess.draft_struct.setdefault("business_requirement", {})
        orig = br.get("product_statement") or ""
        if orig:
            br["product_statement"] = orig + "\n\n【用户补充】" + fb
        else:
            br["product_statement"] = fb
        status = "revised"

    sess.state = "DEFENDING"
    return sess, status


def apply_defend_answers(session_id: str, answer_text: str) -> OrchestratorSession:
    """在 DEFEND 阶段应用用户补充的说明，更新 business_changes，并追加 clarification_log。"""
    sess = _SESSIONS.get(session_id)
    if not sess:
        raise KeyError(f"session {session_id} not found")

    ch = sess.draft_struct.setdefault("system_changes", {})
    orig = ch.get("change_overview") or ""
    if "（待" in orig:
        ch["change_overview"] = answer_text or ""
    else:
        if orig:
            ch["change_overview"] = orig + "\n\n【补充说明】" + (answer_text or "")
        else:
            ch["change_overview"] = answer_text or ""

    # 将本轮 DEFEND 追问与用户答案写入 clarification_log
    br = sess.draft_struct.setdefault("business_requirement", {})
    cl = br.setdefault("clarification_log", {}).setdefault("items", [])
    if isinstance(cl, list) and sess.last_defend_questions:
        cl.append({
            "question": "\n".join(sess.last_defend_questions),
            "answer": answer_text or "",
            "source": "defend",
        })
    sess.state = "DEFENDING"
    return sess

