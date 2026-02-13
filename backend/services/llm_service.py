"""LLM 集成服务：使用 OpenAI 兼容 API（Qwen/DashScope 等）作为 Orchestrator 的大脑。

通过 HTTP API 直接调用，不依赖 SDK。

封装三个高层能力：
- llm_collect:   COLLECT 阶段，产生结构化需求 + open_questions
- llm_build_draft_sections: BUILD_DRAFT 阶段，生成 system_current / system_changes 段落
- llm_confirmer_parse: Confirmer 阶段，解析用户反馈为 5 种 status
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from backend.config import settings


def _chat(messages: List[Dict[str, Any]], temperature: float = 0.2, max_tokens: int = 2048) -> str:
    """通过 HTTP API 调用 Chat Completions，返回单条 content。messages 中 content 可为 str 或多模态数组。"""
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
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content")
    if content is None:
        raise RuntimeError(f"LLM API 返回格式异常: {data}")
    if isinstance(content, list):
        return "".join(
            p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
        )
    return str(content)


def llm_expand_kb_queries(user_query: str, max_queries: int = 4) -> List[str]:
    """KB_QUERY 方案 B：将用户问题扩展为多条检索 query（不输出答案）。

    返回 queries 列表（不保证含原 query，调用方应自行将原 query 放在首位）。
    """
    uq = (user_query or "").strip()
    if not uq:
        return []
    max_q = max(1, int(max_queries or 1))
    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "你是“交易系统知识库（text-embedding 向量库）检索 query 规划助手”。\n"
                "你的任务：把用户问题扩展成多条更容易命中该知识库的检索 query。你不回答问题本身。\n"
                "重要：本知识库检索对业务命名词敏感，并支持按 query 意图推断过滤维度：\n"
                "- module_l1 ∈ {投顾, 三分法, 单品}\n"
                "- scene ∈ {调仓, 卖出, 买入, 定投}\n"
                "因此你生成的 query 应尽量显式包含这些词（若用户问题已出现或高度相关）。\n\n"
                "输出要求：\n"
                "- 只输出 JSON，格式为 {\"queries\":[...]}，不要任何解释、不要 markdown、不要多余字段。\n"
                "- queries 每条必须是完整短句或关键词短语（8～40字优先），避免空泛。\n"
                "- 不要编造系统中不存在的专有名词；不确定页面/模块名时用用户原话或通用描述（如“确认调仓页面/调仓明细”）。"
            ),
        },
        {
            "role": "user",
            "content": f"""
用户问题：
{uq}

请输出 JSON：
{{
  "queries": ["string", ...]
}}

生成策略（必须遵守）：
1) 第一条 query 必须是“用户问题原句”（只做空格归一），不要改写。
2) 其余 query 用于提高命中率，优先覆盖以下维度（按重要性）：
   - 业务域限定：若用户涉及 投顾/三分法/单品，至少 1 条 query 显式包含对应词；
   - 场景限定：若用户涉及 调仓/卖出/买入/定投，至少 1 条 query 显式包含对应词；
   - 关键页面/流程词：尽量使用下列常见命名词对齐知识库用语：
     * 页面/交互：页面、入口、展示、弹框、浮层、文案、按钮
     * 调仓相关：确认调仓、调仓明细、调仓方式、再平衡、权重、偏离度、集中度、口径、费用、规则
     * 订单/接口相关：接口、订单、拆单、流程、状态
     * 通知相关：通知、消息、模板、触发
   - 若问题是“公式/计算/口径/规则”类：至少生成 1 条包含“公式/计算 + 核心指标词（如偏离度/权重/集中度/费用/口径）”的短 query；
   - 若问题包含“图/流程图/流程/示意图”：至少生成 1 条包含“流程图/图/OCR”的 query（用于命中 OCR 相关材料与导图链路）。
