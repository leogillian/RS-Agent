# RS-Agent Changelog

本文件记录 RS-Agent 项目**每次改动**的迭代历程，包括前端、后端、各服务的任何变更（枚举、文案、配置、样式、接口、逻辑等）。版本号采用语义化版本 `MAJOR.MINOR.PATCH`，普通改动以 PATCH 递增。

## [Unreleased]

- 进行中的改动请在发版前记录到此，发版时拆分为新版本条目。

---

## [0.1.29] - 2026-02-13

### 前端

- **附图与流程图缩略图**：Markdown 内附图（`![附图](url)`）与 Mermaid 流程图默认以缩略图展示（220×160px / 340×240px），点击后灯箱全尺寸预览，提升输出结果整体可读性。
- App：mdComponents 新增 `img` 组件，附图缩略图可点击打开 lightbox；MermaidBlock 支持 `onExpand`，流程图缩略图点击弹层全尺寸。
- style：新增 `.msg-markdown-img`、`.mermaid-thumbnail-wrapper`、`.mermaid-expand-hint`、`.lightbox-mermaid-wrap` 样式；KB_QUERY 的 msg-images 缩略图尺寸调整为 220×160px。

---

## [0.1.28] - 2026-02-13

### 服务

- defender_service：通知现状/改动表格在 LLM 无法分析时可填「无」视为有效，不再追问待补充。
- llm_service：草稿 JSON prompt 明确通知表格可填「无」的兜底规则。

---

## [0.1.27] - 2026-02-12

### 服务

- llm_service：KB_QUERY 综合回答 prompt 增加“清单类问题优先表格输出 + 表头稳定复用/兜底表头”，提升通知/模板类问题的结构化可读性与可追溯性。

---

## [0.1.26] - 2026-02-12

### 服务

- llm_service：KB_QUERY 的多 query 扩展 prompt 按交易知识库的 module_l1/scene 与流程/页面/规则关键词做定制，提升检索命中与准确度。
- llm_service：KB_QUERY 综合回答 prompt 增强“流程优先可视化”，当问题或检索结果包含流程线索时优先输出 Mermaid 流程图以提高可读性。

---

## [0.1.25] - 2026-02-12

### 后端

- KB_QUERY：落地方案 B（LLM 扩展多 query → 多次 KB 检索合并去重 → LLM 综合输出），并在返回 content 中携带 `usedLLM/subQueries/rawMarkdown/kbRuns` 便于调试与回放。
- config：新增 KB_QUERY 方案 B 的开关与参数（`RS_AGENT_KB_QUERY_LLM_ENABLED/MAX_SUBQUERIES/MAX_MERGED_CHARS`）。

### 服务

- 新增 `kb_query_enhanced`：封装可复用的 enhanced_kb_query（后续 ORCH_FLOW 可直接复用同一检索增强能力）。
- llm_service：新增 `llm_expand_kb_queries` 与 `llm_kb_synthesize`，分别用于 query 扩展与综合回答（严格基于 KB 合并结果，禁止编造）。

### 文档与规范

- .env.example：补充 KB_QUERY 方案 B 相关环境变量示例。
- README：更新「当前能力」与「LLM 配置」对 KB_QUERY 方案 B 的说明。

---

## [0.1.24] - 2026-02-12

### 文档与规范

- README：在「后续迭代方向」记录 KB_QUERY 先做方案 B（LLM 多 query 检索增强 + 综合输出），并抽象可复用的 `retrieve` 函数供 ORCH_FLOW 后续复用。

---

## [0.1.23] - 2026-02-12

### 后端

