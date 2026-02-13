"""Intent routing between KB query and orchestrator flow."""

from __future__ import annotations

from enum import Enum


class Intent(str, Enum):
    KB_QUERY = "KB_QUERY"
    ORCH_FLOW = "ORCH_FLOW"


_KB_KEYWORDS = (
    "查询知识库",
    "查知识库",
    "用知识库",
    "调用知识库",
    "交易规则",
    "交易系统规则",
)


def detect_intent(text: str) -> Intent:
    """Very simple rule-based intent router.

    - 命中「查询知识库 / 交易规则」相关关键词 → KB_QUERY
    - 其他情况 → ORCH_FLOW
    """
    normalized = (text or "").strip()
    if not normalized:
        return Intent.ORCH_FLOW

    if any(k in normalized for k in _KB_KEYWORDS):
        return Intent.KB_QUERY

    return Intent.ORCH_FLOW