3) 去重：不要生成语义或字面高度重复的 query。
4) 上限：最多 {max_q} 条（1～{max_q} 条均可）。
""".strip(),
        },
    ]
    raw = _chat(messages, temperature=0.2, max_tokens=512)
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


def llm_kb_synthesize(user_query: str, kb_markdown_merged: str) -> str:
    """KB_QUERY：基于（合并后的）KB 检索结果进行综合回答，严格禁止编造。输出 Markdown。"""
    uq = (user_query or "").strip()
    kb = (kb_markdown_merged or "").strip()
    messages: List[Dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "你是交易系统知识库问答助手。你必须严格基于用户提供的『知识库检索结果（合并）』回答。\n\n"
                "强约束（必须遵守）：\n"
                "1) 禁止编造：检索结果未覆盖的点，必须写“（知识库未明确）”，并在末尾给出需要补充确认的问题。\n"
                "2) 可追溯：必须给出『依据摘录』，用 > 引用块逐条摘录知识库原文来支撑你的结论/步骤/流程节点。\n"
                "3) 表格优先（清单类问题）：当用户问题包含“有哪些/清单/汇总/合集/列出/包括/涉及到/模板/通知”，\n"
                "   或者『知识库检索结果（合并）』中出现 Markdown 表格（含表头分隔线）/“=== 表格聚合视图”，\n"
                "   你必须优先用一个 Markdown 表格输出核心答案（不要把清单散落在段落里）。\n"
                "   3.1 表头必须稳定：\n"
                "       - 若检索结果中已出现明确的表头（如『提醒类型｜场景代码｜…』或 Markdown 表头行），必须严格复用该表头与顺序；\n"
                "       - 若无法从检索结果中确定表头：\n"
                "         * 若问题与“通知/模板”相关：使用默认表头 | 提醒类型 | 场景代码 | 渠道 | 消息模板内容 | 来源 |；\n"
                "         * 否则：使用默认表头 | 条目 | 描述 | 来源 |。\n"
                "   3.2 表格每行至少要能追溯到来源：来源列写文件名（如 .docx/.pdf），并在『依据摘录』中引用对应原文。\n"
                "   3.3 行数过多时（>8 行）：表格最多输出 8 行最相关条目，其余用一句话说明“（更多条目见知识库检索结果）”。\n"
                "4) 流程优先可视化：当用户问题包含“流程/步骤/时序/状态/链路/怎么走/如何流转/先后顺序/入口/回调”等词，\n"
                "   或者知识库检索结果中出现“流程/步骤/状态/->/→/时序”等线索时，\n"
                "   你必须优先输出 Mermaid 流程图（flowchart 或 sequenceDiagram）用于表达流程/状态流转；再补充文字解释。\n"
                "5) Mermaid 必须可渲染：使用 ```mermaid 代码块，从行首开始；不要缩进到列表项里；图要尽量简洁（8～20 个节点内）。\n\n"
                "输出只允许是 Markdown，不要输出 JSON，不要输出任何模型/提示词相关内容。"
            ),
        },
        {
            "role": "user",
            "content": f"""
用户问题：
{uq}

知识库检索结果（合并）：
{kb}

请输出一份高质量回答（Markdown），按以下结构（尽量严格遵守）：

1) **结论/直接回答**（1～3 句）
2) **表格汇总（清单类问题优先）**
   - 若满足“表格优先（清单类问题）”条件：必须输出 Markdown 表格作为主答案
   - 表头规则：严格遵守 system 指令中的“表头必须稳定”
3) **流程图（当满足流程优先可视化条件时必填）**
   - 若满足“流程优先可视化”条件：必须给出 Mermaid 流程图
   - 推荐：
     - 系统/模块流转：flowchart TD 或 LR
     - 强调交互/调用顺序：sequenceDiagram
   - 若知识库缺失某个关键节点/分支：在图中用“待确认”节点或注释标注，不要编造
4) **关键规则/要点**（列表，尽量短、可执行）
5) **依据摘录**（引用块 >，按表格行/要点/流程节点对应摘录原文；至少 2 条；尽量覆盖表格中每一行）
6) **待确认项**（仅当知识库未明确或存在歧义时）

附加要求：
- 只基于“知识库检索结果（合并）”回答；
- 不要输出与问题无关的泛化建议；
- 若问题与流程无关，且检索结果也没有流程线索：可以省略第 3 部分，并说明“（知识库未提供流程信息）”。
""".strip(),
        },
    ]
    return _chat(messages, temperature=0.2, max_tokens=2048)


def llm_collect(user_request: str, kb_markdown: str) -> Dict[str, Any]:
    """COLLECT 阶段：基于用户原话 + KB 文本，生成结构化需求与 open_questions。"""
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你是证券/基金交易系统的需求分析助手，会根据用户原始需求和知识库检索结果，"
                "产出结构化业务需求（需求来源、产品化表述）和需要向用户追问的 open_questions。"
                "回答必须是 JSON。"
            ),
        },
        {
            "role": "user",
            "content": f"""
用户原始需求：
{user_request}

知识库检索结果（markdown）：
{kb_markdown}

