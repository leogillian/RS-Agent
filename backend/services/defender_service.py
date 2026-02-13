"""Defender service: 按 demand_analysis_doc_v1 schema 做完整性检查（P3）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

PLACEHOLDER_TOKEN = "（待"

# 通知表格「分析不出」时可填写的占位值，Defender 视为有效不再追问
NONE_NOTIFICATION_VALUES = frozenset({"无", "（无）", "无。"})

# 必填路径与对应追问（与 demand_analysis_doc_v1 对齐）
REQUIRED_PATHS: List[tuple[str, str]] = [
    ("business_requirement.demand_source", "请补充需求来源（用户原始表述）。"),
    ("business_requirement.product_statement", "请补充产品化表述（背景、目标、范围、约束）。"),
    ("system_current.business_rules", "请补充当前业务规则与逻辑。"),
    ("system_current.frontend_current.description", "请补充前端现状（页面、交互、数据展示）。"),
    ("system_current.backend_current.description", "请补充后端现状（模块、功能、流程）。"),
    ("system_current.backend_current.flow_mermaid", "请补充后端现状的系统级流程图（Mermaid）。"),
    ("system_current.notification_current", "请补充通知现状（类型、触发条件、模板与渠道）。"),
    ("system_current.notification_current.table_markdown", "请补充通知现状表格（通知场景/通知内容）。"),
    ("system_changes.change_overview", "请补充改动总览（模块、优先级、依赖）。"),
    ("system_changes.frontend_changes.description", "请补充前端改动说明。"),
    ("system_changes.backend_changes.overview", "请补充后端改动概述（系统级）。"),
    ("system_changes.backend_changes.flow_mermaid", "请补充后端改动的系统级流程图（Mermaid）。"),
    ("system_changes.notification_changes.description", "请补充通知改动说明。"),
    ("system_changes.notification_changes.table_markdown", "请补充通知改动表格（通知场景/通知内容）。"),
]


@dataclass
class MissingField:
    field_path: str
    reason: str
    suggested_questions_to_user: List[str] = field(default_factory=list)
    suggested_query_to_kb: Optional[str] = None


@dataclass
class DefenderResult:
    is_complete: bool
    questions: List[str]
    missing_fields: List[MissingField] = field(default_factory=list)


def _get_path(draft: dict, path: str) -> Any:
    """按点分路径取值，如 system_changes.frontend_changes.description."""
    obj = draft
    for part in path.split("."):
        obj = (obj or {}).get(part)
        if obj is None:
            return None
    return obj


def _str_value(val: Any) -> str:
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        return (val.get("description") or "") or ""
    return ""


def check_draft(draft: dict) -> DefenderResult:
    """按 schema 必填与占位检查草稿，返回 is_complete、questions（追问列表）、missing_fields。"""
    missing: List[MissingField] = []
    for path, question in REQUIRED_PATHS:
        val = _get_path(draft, path)
        if val is None:
            missing.append(MissingField(
                field_path=path,
                reason="required_but_empty",
                suggested_questions_to_user=[question],
                suggested_query_to_kb=path.split(".")[0] + " " + question[:20],
            ))
            continue
        s = _str_value(val) if not isinstance(val, str) else val
        if PLACEHOLDER_TOKEN in s:
            missing.append(MissingField(
                field_path=path,
                reason="placeholder",
                suggested_questions_to_user=[question],
                suggested_query_to_kb=path.split(".")[0] + " " + question[:20],
            ))

    # === 额外格式校验：Mermaid 与表头 ===
    def _need_missing(field_path: str, question: str, reason: str = "invalid_format"):
        missing.append(MissingField(
            field_path=field_path,
            reason=reason,
            suggested_questions_to_user=[question],
            suggested_query_to_kb=field_path.split(".")[0] + " " + question[:20],
        ))

    # 后端现状 Mermaid
    sc_mermaid = _get_path(draft, "system_current.backend_current.flow_mermaid")
    if isinstance(sc_mermaid, str) and sc_mermaid.strip():
        if "```mermaid" not in sc_mermaid:
            _need_missing(
                "system_current.backend_current.flow_mermaid",
                "后端现状流程图需要以 ```mermaid 代码块形式给出（从行首开始）。请补充。",
            )

    # 后端改动 Mermaid
    ch_mermaid = _get_path(draft, "system_changes.backend_changes.flow_mermaid")
    if isinstance(ch_mermaid, str) and ch_mermaid.strip():
        if "```mermaid" not in ch_mermaid:
            _need_missing(
                "system_changes.backend_changes.flow_mermaid",
                "后端改动流程图需要以 ```mermaid 代码块形式给出（从行首开始）。请补充。",
            )

    # 通知现状表格表头（若 LLM 分析不出，填入「无」视为有效，跳过格式校验）
    nt_table = _get_path(draft, "system_current.notification_current.table_markdown")
    if isinstance(nt_table, str) and nt_table.strip():
        nt_stripped = nt_table.strip()
        if nt_stripped not in NONE_NOTIFICATION_VALUES and "| 通知场景 | 通知内容 |" not in nt_table.replace(" ", ""):
            _need_missing(
                "system_current.notification_current.table_markdown",
                "通知现状表格表头必须是：| 通知场景 | 通知内容 |。若知识库无通知信息可填「无」。",
            )

    # 通知改动表格表头（若 LLM 分析不出，填入「无」视为有效，跳过格式校验）
    ntc_table = _get_path(draft, "system_changes.notification_changes.table_markdown")
    if isinstance(ntc_table, str) and ntc_table.strip():
        ntc_stripped = ntc_table.strip()
        if ntc_stripped not in NONE_NOTIFICATION_VALUES and "| 通知场景 | 通知内容 |" not in ntc_table.replace(" ", ""):
            _need_missing(
                "system_changes.notification_changes.table_markdown",
                "通知改动表格表头必须是：| 通知场景 | 通知内容 |。若无通知改动可填「无」。",
            )

    # 防止无限追问：若改动总览已有足够非占位内容，则不再因占位追问
    change_overview = _get_path(draft, "system_changes.change_overview") or _get_path(draft, "system_changes.business_changes")
    co_str = _str_value(change_overview)
    if co_str and len(co_str.replace(PLACEHOLDER_TOKEN, "").strip()) >= 20:
        missing = [m for m in missing if "change_overview" not in m.field_path and "business_changes" not in m.field_path]
        if not missing:
            return DefenderResult(is_complete=True, questions=[], missing_fields=[])

    questions = []
    for m in missing:
        questions.extend(m.suggested_questions_to_user or [])

    if not questions:
        questions = ["请补充具体的改动总览，以及前端/后端/通知各自需要做哪些调整。"]

    return DefenderResult(
        is_complete=len(missing) == 0,
        questions=questions,
        missing_fields=missing,
    )