- **三、系统改动点-后端改动点结构化**：`system_changes.backend_changes` 改为 `overview/steps_text/flow_mermaid`，草稿/终稿按块渲染，确保 Mermaid 不再“丢失”。
- **三、系统改动点-通知改动点表格化**：`system_changes.notification_changes` 支持 `description + table_markdown`，草稿/终稿渲染「通知改动表格」。
- **Defender 增强校验**：后端现状/改动的 Mermaid 必须包含 ```mermaid；通知现状/改动表格必须包含固定表头「通知场景/通知内容」。

---

## [0.1.22] - 2026-02-12

### 后端

- **系统现状去掉关键表格**：移除 `kb_tables_markdown` 相关契约与渲染，避免出现“为了表格而表格”的不准确内容。
- **系统现状-后端补齐系统级流程**：`system_current.backend_current` 增加步骤与 Mermaid 流程图输出与渲染，要求严格基于知识库信息，不确定则显式标注。
- **系统现状-通知表格化**：`system_current.notification_current` 支持输出通知现状表格（通知场景/通知内容）并在草稿/终稿渲染。
- Defender 完整性检查新增现状流程图与通知表格的必填校验。

---

## [0.1.21] - 2026-02-12

### 前端

- **修复 Mermaid 流程图闪烁**：将 Markdown 渲染组件与消息列表渲染 memo 化，减少输入框编辑导致的整页重渲染；Mermaid 渲染使用稳定 id + SVG 缓存，避免反复卸载/重建造成闪烁。

### 后端

- **表格引用改为 LLM 决策**：将 KB 表格作为候选材料提供给 LLM，仅当与用户问题/现状强相关时才输出 `system_current.kb_tables_markdown`，避免“为了表格而表格”。

---

## [0.1.20] - 2026-02-12

### 后端

- **现状部分稳定展示知识库表格**：从 KB 返回的「表格聚合视图」中抽取 Markdown 表格，写入 `system_current.kb_tables_markdown`，并在草稿/终稿固定渲染「关键表格（来自知识库）」区块。
- **后端改动点优先 Mermaid + 步骤文字**：调整 LLM 生成约束与 Markdown 渲染结构，避免列表项吞掉多行内容，使 Mermaid 流程图可渲染、步骤描述可读。
- **候选图更像流程图**：基于 KB 输出的 `path/page` 引用从 PDF 页提取“最大图”作为候选（减少抽到 logo/装饰图导致选图为空）。

---

## [0.1.19] - 2026-02-12

### 后端

- **trace detail 固定格式**：将 SSE trace 的 `detail` 统一为 `key=value | key=value` 形式（如 `target=http:POST ... | model=... | duration_ms=... | images=...`），便于用户快速扫读外部调用与执行摘要。

---

## [0.1.18] - 2026-02-12

### 后端

- **thought process（trace）内容更丰富**：在 SSE trace 中补充当前执行的服务/函数名，并在关键步骤标注外部调用的接口/命令（如 LLM `POST /chat/completions`、KB 子进程 `run_all_sources.py`、文生图接口等），同时增加各步骤耗时与数量摘要，便于用户理解系统在做什么。

---

## [0.1.17] - 2026-02-12

### 前端

- **聪明自动滚动**：仅当用户停留在底部附近时才自动滚动；用户上滑回看时不抢滚动，改为提示「有新消息 · 回到底部」按钮。
- **停止生成**：流式请求支持 AbortController，可点击「停止」中断生成；中断后不会触发回退请求，并在 trace 中记录「已停止生成」。

---

## [0.1.16] - 2026-02-12

### 前端

- **支持流式展示“思考过程”**：新增 SSE 流式调用 `/api/agent/stream`，在会话窗口用折叠面板实时展示 trace 步骤；流失败自动回退到原 `/api/agent`。
- **会话窗口自动滚动**：消息与 trace 更新时自动滚动到最底部，确保始终看到最新内容。

### 后端

- **新增 `POST /api/agent/stream`**：以 `text/event-stream` 输出 `trace` 与 `final` 事件，便于前端实时展示执行轨迹；同时将 trace 以 `payload_type="TRACE"` 写入会话消息，用于历史回放时附着到后续助手消息。

---

## [0.1.15] - 2026-02-12

### 前端

- **流程图链接在应用内灯箱打开并增加关闭按钮**：点击「查看流程图（PNG）」等图片链接时不再跳转新页，改为在当前页用灯箱展示图片；灯箱右上角增加「×」关闭按钮，点击遮罩或按 Escape 也可关闭，便于回到原文。

---

## [0.1.14] - 2026-02-12

### 服务

- **三、系统改动点-后端：LLM 文生图生成流程图**：后端改动部分若有描述，则调用 DashScope 万相文生图 API 根据描述生成流程图 PNG；正文中先用文案描述（去掉 Mermaid 代码块后的文字，若无则「后端流程见下图。」），末尾增加「查看流程图（PNG）」链接，点击后展示 PNG。文案、流程图图片、链接均由现有流程与文生图服务配合完成。

### 后端

- `config`：新增 `image_gen_enabled`、`image_gen_url`、`image_gen_model`（万相 wan2.6-t2i），可通过环境变量 `RS_AGENT_IMAGE_GEN_*` 配置。
- 新增 `image_gen_service`：`generate_flowchart_image(flow_description)` 调用万相同步文生图接口，下载图片保存至 `images_output_dir_abs`，返回 `/api/kb-images/flowchart_xxx.png`；`_strip_mermaid_from_description` 用于从描述中剥离 Mermaid 代码块得到纯文案。
- `orchestrator_controller`：生成草稿时若 `backend_changes.description` 非空则调用文生图，成功则写入 `flowchart_image_url` 与 `description_display`（文案 + 链接）；草稿 Markdown 中「后端改动点」优先使用 `description_display`。
- `editor_service`、`confirmer_service`：「后端改动点」展示优先使用 `backend_changes.description_display`，无则用 `description`。

---

## [0.1.13] - 2026-02-12

### 服务

- **三、系统改动点内展示附图**：草稿与终稿中「三、系统改动点」此前仅在「二、系统现状」末尾展示附图，导致在改动点（含后端改动）部分看不到图。现改为在「三、系统改动点」末尾同样追加「附图（来自知识库）」区块，与 system_current.image_urls 一致，便于在查看后端改动等时对照图片。

### 后端

- `orchestrator_controller`：生成 draft_md 时在「三、系统改动点」末尾追加 img_block。
- `confirmer_service._draft_to_markdown`：在「三、系统改动点」段落末尾追加附图区块。
- `editor_service.render_final`：在「三、系统改动点」段落末尾追加附图区块。

---

## [0.1.12] - 2026-02-12

### 服务

- **现状附图由 LLM 根据需求选图**：草稿/终稿「二、系统现状」的附图不再展示知识库返回的全部图片，改为在现有 `llm_build_draft_sections` 一次调用中传入候选图（base64），由支持视觉的 LLM 根据用户需求识别图片内容并选择最匹配的若干张；返回 `selected_image_indices`，Orchestrator 据此写入 `system_current.image_urls` 与草稿附图区块。
- **系统改动点鼓励流程图**：`llm_build_draft_sections` 的 prompt 增加「三、系统改动点部分请尽量用流程图（Mermaid）示意改动前后流程或模块关系」，并保留原有表格与 Mermaid 要求。

### 后端

- `llm_service`：`_chat` 支持多模态 messages（content 为数组）及返回 content 为 list 时的拼接；新增 `_image_path_to_data_url`；`llm_build_draft_sections` 新增参数 `candidate_image_paths`，有候选图时构建多模态 user message 并在 JSON 中要求 `system_current.selected_image_indices`。
- `orchestrator_controller`：`answer_questions` 中根据 `kb_image_urls` 与 `images_output_dir_abs` 构建候选图路径并传入 LLM，按返回的 `selected_image_indices` 映射为 URL 写入 `draft_system_current.image_urls`；草稿 Markdown 附图使用 `draft.system_current.image_urls`。

---

## [0.1.11] - 2026-02-12

### 后端

- 无接口/路由变更（仅版本号对齐）

### 服务

- **系统现状引用知识库图片**：Orchestrator 在 COLLECT 与用户回答后再次检索时保存 KB 导出的图片路径并转为前端可访问 URL（`/api/kb-images/xxx`），写入 session 的 `kb_image_urls`；用户回答后合并二次检索的图片并去重。草稿与终稿的「二、系统现状」在通知现状后增加「附图（来自知识库）」区块，按 URL 渲染 Markdown 图片。
- `orchestrator_controller`：OrchestratorSession 新增 `kb_image_urls`；`_ensure_collect`、`answer_questions` 接收并保存/合并图片 URL；draft 的 `system_current.image_urls` 写入该列表；返回的 draft_md 中追加附图区块。
- `confirmer_service._draft_to_markdown`：根据 `system_current.image_urls` 在系统现状下追加「附图（来自知识库）」并输出 `![附图](url)`。
- `editor_service.render_final`：根据 `system_current.image_urls` 在系统现状下追加「附图（来自知识库）」并输出 `![附图](url)`。

---

## [0.1.10] - 2026-02-12

### 后端

- 无接口/路由变更（仅版本号对齐）

### 服务

- **产品化表述由 BUILD_DRAFT 一次 LLM 调用统一生成**：草稿与终稿中的「产品化表述」不再用用户最后一句回答简单填充，改为在 `llm_build_draft_sections` 中与系统现状、改动点一并产出；模型输入为用户原话、知识库检索结果、澄清问题与用户答案，输出融合后的产品化表述（背景、目标、范围、约束）及可选的 demand_source。
- `llm_service.llm_build_draft_sections`：扩展 prompt 与返回 JSON，增加 `business_requirement.product_statement`、`business_requirement.demand_source`；要求模型综合多源信息生成产品化表述。
- `orchestrator_controller.answer_questions`：优先使用 LLM 返回的 product_statement、demand_source 写入 draft 与 requirement_structured；LLM 未返回或调用失败时回退为原逻辑（用户最后一答作 product_statement、用户原话作 demand_source）。

---

## [0.1.9] - 2026-02-12

### 前端

- 按 mockup 科技感布局落地生产环境：深色主题、顶栏渐变 logo、左侧窄轨 + 历史抽屉、全宽对话区、命令行风格输入（`>` 提示符）
- 历史会话改为抽屉形式，点击顶栏或左侧轨「≡」打开，选会话或点击遮罩关闭
- 消息展示增加时间戳（等宽字）、角色/时间 meta、用户/助手/确认提示左侧色条（绿/金/红）
- 引入 Inter + JetBrains Mono 字体，打印样式适配新布局

### 后端

- 无改动（仅版本号对齐）

---

## [0.1.8] - 2026-02-11

### 后端

- 会话与消息时间字段改为在插入/更新时使用 `datetime('now','localtime')` 显式写入本地时间，避免 SQLite `CURRENT_TIMESTAMP` 使用 UTC 导致前端展示非北京时间

---

## [0.1.7] - 2026-02-11

### 前端

- 历史会话侧栏新增「开启新对话」按钮，点击后清空当前聊天记录与输入框、解绑 `sessionId`，准备开启全新会话
- 点击「开启新对话」时取消当前历史会话高亮，仅通过后续请求刷新列表展示会话

### 后端

- 无改动（仅版本号对齐）

---

## [0.1.6] - 2026-02-11

### 前端

- 聊天输入区固定在视口底部，长内容时仅中间消息区域滚动，保证随时可输入
- 历史会话列表项展示首条用户问题文本，过长时通过 CSS 省略号截断
- 历史会话列表前端仅请求最近 10 条记录

### 后端

- `list_conversations` 增加 `first_user_text` 字段，返回每个会话首条用户消息内容
- 新增 `trim_old_conversations`，在创建新会话时自动删除更早的会话及其消息，仅保留最近 10 条记录

---

## [0.1.5] - 2026-02-11

### 前端

- 块分隔与引用样式：`hr` 分隔线、`blockquote` 引用块样式、内联 `code` 高亮
- 待澄清项样式：`待澄清项记录` 标题高亮、`details/summary` 折叠展示
- 打印/导出：新增“导出/打印”按钮，添加 `@media print` 规则隐藏侧栏与输入区
- Markdown 渲染增强：支持渲染 HTML（用于 `details`），新增 `rehype-raw`

### 后端

- 草稿/终稿排版：主章节之间插入 `---` 分隔线
- 最终文档待澄清项记录改为 `<details>` 折叠块

### 文档与规范

- README 后续迭代方向更新：导出为 PDF/Word 与模板化输出

---

## [0.1.4] - 2026-02-11

### 前端

- Mermaid 流程图渲染：新增 MermaidBlock 组件，支持 ```mermaid 代码块渲染为流程图/时序图等
- Markdown 表格、Mermaid 块样式补充