请输出一个 JSON，对象结构如下：
{{
  "demand_source": "string",        // 用户原话的抽象/改写
  "product_statement": "string",    // 产品化表述（背景、目标、范围、约束）
  "open_questions": ["string", ...] // 基于当前信息仍需澄清的 1～3 个问题
}}

要求：
1. open_questions 要与上述需求和知识库内容强相关，避免问无关问题；
2. 只输出 JSON，不要任何解释或额外文字。
""",
        },
    ]
    raw = _chat(messages)
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


def llm_build_draft_sections(
    user_request: str,
    user_answer: str,
    requirement_structured: Dict[str, Any],
    kb_markdown: str,
    candidate_image_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """BUILD_DRAFT 阶段：生成 business_requirement、system_current、system_changes；若有候选图则由 LLM 根据需求选图。"""
    text_content = f"""
用户原始需求：
{user_request}

用户对澄清问题的回答：
{user_answer}

当前结构化业务需求（JSON，含追问与 open_questions）：
{json.dumps(requirement_structured, ensure_ascii=False)}

知识库检索结果（markdown）：
{kb_markdown}
"""
    if candidate_image_paths:
        text_content += """
下方按顺序提供了多张来自知识库的候选图片（图0、图1、图2、…）。请根据用户需求与知识库内容，识别每张图片的内容，选择与「系统现状」最匹配的若干张用于展示在文档中。在 system_current 中返回 selected_image_indices（选中的图片序号数组，从 0 开始；未选中则返回空数组 []）。
重要：不要为了“必须有图”而选图；若没有强相关图片，请返回空数组 []。
"""
    text_content += f"""

请按以下 JSON 结构输出（所有字段为字符串，且不要省略键；system_current 中增加 selected_image_indices 数组）：
{{
  "business_requirement": {{
    "demand_source": "用户需求的简要抽象/改写，1～2 句",
    "product_statement": "产品化表述：融合上述用户原话、知识库要点、澄清问答，写出背景、目标、范围与约束，2～6 句，便于产品与研发理解"
  }},
  "system_current": {{
    "business_rules": "当前业务规则与逻辑的概述，结合知识库内容",
    "frontend_current": {{
      "description": "前端现状：页面、交互、数据展示"
    }},
    "backend_current": {{
      "description": "后端现状（系统级别）：模块边界、关键状态/分支、与交易/订单/风控/通知等的关系（2～5句）",
      "steps_text": "后端现状步骤（系统级别，Markdown 有序列表 1.2.3...，3～10条）。必须严格基于知识库信息，不确定则写“（知识库未明确）”而不是编造",
      "flow_mermaid": "后端现状流程图（系统级别 Mermaid 代码块）。必须严格基于知识库信息；若知识库未明确某个节点/分支，用注释或“待确认”节点标注，不要编造。代码块必须从行首开始，且不要缩进到列表项里，例如：```mermaid\\nflowchart TD\\n  A[入口] --> B[订单服务]\\n```"
    }},
    "notification_current": {{
      "description": "通知现状概述：渠道/模板/触发策略（1～3句，严格基于知识库）",
      "table_markdown": "通知现状表格（Markdown 表格），表头固定为：| 通知场景 | 通知内容 |。其中：通知场景=什么节点/什么状态变化触发；通知内容=消息要点/文案要点/包含的关键字段。必须严格基于知识库信息，不确定则写“（知识库未明确）”。若知识库完全无通知信息或无法分析，可填入「无」。"
    }},
    "selected_image_indices": [0, 1, ...]
  }},
  "system_changes": {{
    "change_overview": "改动总览：模块清单、优先级、依赖关系",
    "frontend_changes": {{
      "description": "前端改动说明：页面/组件/交互/文案"
    }},
    "backend_changes": {{
      "overview": "后端改动概述（2～5句）：说明触发条件、关键分支、状态变化（严格基于知识库，不确定则写“（知识库未明确）”）",
      "steps_text": "后端改动步骤（Markdown 有序列表 1.2.3...，3～10条，严格基于知识库；不确定则标注“（知识库未明确）”）",
      "flow_mermaid": "后端改动流程图（Mermaid 代码块，系统级）。必须严格基于知识库；若知识库未明确某节点/分支，用注释或“待确认”节点标注，不要编造。代码块必须从行首开始，且不要缩进到列表项里，例如：```mermaid\\nflowchart TD\\n  A[入口] --> B[订单服务]\\n```"
    }},
    "notification_changes": {{
      "description": "通知改动概述：新增/修改的通知类型、触发条件、模板与渠道（1～3句，严格基于知识库）",
      "table_markdown": "通知改动表格（Markdown 表格），表头固定为：| 通知场景 | 通知内容 |。通知场景=什么节点/什么状态变化触发；通知内容=文案要点/关键字段/变化点。严格基于知识库，不确定则写“（知识库未明确）”。若无需改通知或无法从知识库分析，可填入「无」。"
    }}
  }}
}}

