"""FastAPI application entrypoint for RS-Agent."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.db import cleanup_expired_sessions, init_db
from backend.routers import agent as agent_router
from backend.__version__ import __version__

logger = logging.getLogger(__name__)


async def _session_cleanup_loop() -> None:
    """P1-4: 后台定时清理超时会话。"""
    interval = max(30, settings.session_cleanup_interval_seconds)
    ttl = settings.session_ttl_seconds
    while True:
        await asyncio.sleep(interval)
        try:
            count = cleanup_expired_sessions(ttl)
            if count > 0:
                logger.info("Session cleanup: removed %d expired session(s) (TTL=%ds)", count, ttl)
        except Exception:
            logger.exception("Session cleanup error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时初始化 DB 与图片目录；后台运行会话清理任务。"""
    init_db()
    Path(settings.images_output_dir_abs).mkdir(parents=True, exist_ok=True)
    # P1-4: 启动后台清理任务
    cleanup_task = asyncio.create_task(_session_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()
        try:
            await cleanup_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="RS-Agent Backend", version=__version__, lifespan=lifespan)

# 统一错误响应：不暴露堆栈、路径、配置或密钥
def _safe_detail(exc: Exception) -> str:
    if hasattr(exc, "detail"):
        d = getattr(exc, "detail")
        if isinstance(d, str):
            return d
        if isinstance(d, list):
            return "Validation error"
    return "Internal server error"


@app.exception_handler(HTTPException)
async def http_exception_handler(_r: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": _safe_detail(exc)},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_r: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={"detail": "Validation error", "errors": exc.errors()},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(_r: Request, exc: Exception) -> JSONResponse:
    logging.exception("Unhandled error")
    return JSONResponse(
        status_code=500,
        content={"detail": _safe_detail(exc)},
    )


# 本地前端访问，methods/headers 收紧为实际使用列表
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_origin_regex=r"http://(127\.0\.0\.1|localhost):(517[3-9]|[0-9]{4})",
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "Accept"],
)


# KB 导出图片静态服务，前端通过 /api/kb-images/文件名 访问
app.mount("/api/kb-images", StaticFiles(directory=str(settings.images_output_dir_abs)), name="kb-images")


@app.get("/health")
async def health() -> dict:
    """健康检查，含 LLM 配置诊断（不暴露 API Key）。无需认证。"""
    llm_ok = bool(settings.llm_api_key) and bool(settings.llm_base_url)
    return {
        "status": "ok",
        "llm_configured": llm_ok,
        "llm_model": settings.llm_model,
        "llm_base_url": settings.llm_base_url[:50] + "..." if (settings.llm_base_url and len(settings.llm_base_url) > 50) else (settings.llm_base_url or ""),
    }


app.include_router(agent_router.router, prefix="/api")