### 服务

- llm_service：llm_build_draft_sections prompt 增加表格与 Mermaid 引导：适合表格的用 Markdown 表格；流程/时序用 Mermaid 代码块；规则类保持段落

---

## [0.1.3] - 2026-02-11

### 前端

- Markdown 可读性增强：h1/h2/h3 标题分级字号与颜色（h1 加粗下划线、h2 左侧色条、h3 层级缩进）
- 重点内容样式：strong 加粗并蓝色高亮，表格表头浅灰底
- 图片预览灯箱：点击图片放大查看，点击背景或 Esc 关闭

### 服务

- llm_service：llm_build_draft_sections prompt 增加「关键结论用 **粗体** 标记」要求

---

## [0.1.2] - 2026-02-11

### 文档与规范

- CHANGELOG 记录规则：要求记录每次改动（含枚举、文案、配置等），按前端/后端/服务分块；版本 PATCH 用于任意改动
- Cursor 规则 `rs-agent-version-changelog.mdc` 更新，纳入 README 为发版同步文件；README 版本与样式描述同步

---

## [0.1.1] - 2026-02-11

### 前端

- UI 易读性优化：深色主题改为护眼亮色（主背景 `#f5f6f0`，主面板白底）
- 文案：标题「RS-Agent · 交易系统助手」→「RS-Agent」；副标题改为「支持业务查询 & 需求分析」
- 占位提示与输入框 placeholder 统一为「业务查询输入【查询知识库】，需求分析输入【系统改动点】」
- 样式：消息气泡、侧边栏、输入区浅色背景；表格与代码块适配亮色主题；错误提示与按钮 hover 态优化