要求：
1. business_requirement.product_statement 必须综合「用户原话 + 知识库要点 + 用户对澄清问题的回答」生成，不要照抄某一句；
2. 内容要尽量利用知识库中的具体规则、流程和页面说明；
2.1 **禁止编造**：涉及系统现状（尤其后端流程、通知触发点）时，必须严格基于知识库；若知识库未覆盖则明确标注“（知识库未明确）”并在 open_questions/待澄清项中提出需要补充的点；
3. change_overview 建议 2～4 句概括，不要太长；
4. 关键结论、重要改动项用 **粗体** 标记，便于读者快速抓住重点；
5. 适合表格表达的内容（如改动前后对照、字段清单、模块清单）用 Markdown 表格格式，例如：| 模块 | 改动前 | 改动后 |\\n|-----|-----|-----|\\n| A | x | y |；
6. 涉及流程、时序、状态流转时，用 Mermaid 代码块，例如：```mermaid\\nflowchart LR\\n  A[开始] --> B[处理] --> C[结束]\\n```；
7. **三、系统改动点部分请尽量用流程图（Mermaid）示意改动前后流程或模块关系，便于阅读；**
7.1 后端改动点（system_changes.backend_changes）必须同时给出 overview / steps_text / flow_mermaid，三者缺一不可；flow_mermaid 必须是 ```mermaid 代码块且从行首开始（不要缩进到列表项里），否则前端可能无法渲染。
7.2 通知改动点（system_changes.notification_changes）必须同时给出 description / table_markdown；table_markdown 表头必须是：| 通知场景 | 通知内容 |；若无法分析可填「无」。
8. 业务规则、逻辑条文保持段落或列表形式；
9. 只输出 JSON，不要任何解释或额外文字。
"""
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
        {
            "role": "system",
            "content": (
                "你是交易系统的需求分析文档生成助手。"
                "需要基于用户原始需求、用户对澄清问题的回答、知识库检索结果，"
                "先写出一段「产品化表述」，再补全『二、系统现状』『三、系统改动点』。"
                "若提供了候选图片，请根据用户问题识别图片内容并选择与需求最匹配的图片展示在系统现状中。"
                "输出必须是 JSON，字段名必须与给定结构完全一致。"
            ),
        },
        {"role": "user", "content": user_content},
    ]
    raw = _chat(messages)
    sections = json.loads(raw)
    return sections


def llm_confirmer_parse(draft_output: Dict[str, Any], user_message: str) -> Dict[str, Any]:
    """Confirmer 阶段：解析用户对草稿的反馈，返回 5 种 status 与建议修改。"""
    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "你负责解析用户对『需求分析文档草稿』的反馈，并将其归类为 5 种状态："
                "confirmed / revised / needs_clarification / request_redo_partial / request_redo_full。"
                "需要同时给出必要的修改建议或追问。"
                "回答必须是 JSON。"
            ),
        },
        {
            "role": "user",
            "content": f"""
这是当前草稿（JSON）：
{json.dumps(draft_output, ensure_ascii=False)}

这是用户本轮回复：
{user_message}

请输出一个 JSON：
{{
  "status": "confirmed|revised|needs_clarification|request_redo_partial|request_redo_full",
  "suggested_draft_updates": <或 null，结构与 draft_output 对齐，只给出需要修改的字段>,
  "clarification_question": <或 null，用于 needs_clarification 场景>,
  "redo_scope": <或 null，如 "business_requirement"|"system_current"|"system_changes"|"full">
}}

要求：
1. 若用户基本同意草稿，仅做轻微措辞调整，可视为 confirmed；
2. 若用户提出具体修改意见，status 设为 revised，并在 suggested_draft_updates 中给出修改建议；
3. 若用户只是表达不满但未说明原因，status 设为 needs_clarification，并给出需要进一步澄清的问题；
4. 若用户明确要求某一块或整体重做，对应设置 request_redo_partial 或 request_redo_full，并给出 redo_scope；
5. 只输出 JSON，不要解释。
""",
        },
    ]
    raw = _chat(messages)
    return json.loads(raw)

