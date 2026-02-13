"""LLM 文生图服务：调用 DashScope 万相生成流程图 PNG，保存到静态目录并返回可访问 URL。"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Optional

import requests

from backend.config import settings

logger = logging.getLogger(__name__)

# 文生图 prompt 最大长度（万相限制约 2100 字符）
PROMPT_MAX_LEN = 2000


def _strip_mermaid_from_description(description: str) -> str:
    """去掉 description 中的 ```mermaid ... ``` 块，只保留文案。"""
    if not description or "```mermaid" not in description:
        return (description or "").strip()
    # 去掉第一个 ```mermaid 到对应 ``` 之间的内容
    out = re.sub(r"```mermaid\s*[\s\S]*?```", "", description, flags=re.IGNORECASE)
    return out.strip()


def generate_flowchart_image(flow_description: str) -> Optional[str]:
    """根据后端流程描述调用万相文生图，保存 PNG 并返回可访问 URL（如 /api/kb-images/flowchart_xxx.png）。

    若未配置 API、请求失败或保存失败则返回 None。
    """
    if not getattr(settings, "image_gen_enabled", True):
        return None
    if not settings.llm_api_key:
        logger.warning("文生图未配置 API Key，跳过流程图生成")
        return None
    desc = _strip_mermaid_from_description(flow_description or "")
    if not desc:
        desc = "后端流程：步骤与节点"
    prompt = f"根据以下后端流程说明，生成一张清晰的流程图示意图，风格简洁专业，包含节点与箭头，适合技术文档。流程说明：{desc}"
    if len(prompt) > PROMPT_MAX_LEN:
        prompt = prompt[: PROMPT_MAX_LEN - 20] + "…"

    url = getattr(settings, "image_gen_url", None) or "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    model = getattr(settings, "image_gen_model", "wan2.6-t2i")
    payload = {
        "model": model,
        "input": {
            "messages": [
                {"role": "user", "content": [{"text": prompt}]}
            ]
        },
        "parameters": {
            "n": 1,
            "size": "1280*1280",
            "prompt_extend": False,
            "watermark": False,
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("文生图请求失败: %s", e)
        return None

    # 同步接口返回 output.choices[0].message.content[0].image
    choices = (data.get("output") or {}).get("choices") or []
    image_url_remote = None
    if choices:
        content = (choices[0].get("message") or {}).get("content") or []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image" and item.get("image"):
                image_url_remote = item.get("image")
                break
    if not image_url_remote:
        logger.warning("文生图响应中未找到 image URL: %s", data)
        return None

    # 下载并保存到本地
    out_dir = getattr(settings, "images_output_dir_abs", None) or settings.images_output_dir
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"flowchart_{uuid.uuid4().hex[:12]}.png"
    out_path = out_dir / name
    try:
        r = requests.get(image_url_remote, timeout=30)
        r.raise_for_status()
        out_path.write_bytes(r.content)
    except Exception as e:
        logger.warning("下载文生图失败: %s", e)
        return None
    return f"/api/kb-images/{name}"
