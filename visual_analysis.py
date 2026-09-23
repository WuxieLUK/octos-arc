"""Visual reference extraction for ARC requirements.

Requirement descriptions embed UI images either as Markdown
(``![image](./reference/foo.png)``) or in a ``visual_reference`` field.  The
main coding model is kept on text; this module sends those images to a
vision-capable model and returns style/layout analyses that are injected into
the regular prompt.  Analysis is cached under ``.arc`` so repeated/evolution
runs do not re-bill the same images.
"""
from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Callable
from urllib import error as urlerror
from urllib import request as urlrequest


VERSION = "arc-visual-style-v1"

ANALYSIS_PROMPT = """\
You extract frontend style requirements from an input UI image. Output
implementation-oriented frontend style requirements: page layout hierarchy,
section composition, component appearance, typography, colors, spacing, data
presentation patterns, and interaction surfaces.

Do NOT treat the image as business data. Do not copy screenshot-specific
records, names, phone numbers, emails, IDs, dates, prices, counts, table rows,
or chart values as future seeded content. Keep visible text only when it
defines page chrome or interaction structure, such as navigation labels,
section titles, field labels, button labels, tab names, or status categories.
Be concise and write in English."""


def _walk(node: dict):
    if not isinstance(node, dict):
        return
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _image_refs(node: dict) -> list[str]:
    seen: set[str] = set()
    refs: list[str] = []
    visual = node.get("visual_reference") or []
    if isinstance(visual, list):
        for item in visual:
            if isinstance(item, dict) and item.get("image_path"):
                refs.append(str(item["image_path"]).strip())
    description = str(node.get("description") or "")
    for match in re.finditer(r"!\[[^\]]*\]\(([^)]+)\)", description):
        refs.append(match.group(1).strip())
    out: list[str] = []
    for ref in refs:
        if ref and ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def _resolve(req_dir: Path, image_path: str) -> Path | None:
    image_path = image_path.strip()
    if not image_path or re.match(r"^[a-z]+://", image_path, re.I):
        return None
    path = Path(image_path)
    if path.is_absolute():
        return path
    return (req_dir / image_path).resolve()


def _load_cache(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _cache_key(full_path: Path, model: str) -> str:
    try:
        stat = full_path.stat()
        raw = f"{full_path}::{stat.st_mtime_ns}::{stat.st_size}::{model}::{VERSION}"
    except OSError:
        raw = f"{full_path}::{model}::{VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_content(message: dict) -> str:
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "\n".join(parts).strip()
    return ""


def _analyze_one(full_path: Path, base_url: str, api_key: str, model: str) -> str:
    mime = mimetypes.guess_type(str(full_path))[0] or "image/png"
    data_url = f"data:{mime};base64," + base64.b64encode(full_path.read_bytes()).decode("utf-8")
    body = json.dumps(
        {
            "model": model,
            "messages": [
                {"role": "system", "content": ANALYSIS_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Analyze this UI image."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ],
            "max_tokens": 1200,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urlrequest.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + api_key,
        },
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("vision response had no choices")
    text = _extract_content(choices[0].get("message") or {})
    if not text:
        raise RuntimeError("vision response had no text content")
    return text


def analyze_visual_references(
    tree: dict,
    req_dir: Path,
    cache_path: Path,
    base_url: str,
    api_key: str,
    model: str,
    *,
    only_nodes: set[str] | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, list[dict]]:
    """Return ``{node_id: [{"image_path", "analysis"}]}`` for text prompts."""
    if os.environ.get("OCTOS_ARC_VISUAL_ANALYSIS", "1") == "0":
        return {}
    if not (base_url and api_key and model):
        return {}
    cache = _load_cache(cache_path)
    updated = False
    attached: dict[str, list[dict]] = {}
    for node in _walk(tree):
        node_id = str(node.get("id") or "")
        if not node_id or (only_nodes is not None and node_id not in only_nodes):
            continue
        analyses: list[dict] = []
        for image_path in _image_refs(node):
            full_path = _resolve(req_dir, image_path)
            if full_path is None or not full_path.is_file():
                if log is not None:
                    log(f"[visual] image not found for {node_id}: {image_path}")
                continue
            key = _cache_key(full_path, model)
            entry = cache.get(key)
            if isinstance(entry, dict) and entry.get("analysis"):
                analysis = str(entry["analysis"])
            else:
                try:
                    if log is not None:
                        log(f"[visual] analyzing {image_path} for {node_id}")
                    analysis = _analyze_one(full_path, base_url, api_key, model)
                    cache[key] = {
                        "image_path": image_path,
                        "full_path": str(full_path),
                        "model": model,
                        "version": VERSION,
                        "analysis": analysis,
                    }
                    updated = True
                except (OSError, urlerror.URLError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
                    if log is not None:
                        log(f"[visual] analysis failed for {image_path}: {exc}")
                    continue
            analyses.append({"image_path": image_path, "analysis": analysis})
        if analyses:
            attached[node_id] = analyses
    if updated:
        _save_cache(cache_path, cache)
    return attached
