"""Prompt template loader (P1-3).

Loads YAML prompt templates from the ``backend/prompts/`` directory and
provides a simple ``{variable}`` substitution interface.

Usage::

    from backend.prompts import load_prompt

    tpl = load_prompt("expand_kb_queries")
    system_msg = tpl.system(user_query="...", max_queries=4)
    user_msg   = tpl.user(user_query="...", max_queries=4)
"""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Dict

import yaml

_PROMPTS_DIR = Path(__file__).resolve().parent


class PromptTemplate:
    """A loaded prompt template with named sections (system, user, etc.)."""

    def __init__(self, data: Dict[str, str]) -> None:
        self._data = data

    def _render(self, key: str, **kwargs: object) -> str:
        tpl = self._data.get(key, "")
        if not tpl:
            return ""
        # Use str.format_map with a defaulting dict so missing keys are
        # left as-is (avoids KeyError on JSON braces escaped as {{ }}).
        return tpl.format_map(_SafeDict(kwargs))

    def system(self, **kwargs: object) -> str:
        return self._render("system", **kwargs)

    def user(self, **kwargs: object) -> str:
        return self._render("user", **kwargs)

    def get(self, key: str, **kwargs: object) -> str:
        return self._render(key, **kwargs)


class _SafeDict(dict):
    """dict subclass that returns the key placeholder for missing keys."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@functools.lru_cache(maxsize=None)
def load_prompt(name: str) -> PromptTemplate:
    """Load a YAML prompt template by name (without extension).

    Templates are cached after first load.
    """
    path = _PROMPTS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Prompt template must be a YAML mapping: {path}")
    return PromptTemplate(data)
