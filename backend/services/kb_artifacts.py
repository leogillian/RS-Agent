"""Utilities to extract tables/images artifacts from KB markdown output.

We reuse trading-knowledge-base/scripts/run_all_sources.py output format, which includes:
- "=== 表格聚合视图 ..." section with Markdown tables
- "=== 图片 (images) ===" section with lines like: "path=/abs/file.pdf page=12"
"""

from __future__ import annotations

import re
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple


TABLE_SECTION_MARKER = "=== 表格聚合视图"
IMAGE_SECTION_MARKER = "=== 图片 (images) ==="


def extract_table_aggregate_markdown(kb_markdown: str) -> str:
    """Extract the "表格聚合视图" section from KB markdown.

    Returns:
      - Markdown text (may contain multiple tables) without the leading marker line,
      - or "" when not found.
    """
    text = (kb_markdown or "").strip()
    if not text:
        return ""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith(TABLE_SECTION_MARKER):
            start = i
            break
    if start is None:
        return ""
    # End at next "=== ..." marker (images/docs/etc) or end of text.
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip().startswith("=== ") and j != start:
            end = j
            break
    # Drop the marker header line itself to avoid duplicate headings in our doc.
    body = "\n".join(lines[start + 1 : end]).strip("\n").strip()
    return body


@dataclass(frozen=True)
class ImageRef:
    path: str
    page: str


def extract_image_refs(kb_markdown: str, max_refs: int = 20) -> List[ImageRef]:
    """Extract image references from KB markdown ("path=... page=...").

    Notes:
    - The KB output prints "path=<path> page=<page>" without quoting.
      We parse it by locating the last " page=" token to tolerate spaces in path.
    """
    text = (kb_markdown or "").strip()
    if not text:
        return []
    refs: List[ImageRef] = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("path=") or " page=" not in s:
            continue
        idx = s.rfind(" page=")
        path = s[len("path=") : idx].strip()
        page = s[idx + len(" page=") :].strip()
        if not path:
            continue
        refs.append(ImageRef(path=path, page=page or ""))
        if len(refs) >= max_refs:
            break
    return refs


def _safe_int(value: str, default: int = 1) -> int:
    try:
        return int(str(value).strip())
    except Exception:
        return default


def _extract_best_pdf_image_bytes(pdf_path: Path, page_no_1based: int) -> Tuple[Optional[bytes], str]:
    """Extract the largest image on a PDF page.

    Returns (bytes, ext) or (None, "png") when unavailable.
    """
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None, "png"

    try:
        doc = fitz.open(pdf_path)
        if page_no_1based < 1 or page_no_1based > len(doc):
            doc.close()
            return None, "png"
        page = doc[page_no_1based - 1]
        # full=True to get width/height info; fallback if not available.
        imgs = page.get_images(full=True) or page.get_images()
        doc.close()
        if not imgs:
            return None, "png"
        # Choose the image with largest area; tuple layout differs with full=True.
        def _area(t: tuple) -> int:
            try:
                # full=True: (xref, smask, width, height, bpc, colorspace, alt, name, filter)
                w = int(t[2])
                h = int(t[3])
                return w * h
            except Exception:
                return 0

        best = max(imgs, key=_area)
        xref = best[0]
        doc2 = fitz.open(pdf_path)
        base = doc2.extract_image(xref)
        doc2.close()
        raw = base.get("image")
        ext = (base.get("ext") or "png").lower()
        if not raw:
            return None, "png"
        return raw, ext or "png"
    except Exception:
        return None, "png"


def _extract_docx_image_bytes(docx_path: Path, image_index_1based: int) -> Tuple[Optional[bytes], str]:
    """Extract one image from a docx by index within word/media/ (sorted).

    This mirrors trading-knowledge-base behavior and is reliable when the KB returns
    a docx image index as `page`.
    """
    if image_index_1based < 1:
        image_index_1based = 1
    try:
        with zipfile.ZipFile(docx_path, "r") as z:
            media = sorted(
                [
                    n
                    for n in z.namelist()
                    if n.startswith("word/media/") and len(n) > len("word/media/")
                ]
            )
            if not media:
                return None, "png"
            if image_index_1based > len(media):
                image_index_1based = len(media)
            name = media[image_index_1based - 1]
            raw = z.read(name)
            ext = Path(name).suffix.lstrip(".").lower() or "png"
            return raw, ext
    except Exception:
        return None, "png"


def extract_best_images(
    refs: Iterable[ImageRef],
    out_dir: str | Path,
    max_images: int = 8,
) -> List[str]:
    """Try to extract better candidate images from referenced source docs.

    Strategy:
    - For PDF: extract the largest image on the referenced page (more likely the flowchart).
    - For DOCX: extract by referenced index (mirrors KB script).

    Returns absolute file paths saved under out_dir. On failure returns [].
    """
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    saved: List[str] = []
    seen_keys: set[tuple[str, str]] = set()
    batch = uuid.uuid4().hex[:10]

    for ref in refs:
        if len(saved) >= max_images:
            break
        key = (ref.path, ref.page)
        if key in seen_keys:
            continue
        seen_keys.add(key)

        src = Path(ref.path)
        if not src.is_file():
            continue
        page_no = _safe_int(ref.page, default=1)

        raw: Optional[bytes] = None
        ext = "png"
        suffix = src.suffix.lower()
        if suffix == ".pdf":
            raw, ext = _extract_best_pdf_image_bytes(src, page_no)
        elif suffix == ".docx":
            raw, ext = _extract_docx_image_bytes(src, page_no)
        else:
            continue

        if not raw:
            continue
        safe_ext = ext if ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp") else "png"
        name = f"kb_best_{batch}_{len(saved) + 1}.{safe_ext}"
        path = out / name
        try:
            path.write_bytes(raw)
        except Exception:
            continue
        saved.append(str(path))

    return saved

