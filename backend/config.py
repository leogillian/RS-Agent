"""Configuration helpers for RS-Agent backend."""

from __future__ import annotations

import os
from pathlib import Path

# 优先从 RS-Agent 根目录加载 .env，确保启动时能读取 LLM 等配置
_RS_AGENT_ROOT = Path(__file__).resolve().parent.parent
_env_file = _RS_AGENT_ROOT / ".env"
if _env_file.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_file)
    except ImportError:
        pass


class Settings:
    """Simple settings holder.

    In后续版本可以替换为 pydantic-settings，这里先保持依赖最小。
    """

    def __init__(self) -> None:
        # trading-knowledge-base 技能目录，优先读环境变量，其次按当前用户 ~/.cursor 路径推断
        kb_dir_env = os.environ.get("TRADING_KB_SKILL_DIR")
        if kb_dir_env:
            self.trading_kb_skill_dir = Path(kb_dir_env).expanduser()
        else:
            home = Path.home()
            self.trading_kb_skill_dir = (
                home / ".cursor" / "skills" / "trading-knowledge-base"
            )

        # run_all_sources.py 脚本路径
        self.run_all_sources_path = (
            self.trading_kb_skill_dir / "scripts" / "run_all_sources.py"
        )

        # 导出图片的默认目录（相对启动后端时的工作目录）
        self.images_output_dir = Path(
            os.environ.get("RS_AGENT_IMAGES_DIR", "rs_agent_kb_images")
        )

        # SQLite 数据库路径（默认放在 RS-Agent 根目录下的 data 子目录）
        base = Path(__file__).resolve().parent.parent
        db_path_env = os.environ.get("RS_AGENT_DB_PATH")
        if db_path_env:
            self.db_path = str(Path(db_path_env).expanduser())
        else:
            self.db_path = str(base / "data" / "rs_agent.db")

        # 用户上传图片目录（用于以图搜图等），返回绝对路径
        self.upload_dir = base / "data" / "uploads"
        # KB 导出图片目录，用于静态服务；统一为绝对路径
        self.images_output_dir_abs = (
            Path(self.images_output_dir).resolve()
            if self.images_output_dir.is_absolute()
            else (base / self.images_output_dir).resolve()
        )

        # ==== LLM（Qwen / OpenAI 兼容 API）配置 ====
        # API Key：优先级 LLM_API_KEY > DASHSCOPE_API_KEY > OPENAI_API_KEY
        self.llm_api_key = (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("DASHSCOPE_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        # 兼容旧变量名，便于平滑迁移
        self.dashscope_api_key = self.llm_api_key
        # OpenAI 兼容模式基址，按你提供的 URL 配置
        self.llm_base_url = os.environ.get(
            "RS_AGENT_LLM_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/")
        # 默认模型名；DashScope 兼容模式有效值为 qwen-turbo/qwen-plus/qwen-max 等
        self.llm_model = os.environ.get("RS_AGENT_LLM_MODEL", "qwen-plus")

        # ==== LLM 重试（P1-5）====
        # 最大重试次数（默认 3）
        self.llm_max_retries = int(os.environ.get("RS_AGENT_LLM_MAX_RETRIES", "3") or "3")
        # 重试最小等待秒数（默认 1）
        self.llm_retry_min_wait = float(os.environ.get("RS_AGENT_LLM_RETRY_MIN_WAIT", "1") or "1")
        # 重试最大等待秒数（默认 10）
        self.llm_retry_max_wait = float(os.environ.get("RS_AGENT_LLM_RETRY_MAX_WAIT", "10") or "10")

        # ==== KB_QUERY 方案 B（LLM 多 query 检索增强 + 综合输出）====
        self.kb_query_llm_enabled = os.environ.get("RS_AGENT_KB_QUERY_LLM_ENABLED", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        self.kb_query_max_subqueries = int(os.environ.get("RS_AGENT_KB_QUERY_MAX_SUBQUERIES", "4") or "4")
        self.kb_query_max_merged_chars = int(os.environ.get("RS_AGENT_KB_QUERY_MAX_MERGED_CHARS", "12000") or "12000")

        # ==== 会话超时与清理（P1-4）====
        # sessions 表中超过 TTL 的记录将被后台定时清理（秒，默认 7200 = 2h）
        self.session_ttl_seconds = int(os.environ.get("RS_AGENT_SESSION_TTL_SECONDS", "7200") or "7200")
        # 后台清理检查间隔（秒，默认 300 = 5min）
        self.session_cleanup_interval_seconds = int(
            os.environ.get("RS_AGENT_SESSION_CLEANUP_INTERVAL", "300") or "300"
        )

        # ==== API 认证 ====
        # 若设置了 RS_AGENT_API_KEY，则所有 /api/* 端点需要 Authorization: Bearer <key>
        # 留空或未设置则不启用认证（向后兼容）
        self.api_key = os.environ.get("RS_AGENT_API_KEY", "").strip()

        # ==== 文生图（流程图）配置：DashScope 万相 ====
        self.image_gen_enabled = os.environ.get("RS_AGENT_IMAGE_GEN_ENABLED", "true").lower() in ("true", "1", "yes")
        self.image_gen_url = os.environ.get(
            "RS_AGENT_IMAGE_GEN_URL",
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
        ).rstrip("/")
        self.image_gen_model = os.environ.get("RS_AGENT_IMAGE_GEN_MODEL", "wan2.6-t2i")


settings = Settings()

