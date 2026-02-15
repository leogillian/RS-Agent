# RS-Agent 项目说明（v0.3.0）

RS-Agent 是一个独立的前后端项目，通过单一入口为交易系统提供智能助手能力：

- 用户通过一个 Web 页面，以**文本 + 可选图片**描述需求；
- 后端根据意图自动在两条路径间路由：
  - **查询知识库（KB_QUERY）**：复用现有 `trading-knowledge-base` 能力，对接向量库 `/search`，回答规则/流程类问题；
  - **需求分析 Orchestrator（ORCH_FLOW）**：按「一、业务需求；二、系统现状；三、系统改动点」三板块，驱动多轮问答与草稿生成。

本项目不会修改现有 Cursor 的 `.cursor/rules/*` 与各个技能，仅作为旁路服务复用相同的知识库和向量库。

## 目录结构

- `backend/`：FastAPI 后端
  - `app.py`：应用入口，暴露 `/health` 与 `/api` 路由；
  - `routers/agent.py`：`/api/agent` 与 `/api/version` 接口（仅协议转换，业务逻辑委托给 service）；
  - `services/`：
    - `agent_pipeline.py`：**AgentPipeline** — stream / 非 stream 共用的统一业务逻辑管道（P0-1）；
    - `intent_router.py`：根据文本判断意图（KB_QUERY / ORCH_FLOW）；
    - `trading_kb_service.py`：封装 `trading-knowledge-base/scripts/run_all_sources.py` 调用；
    - `orchestrator_controller.py`：Orchestrator 状态机，会话通过 SQLite 持久化（P0-2）；
  - `config.py`：读取 trading-knowledge-base 技能目录、脚本与图片输出目录等配置；
  - `db.py`：SQLite 持久化（conversations、messages、sessions 表）；
  - `__version__.py`：当前后端版本号。
- `frontend/`：React + Vite + TypeScript 单页前端
  - `index.html`：入口 HTML；
  - `vite.config.ts`：开发服务器与 `/api` 代理配置；
  - `src/`：
    - `main.tsx`：挂载 React 应用；
    - `App.tsx`：单页面聊天式 UI，统一调用 `/api/agent`；
    - `api.ts`：前端对 `/api/agent` 的封装；
    - `style.css`：护眼亮色系对话风样式。

## 当前能力（v0.1.15）

### 后端

- `POST /api/agent`：
  - 新请求（无 `sessionId`）：
    - 若文本命中「查询知识库/交易规则」类关键词 → 走 KB_QUERY 路径：
      - 调用 `kb_query_enhanced.enhanced_kb_query`：LLM 扩展多 query → 多次 KB 检索合并去重（子进程执行 `run_all_sources.py`）→ LLM 综合输出（失败则回退原始 KB 合并结果）；
      - 返回 `payloadType="KB_ANSWER"`，携带 Markdown 文本与图片路径（并附带 `usedLLM/subQueries/rawMarkdown` 调试字段）。
    - 否则 → 走 ORCH_FLOW 路径：
      - 创建简化版 Orchestrator 会话，生成一组固定 `open_questions`；
      - 返回 `payloadType="OPEN_QUESTIONS"` 与 `sessionId`。
  - 已有会话（带 `sessionId`）：
    - 当前仅实现从 `ASK_QUESTIONS` → `DRAFT`：
      - 将用户回答与初始需求合并，生成一份极简的三板块需求分析草稿 Markdown；
      - 返回 `payloadType="DRAFT"`。

### 前端

- 单页面聊天式 UI：
  - 顶部展示标题与说明；
  - 中间为对话区，按气泡样式展示「你 / 助手」消息；
  - 底部输入区支持多行文本，回车发送（Shift+Enter 换行）。
- 统一调用 `/api/agent`：
  - 根据 `payloadType` 区分展示 KB 答案、open_questions 或草稿文本。

## LLM 配置（Qwen / DashScope）

KB_QUERY / ORCH_FLOW 路径均可能调用 LLM（KB_QUERY 用于检索增强与综合输出；ORCH_FLOW 用于 open_questions、草稿与用户反馈解析）。需配置：

| 环境变量 | 说明 | 示例 |
|---------|------|------|
| `DASHSCOPE_API_KEY` 或 `LLM_API_KEY` | API Key | `sk-xxx` |
| `RS_AGENT_LLM_BASE_URL` | OpenAI 兼容 API 基址 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `RS_AGENT_LLM_MODEL` | 模型名 | `qwen-plus` / `qwen-turbo` / `qwen-max` |

**推荐**：在 RS-Agent 根目录创建 `.env` 文件（可参考 `.env.example`），后端启动时会自动加载。

**诊断**：`GET /health` 会返回 `llm_configured`、`llm_model` 等，用于确认 LLM 是否生效。若未配置或调用失败，系统会静默回退到规则版逻辑，此时后端控制台会打印 `LLM ... 调用失败，已回退到...` 的警告。

## 运行单测/集成测

在 **RS-Agent 根目录**（本仓库根目录）执行，使用项目虚拟环境中的 Python/pip，避免系统未装或 PATH 未配置时报错：

```bash
./backend/.venv/bin/pip install -r backend/requirements-test.txt   # 首次需安装 pytest
./backend/.venv/bin/python -m pytest tests/ -v
```

或先激活虚拟环境：`source backend/.venv/bin/activate`，再执行 `pip install -r backend/requirements-test.txt`、`python -m pytest tests/ -v`。  
提交时自动跑测试：`pip install pre-commit && pre-commit install`（见 `.pre-commit-config.yaml`）。

## 端到端测试

从新会话到产出最终需求分析文档的完整步骤见：**[端到端测试步骤.md](./端到端测试步骤.md)**（含各回合请求示例与检查点）。

## 后续迭代方向

详见 **[roadmap.md](./roadmap.md)**，按 Phase 1（P0）→ Phase 2（P1）→ Phase 3 分阶段实施；某项完成后在 CHANGELOG 发版并从 roadmap 中删除该条。

