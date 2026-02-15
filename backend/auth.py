"""API Key authentication for RS-Agent (P0-4).

If ``RS_AGENT_API_KEY`` is set, all protected endpoints require the header::

    Authorization: Bearer <RS_AGENT_API_KEY>

If the env var is empty or unset, authentication is **disabled** (backward-compatible).
"""

from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.config import settings

# Use auto_error=False so we can return a custom 401 message when auth is enabled
_bearer_scheme = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> None:
    """FastAPI dependency: verify API key when authentication is enabled.

    Usage::

        @router.post("/agent", dependencies=[Depends(require_api_key)])
        async def agent_endpoint(...): ...
    """
    configured_key = settings.api_key
    if not configured_key:
        # Auth not configured → allow all requests
        return

    if credentials is None or credentials.credentials != configured_key:
        raise HTTPException(
            status_code=401,
            detail="未授权：请在请求头中提供有效的 Authorization: Bearer <API_KEY>",
            headers={"WWW-Authenticate": "Bearer"},
        )
