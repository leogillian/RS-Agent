"""Pytest fixtures for RS-Agent tests. Run from RS-Agent root: python -m pytest tests/ -v."""

from __future__ import annotations

import sys
from pathlib import Path

# 保证从 RS-Agent 根目录运行时能 import backend
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
