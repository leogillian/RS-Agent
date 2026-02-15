"""Intent routing between KB query and orchestrator flow.

P1-1: 混合意图路由 — 关键词规则（快速路径） + LLM 意图分类（兜底），降低误判。

路由策略：
1. 强关键词命中 → 直接返回（KB_QUERY 或 ORCH_FLOW）；
2. 关键词未命中 / 模糊 → 调用 LLM 分类（若 LLM 不可用则回退 ORCH_FLOW）。
"""

from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class Intent(str, Enum):
    KB_QUERY = "KB_QUERY"
    ORCH_FLOW = "ORCH_FLOW"


# ---------------------------------------------------------------------------
# 关键词规则（快速路径）
# ---------------------------------------------------------------------------

# 强 KB_QUERY 关键词：命中即走 KB_QUERY
_KB_STRONG_KEYWORDS = (
    "查询知识库",
    "查知识库",
    "用知识库",
    "调用知识库",
    "交易规则",
    "交易系统规则",
)

# 弱 KB_QUERY 模式：疑问句 + 交易/规则相关词 → 倾向 KB_QUERY
_KB_QUESTION_PATTERNS = [
    re.compile(r"(?:什么|怎么|如何|哪些|几|多少|是否|有没有|能否).*(?:规则|流程|逻辑|配置|现状|机制|策略|方案)"),
    re.compile(r"(?:规则|流程|逻辑|配置|现状|机制|策略|方案).*(?:是什么|有哪些|怎么样|如何)"),
    re.compile(r"(?:定投|调仓|赎回|申购|追加|份额|基金|组合|持仓|下单|拆单).*(?:规则|流程|逻辑|怎么|是什么|有哪些)"),
    re.compile(r"(?:规则|流程|逻辑).*(?:定投|调仓|赎回|申购|追加|份额|基金|组合|持仓|下单|拆单)"),
]

# 强 ORCH_FLOW 关键词：命中即走 ORCH_FLOW
_ORCH_STRONG_KEYWORDS = (
    "系统改动点",
    "改动点",
    "需求分析",
    "生成需求分析",
)

# 弱 ORCH_FLOW 模式：包含改动/新增/优化等动作词 → 倾向 ORCH_FLOW
_ORCH_ACTION_PATTERNS = [
    re.compile(r"(?:新增|增加|添加|修改|调整|优化|去掉|移除|删除|隐藏|改为|改成|替换|升级|重构|上线|需要)"),
]


def _rule_based_detect(text: str) -> Tuple[Optional[Intent], float]:
    """基于规则的意图检测。

    Returns:
        (intent, confidence):
        - intent is None when rules cannot decide (ambiguous).
        - confidence: 1.0 for strong match, 0.7 for weak match.
    """
    normalized = (text or "").strip()
    if not normalized:
        return Intent.ORCH_FLOW, 1.0

    # 1. 强关键词：KB_QUERY
    if any(k in normalized for k in _KB_STRONG_KEYWORDS):
        return Intent.KB_QUERY, 1.0

    # 2. 强关键词：ORCH_FLOW
    if any(k in normalized for k in _ORCH_STRONG_KEYWORDS):
        return Intent.ORCH_FLOW, 1.0

    # 3. 弱模式匹配
    kb_score = sum(1 for p in _KB_QUESTION_PATTERNS if p.search(normalized))
    orch_score = sum(1 for p in _ORCH_ACTION_PATTERNS if p.search(normalized))

    if kb_score > 0 and orch_score == 0:
        return Intent.KB_QUERY, 0.7
    if orch_score > 0 and kb_score == 0:
        return Intent.ORCH_FLOW, 0.7

    # 4. 模糊区域 → 无法确定
    return None, 0.0


def detect_intent(text: str) -> Intent:
    """同步快速路径：仅使用规则检测，不调用 LLM。

    保留此函数以兼容不需要 LLM 的场景。
    """
    intent, _ = _rule_based_detect(text)
    return intent if intent is not None else Intent.ORCH_FLOW


async def detect_intent_hybrid(text: str) -> Tuple[Intent, str]:
    """混合意图路由（P1-1）：规则 + LLM 兜底。

    Returns:
        (intent, method): method 为 "rule" 或 "llm" 或 "llm_fallback"，用于 trace 展示。
    """
    intent, confidence = _rule_based_detect(text)

    # 强命中 → 直接返回
    if intent is not None and confidence >= 0.9:
        return intent, "rule"

    # 弱命中或无法确定 → 尝试 LLM 分类
    try:
        from backend.services.llm_service import llm_classify_intent
        llm_intent = await llm_classify_intent(text)
        if llm_intent is not None:
            return llm_intent, "llm"
    except Exception as e:
        logger.warning("LLM 意图分类失败，回退到规则版: %s", e)

    # LLM 不可用 → 使用规则结果或默认 ORCH_FLOW
    return (intent if intent is not None else Intent.ORCH_FLOW), "llm_fallback"
