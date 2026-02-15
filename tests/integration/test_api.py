"""集成测试：/health、/api 等接口及错误响应不泄露敏感信息。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app import app


client = TestClient(app)


def test_health_returns_ok() -> None:
    """GET /health 返回 200 且 status=ok。"""
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "ok"
    assert "llm_configured" in data
    # 不应暴露 API Key 等敏感信息
    raw = r.text
    assert "sk-" not in raw


def test_agent_post_empty_text_validation() -> None:
    """POST /api/agent 缺少或空 text 时返回 400，且 detail 安全。"""
    r = client.post("/api/agent", json={"text": ""})
    assert r.status_code == 400
    data = r.json()
    assert "detail" in data
    assert "traceback" not in str(data).lower()
    assert "api_key" not in str(data).lower()


def test_agent_post_invalid_body_returns_422() -> None:
    """POST /api/agent  body 缺少必填字段时返回 422，响应无堆栈。"""
    r = client.post("/api/agent", json={})
    assert r.status_code == 422
    data = r.json()
    assert "detail" in data
    assert "traceback" not in r.text.lower()
    assert "api_key" not in r.text.lower()


def test_version_returns_version() -> None:
    """GET /api/version 返回版本号。"""
    r = client.get("/api/version")
    assert r.status_code == 200
    data = r.json()
    assert "version" in data
    assert isinstance(data["version"], str)
