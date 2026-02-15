"""Confirmer service: 草稿展示 + 解析用户回复为 5 种 status（方案 A）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from backend.services.llm_service import llm_confirmer_parse
from backend.utils.text import sanitize_draft_text


DEFAULT_PROMPT = "请确认以上内容是否无误，确认后将继续做完整性检查并生成最终文档。"


@dataclass
class ConfirmerDisplayResult:
    """阶段一：生成展示时的返回."""
    display_content: str
    prompt_to_user: str


@dataclass
class ConfirmerParseResult:
    """阶段二：解析用户回复时的返回."""
    status: str  # confirmed | revised | needs_clarification | request_redo_partial | request_redo_full
    user_message: str
    suggested_draft_updates: Optional[Dict[str, Any]] = None
    clarification_question: Optional[str] = None
    redo_scope: Optional[str] = None


def _draft_to_markdown(draft: dict) -> str:
    """将 draft_output 转为可展示的 Markdown（与 editor 结构一致）。"""
    br = draft.get("business_requirement") or {}
    sc = draft.get("system_current") or {}
    ch = draft.get("system_changes") or {}
    demand_source = sanitize_draft_text(br.get("demand_source"), "（待补充）")
    product_statement = sanitize_draft_text(br.get("product_statement"), "（待补充）")
    business_rules = sanitize_draft_text(sc.get("business_rules"), "（待补充）")
    fe_desc = sanitize_draft_text(
        (sc.get("frontend_current") or {}).get("description"), "（待补充）"
    )
    be_obj = sc.get("backend_current") or {}
    be_desc = sanitize_draft_text((be_obj or {}).get("description"), "（待补充）") if isinstance(be_obj, dict) else sanitize_draft_text(str(be_obj), "（待补充）")
    be_steps = sanitize_draft_text((be_obj or {}).get("steps_text"), "") if isinstance(be_obj, dict) else ""
    be_flow = sanitize_draft_text((be_obj or {}).get("flow_mermaid"), "") if isinstance(be_obj, dict) else ""
    nt = sc.get("notification_current")
    nt_desc = sanitize_draft_text(nt, "（待补充）") if isinstance(nt, str) else sanitize_draft_text((nt or {}).get("description"), "（待补充）")
    nt_table = (nt or {}).get("table_markdown") if isinstance(nt, dict) else ""
    nt_table = sanitize_draft_text(nt_table, "") if isinstance(nt, dict) else ""
    change_overview = sanitize_draft_text(
        ch.get("change_overview") or ch.get("business_changes"), "（待补充）"
    )
    fe_ch = sanitize_draft_text(
        (ch.get("frontend_changes") or {}).get("description"), "（待补充）"
    )
    _be = ch.get("backend_changes") or {}
    # 兼容旧版：description / description_display；新版：overview/steps_text/flow_mermaid
    be_ch_overview = sanitize_draft_text((_be or {}).get("overview"), "") if isinstance(_be, dict) else ""
    be_ch_steps = sanitize_draft_text((_be or {}).get("steps_text"), "") if isinstance(_be, dict) else ""
    be_ch_flow = sanitize_draft_text((_be or {}).get("flow_mermaid"), "") if isinstance(_be, dict) else ""
    be_ch_legacy = sanitize_draft_text(
        (_be or {}).get("description_display") or (_be or {}).get("description"),
        "",
    ) if isinstance(_be, dict) else sanitize_draft_text(str(_be), "")

    nt_ch = ch.get("notification_changes")
    nt_ch_desc = sanitize_draft_text(nt_ch, "（待补充）") if isinstance(nt_ch, str) else sanitize_draft_text((nt_ch or {}).get("description"), "（待补充）")
    nt_ch_table = (nt_ch or {}).get("table_markdown") if isinstance(nt_ch, dict) else ""
    nt_ch_table = sanitize_draft_text(nt_ch_table, "") if isinstance(nt_ch, dict) else ""
    base = f"""# 需求分析文档草稿

## 一、业务需求

- 需求来源：{demand_source}
- 产品化表述：{product_statement}

---

## 二、系统现状

### 业务规则与逻辑

{business_rules}
"""
    base += f"""

### 前端现状

{fe_desc}

### 后端现状

{be_desc}
"""
    if be_steps.strip():
        base += f"""

#### 后端现状步骤（系统级别）

{be_steps.strip()}
"""
    if be_flow.strip():
        base += f"""

#### 后端现状流程图（系统级别）

{be_flow.strip()}
"""
    base += f"""

### 通知现状

{nt_desc}
"""
    if nt_table.strip():
        base += f"""

#### 通知现状表格

