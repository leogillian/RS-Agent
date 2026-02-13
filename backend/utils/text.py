"""文本清洗：避免 LLM 输出 "undefined"/"null" 写入 Markdown 导致前端 Mermaid 报错。"""

from __future__ import annotations


def sanitize_draft_text(
    value: str | None,
    placeholder: str = "（待补充）",
) -> str:
    """若值为 None、空、或字面量 "undefined"/"null"，返回占位文案。"""
    if value is None:
        return placeholder
    s = value.strip() if isinstance(value, str) else str(value).strip()
    if not s or s.lower() in ("undefined", "null"):
        return placeholder
    return s
