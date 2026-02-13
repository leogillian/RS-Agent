"""FastAPI application entrypoint for RS-Agent."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.db import init_db
from backend.routers import agent as agent_router
from backend.__version__ import __version__


app = FastAPI(title="RS-Agent Backend", version=__version__)

# 简单允许本地前端访问，后续可按需收紧
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):(517[3-9]|[0-9]{4})",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    Path(settings.images_output_dir_abs).mkdir(parents=True, exist_ok=True)


# KB 导出图片静态服务，前端通过 /api/kb-images/文件名 访问
app.mount("/api/kb-images", StaticFiles(directory=str(settings.images_output_dir_abs)), name="kb-images")


@app.get("/health")
def health() -> dict:
    """健康检查，含 LLM 配置诊断（不暴露 API Key）。"""
    llm_ok = bool(settings.llm_api_key) and bool(settings.llm_base_url)
    return {
        "status": "ok",
        "llm_configured": llm_ok,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url[:50] + "..." if (settings.llm_base_url and len(settings.llm_base_url) > 50) else (settings.llm_base_url or ""),
    }


app.include_router(agent_router.router, prefix="/api")

