"""Minimal Editor-style service for RS-Agent.

根据已对齐 demand_analysis_doc_v1 的 draft_struct，生成最终 Markdown 文本。
后续可以在此处扩展更多排版与高亮能力。
"""

from __future__ import annotations

from backend.utils.text import sanitize_draft_text


def render_final(draft: dict) -> str:
  br = draft.get("business_requirement") or {}
  sc = draft.get("system_current") or {}
  ch = draft.get("system_changes") or {}

  demand_source = sanitize_draft_text(br.get("demand_source"), "（待补充）")
  product_statement = sanitize_draft_text(br.get("product_statement"), "（待补充）")
  clarification_log = br.get("clarification_log") or {}
  cl_items = clarification_log.get("items") or []

  business_rules = sanitize_draft_text(sc.get("business_rules"), "（待补充）")
  fe_desc = sanitize_draft_text(
    (sc.get("frontend_current") or {}).get("description"),
    "（待补充前端现状）",
  )
  be_obj = sc.get("backend_current") or {}
  be_desc = (
    sanitize_draft_text((be_obj or {}).get("description"), "（待补充后端现状）")
    if isinstance(be_obj, dict)
    else sanitize_draft_text(str(be_obj), "（待补充后端现状）")
  )
  be_steps = sanitize_draft_text((be_obj or {}).get("steps_text"), "") if isinstance(be_obj, dict) else ""
  be_flow = sanitize_draft_text((be_obj or {}).get("flow_mermaid"), "") if isinstance(be_obj, dict) else ""
  nt_raw = sc.get("notification_current")
  nt_desc = sanitize_draft_text(nt_raw, "（待补充通知/消息现状）") if isinstance(nt_raw, str) else sanitize_draft_text((nt_raw or {}).get("description"), "（待补充通知/消息现状）")
  nt_table = (nt_raw or {}).get("table_markdown") if isinstance(nt_raw, dict) else ""
  nt_table = sanitize_draft_text(nt_table, "") if isinstance(nt_raw, dict) else ""

  change_overview = sanitize_draft_text(
    ch.get("change_overview") or ch.get("business_changes"),
    "（待补充改动总览）",
  )
  fe_changes = sanitize_draft_text(
    (ch.get("frontend_changes") or {}).get("description"),
    "（待补充前端改动点）",
  )
  _be = ch.get("backend_changes") or {}
  be_ch_overview = sanitize_draft_text((_be or {}).get("overview"), "") if isinstance(_be, dict) else ""
  be_ch_steps = sanitize_draft_text((_be or {}).get("steps_text"), "") if isinstance(_be, dict) else ""
  be_ch_flow = sanitize_draft_text((_be or {}).get("flow_mermaid"), "") if isinstance(_be, dict) else ""
  be_changes_legacy = sanitize_draft_text(
    (_be or {}).get("description_display") or (_be or {}).get("description"),
    "（待补充后端改动点）",
  ) if isinstance(_be, dict) else sanitize_draft_text(str(_be), "（待补充后端改动点）")
  nt_ch_raw = ch.get("notification_changes")
  nt_changes_desc = sanitize_draft_text(nt_ch_raw, "（待补充通知/消息改动点）") if isinstance(nt_ch_raw, str) else sanitize_draft_text((nt_ch_raw or {}).get("description"), "（待补充通知/消息改动点）")
  nt_changes_table = (nt_ch_raw or {}).get("table_markdown") if isinstance(nt_ch_raw, dict) else ""
  nt_changes_table = sanitize_draft_text(nt_changes_table, "") if isinstance(nt_ch_raw, dict) else ""
  risks = ch.get("risks") or []

  md = f"""# 需求分析文档（正式版）

## 一、业务需求

- 需求来源：{demand_source}
- 产品化表述：{product_statement}

---

## 二、系统现状

### 业务规则与逻辑

{business_rules}
"""
  md = md + f"""

### 前端现状

- {fe_desc}

### 后端现状

- {be_desc}
"""
  if be_steps.strip():
    md = md + f"""

#### 后端现状步骤（系统级别）

{be_steps.strip()}
"""
  if be_flow.strip():
    md = md + f"""

#### 后端现状流程图（系统级别）

{be_flow.strip()}
"""
  md = md + f"""

### 通知 / 消息现状

- {nt_desc}
"""
  if nt_table.strip():
    md = md + f"""

#### 通知现状表格

{nt_table.strip()}
"""
  image_urls = sc.get("image_urls") or []
  if image_urls:
    md = md + "\n\n### 附图（来自知识库）\n" + "\n".join(f"![附图]({u})" for u in image_urls)
  md = md + f"""

---

## 三、系统改动点

### 改动总览

{change_overview}

### 前端改动点

{fe_changes}

### 后端改动点

"""
  if be_ch_overview.strip() or be_ch_steps.strip() or be_ch_flow.strip():
    if be_ch_overview.strip():
      md = md + f"{be_ch_overview.strip()}\n\n"
    if be_ch_steps.strip():
      md = md + f"#### 后端改动步骤\n\n{be_ch_steps.strip()}\n\n"
    if be_ch_flow.strip():
      md = md + f"#### 后端改动流程图\n\n{be_ch_flow.strip()}\n"
  else:
    md = md + f"{be_changes_legacy}\n"
  md = md + f"""

### 通知改动点

{nt_changes_desc}
"""
  if nt_changes_table.strip():
    md = md + f"""

#### 通知改动表格

{nt_changes_table.strip()}
"""
  if image_urls:
    md = md + "\n\n### 附图（来自知识库）\n" + "\n".join(f"![附图]({u})" for u in image_urls)
  if risks:
    risk_lines = [
      f"- {sanitize_draft_text(r, '（待补充）') if isinstance(r, str) else r}"
      for r in risks
    ]
    md = md + "\n\n### 风险与回滚要点\n\n" + "\n".join(risk_lines)
  md = md + "\n"
  if cl_items:
    lines = ["", "<details open>", "<summary>待澄清项记录</summary>", ""]
    for i, item in enumerate(cl_items, 1):
      q = sanitize_draft_text(item.get("question"), "")
      a = sanitize_draft_text(item.get("answer"), "")
      src = sanitize_draft_text(item.get("source"), "")
      lines.append(f"### 第 {i} 轮（来源：{src}）")
      lines.append(f"- **问题**：{q}")
      lines.append(f"- **用户答案**：{a}")
      lines.append("")
    lines.append("</details>")
    md = md + "\n".join(lines)
  return md.strip()