---

## [0.1.0] - 2026-02-09

### 前端

- 新增独立 React + Vite + TypeScript 单页应用
- 单一入口对话式页面：文本输入框，统一调用 `/api/agent`
- 气泡式展示用户与助手消息，支持 Markdown 文本展示

### 后端

- 新增 FastAPI 应用，统一入口 `POST /api/agent`，`GET /api/version`
- intent_router：文本命中「查询知识库 / 交易规则」等 → KB_QUERY；否则 → ORCH_FLOW
- trading_kb_service：子进程调用 `run_all_sources.py`，解析 Markdown 与图片路径
- orchestrator_controller：简化版状态机，新会话固定 open_questions，用户回答后生成三板块需求分析草稿

### 已知问题

- Orchestrator 仅一轮提问 + 简易草稿，未对齐 DEFEND_CHECK / Editor 完整流程
- 会话状态存进程内存，服务重启后丢失

---

## [0.0.1] - 2026-02-08

### 前端

- 项目从零起步：创建 React + Vite + TypeScript 脚手架，基础入口与样式占位

### 后端

- 项目从零起步：创建 FastAPI 应用骨架，/health、/api 路由占位，config、db 模块初始化

### 文档与规范

- README、.env.example、端到端测试步骤 等文档占位；CHANGELOG 与版本号规范建立

