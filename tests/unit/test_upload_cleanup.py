"""单元测试：上传存储 _UPLOAD_STORE 按时间清理逻辑。"""

from __future__ import annotations

import time
from pathlib import Path
import pytest

from backend.routers import agent as agent_router


# 暴露模块级 store 与清理函数便于测试
_UPLOAD_STORE = agent_router._UPLOAD_STORE
_cleanup_old_uploads = agent_router._cleanup_old_uploads
UPLOAD_MAX_AGE_SECONDS = agent_router.UPLOAD_MAX_AGE_SECONDS


@pytest.fixture(autouse=True)
def reset_upload_store():
    """每个测试前清空 store，避免跨用例污染。"""
    _UPLOAD_STORE.clear()
    yield
    _UPLOAD_STORE.clear()


def test_cleanup_removes_old_entries(tmp_path: Path) -> None:
    """超过最大年龄的条目应被删除，且对应文件被删除。"""
    old_path = tmp_path / "old.png"
    old_path.write_bytes(b"x")
    _UPLOAD_STORE["old-uid"] = (str(old_path.resolve()), time.time() - UPLOAD_MAX_AGE_SECONDS - 1)

    _cleanup_old_uploads()

    assert "old-uid" not in _UPLOAD_STORE
    assert not old_path.exists()


def test_cleanup_keeps_recent_entries(tmp_path: Path) -> None:
    """未超过最大年龄的条目保留。"""
    recent_path = tmp_path / "recent.png"
    recent_path.write_bytes(b"y")
    _UPLOAD_STORE["recent-uid"] = (str(recent_path.resolve()), time.time())

    _cleanup_old_uploads()

    assert "recent-uid" in _UPLOAD_STORE
    assert _UPLOAD_STORE["recent-uid"][0] == str(recent_path.resolve())
    assert recent_path.exists()


def test_cleanup_handles_missing_file(tmp_path: Path) -> None:
    """磁盘文件已被删除时，仍能从 store 移除条目，不抛异常。"""
    missing_path = tmp_path / "missing.png"
    assert not missing_path.exists()
    _UPLOAD_STORE["missing-uid"] = (str(missing_path.resolve()), time.time() - UPLOAD_MAX_AGE_SECONDS - 1)

    _cleanup_old_uploads()

    assert "missing-uid" not in _UPLOAD_STORE


def test_resolve_image_paths_returns_path_strings() -> None:
    """_resolve_image_paths 返回路径字符串列表，且会触发清理。"""
    _UPLOAD_STORE["a"] = ("/fake/path/a.png", time.time())
    result = agent_router._resolve_image_paths(["a"])
    assert result == ["/fake/path/a.png"]
    # 清理后若 a 已过期会被删，这里未过期故仍在
    result2 = agent_router._resolve_image_paths(["a"])
    assert result2 == ["/fake/path/a.png"]