{nt_table.strip()}
"""
    image_urls = sc.get("image_urls") or []
    if image_urls:
        img_block = "\n### 附图（来自知识库）\n" + "\n".join(f"![附图]({u})" for u in image_urls)
        base = base + img_block
    base = base + f"""

---

## 三、系统改动点

### 改动总览

{change_overview}

### 前端改动点

{fe_ch}

### 后端改动点

"""
    # 新结构优先（overview/steps/flow），否则回退 legacy description
    if be_ch_overview.strip() or be_ch_steps.strip() or be_ch_flow.strip():
        if be_ch_overview.strip():
            base += f"{be_ch_overview.strip()}\n\n"
        if be_ch_steps.strip():
            base += f"#### 后端改动步骤\n\n{be_ch_steps.strip()}\n\n"
        if be_ch_flow.strip():
            base += f"#### 后端改动流程图\n\n{be_ch_flow.strip()}\n"
    else:
        base += f"{be_ch_legacy.strip() or '（待补充）'}\n"
    base += f"""

### 通知改动点

{nt_ch_desc}
"""
    if nt_ch_table.strip():
        base += f"""

#### 通知改动表格

{nt_ch_table.strip()}
"""
    if image_urls:
        img_block = "\n\n### 附图（来自知识库）\n" + "\n".join(f"![附图]({u})" for u in image_urls)
        base = base + img_block
    return base


def get_display(draft_output: dict, prompt_to_user: Optional[str] = None) -> ConfirmerDisplayResult:
    """阶段一：根据 draft_output 生成展示内容与提示语。"""
    display_content = _draft_to_markdown(draft_output)
    return ConfirmerDisplayResult(
        display_content=display_content,
        prompt_to_user=prompt_to_user or DEFAULT_PROMPT,
    )


async def parse_feedback(draft_output: dict, user_message: str) -> ConfirmerParseResult:
    """阶段二：解析用户对草稿的回复，返回 status 及可选字段。

    优先调用 Qwen（llm_confirmer_parse）；若 LLM 不可用或报错，则回退到原有关键词规则。
    """
    msg = (user_message or "").strip()

    # 尝试用 LLM 解析
    try:
        parsed = await llm_confirmer_parse(draft_output, user_message or "")
        status = parsed.get("status") or "confirmed"
        suggested = parsed.get("suggested_draft_updates")
        clarification_question = parsed.get("clarification_question")
        redo_scope = parsed.get("redo_scope")
        return ConfirmerParseResult(
            status=status,
            user_message=user_message or "",
            suggested_draft_updates=suggested,
            clarification_question=clarification_question,
            redo_scope=redo_scope,
        )
    except Exception as e:
        # 回退到原有关键词规则
        import logging
        logging.getLogger(__name__).warning("LLM llm_confirmer_parse 调用失败，已回退到关键词规则: %s", e)

    # === 回退逻辑（原实现） ===
    # request_redo_full
    if any(k in msg for k in ("整体重做", "全部重做", "重新生成", "从头再来")):
        return ConfirmerParseResult(
            status="request_redo_full",
            user_message=user_message or "",
            redo_scope="full",
        )

    # request_redo_partial
    if any(k in msg for k in ("只改业务需求", "只改系统现状", "只改系统改动点", "只改业务", "只改现状", "只改改动点")):
        scope = "business_requirement" if "业务" in msg else ("system_current" if "现状" in msg else "system_changes")
        return ConfirmerParseResult(
            status="request_redo_partial",
            user_message=user_message or "",
            redo_scope=scope,
        )
    if "重做" in msg and ("一块" in msg or "部分" in msg or "一段" in msg):
        return ConfirmerParseResult(
            status="request_redo_partial",
            user_message=user_message or "",
            redo_scope="system_changes",
        )

    # confirmed
    if not msg or any(k in msg for k in ("确认", "没问题", "OK", "ok", "好", "可以", "无异议", "通过")):
        return ConfirmerParseResult(status="confirmed", user_message=user_message or "")

    # needs_clarification: 很短且像否定
    if len(msg) <= 4 and any(k in msg for k in ("不", "有问", "错", "改")):
        return ConfirmerParseResult(
            status="needs_clarification",
            user_message=user_message or "",
            clarification_question="请具体说明需要修改的部分或您的建议，以便更新草稿。",
        )

    # revised: 有具体修改意见（较长或明确补充）
    br = draft_output.get("business_requirement") or {}
    orig_ps = br.get("product_statement") or ""
    new_ps = orig_ps + "\n\n【用户补充】" + msg if orig_ps else msg
    return ConfirmerParseResult(
        status="revised",
        user_message=user_message or "",
        suggested_draft_updates={
            "business_requirement": {"product_statement": new_ps},
        },
    )
