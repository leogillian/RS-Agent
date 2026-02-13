"""Service wrapper around trading-knowledge-base run_all_sources.py."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from backend.config import settings


class KBQueryError(RuntimeError):
    """Raised when knowledge base query fails."""


def query_kb(
    query: str,
    image_paths: Optional[List[str]] = None,
) -> Tuple[str, List[str]]:
    """Call trading-knowledge-base run_all_sources.py and return (markdown, images).

    - query: 用户自然语言问题
    - image_paths: 可选图片路径列表（目前简单策略：若传入非空，只取第一张作为 --query-image）

    返回:
      markdown_text: 脚本 stdout 中的主文本（不含 stderr 日志）
      image_paths: 从 stdout 中解析出的导出图片路径列表（若脚本按约定输出）
    """
    script_path = settings.run_all_sources_path
    if not script_path.is_file():
        raise KBQueryError(f"run_all_sources.py not found at {script_path}")

    # 使用当前解释器（避免系统无 python 命令，仅有 python3 的情况）
    cmd = [sys.executable, str(script_path)]
    if query:
        cmd += ["--query", query]

    # 简化策略：若有图片，仅用第一张作为 --query-image
    if image_paths:
        first = Path(image_paths[0])
        cmd += ["--query-image", str(first)]

    # 始终指定图片导出目录（绝对路径），便于静态服务与前端展示
    images_dir = getattr(settings, "images_output_dir_abs", None) or settings.images_output_dir
    cmd += ["--output-images-dir", str(images_dir)]

    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        raise KBQueryError(f"failed to start KB script: {exc!r}") from exc

    if proc.returncode != 0:
        raise KBQueryError(
            f"KB script exited with {proc.returncode}: {proc.stderr.strip()}"
        )

    stdout = proc.stdout or ""
    markdown_lines: list[str] = []
    exported_images: list[str] = []

    in_image_paths_block = False
    for line in stdout.splitlines():
        stripped = line.strip()
        # run_all_sources.py 在导出图片后会输出 "[图片路径]" 再列出路径
        if stripped == "[图片路径]":
            in_image_paths_block = True
            continue
        if in_image_paths_block:
            if not stripped:
                continue
            # 简单认为整行就是路径
            exported_images.append(stripped)
            continue
        markdown_lines.append(line)

    markdown_text = "\n".join(markdown_lines).strip()
    return markdown_text, exported_images

