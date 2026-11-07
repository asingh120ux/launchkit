"""Placeholder creatives: solid colour PNGs with the ad name on them, at the size in the filename."""

from __future__ import annotations

import hashlib
import os
import re

from PIL import Image, ImageDraw, ImageFont

SIZE_RE = re.compile(r"_(\d+)x(\d+)\.png$", re.IGNORECASE)


def placeholder_specs(plan: dict) -> list:
    """Every image file the plan references, as (relative path, label). First label wins."""
    specs = {}
    meta = plan.get("meta") or {}
    for name, ad in (meta.get("ads") or {}).items():
        f = (ad or {}).get("file")
        if isinstance(f, str) and f:
            specs.setdefault(f, name)
    for c in (plan.get("google") or {}).get("campaigns") or []:
        for ag in c.get("asset_groups") or []:
            images = ag.get("images") or {}
            for f in (images.get("landscape"), images.get("square"), ag.get("logo")):
                if isinstance(f, str) and f:
                    specs.setdefault(f, os.path.splitext(os.path.basename(f))[0])
    return list(specs.items())


def _colour(label: str) -> tuple:
    h = hashlib.md5(label.encode("utf-8")).digest()
    # keep it mid-tone so white text always reads
    return tuple(60 + b % 120 for b in h[:3])


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow older than 10.1
        return ImageFont.load_default()


def _fit_font(draw, text: str, max_width: int, start: int):
    size = start
    while size > 10:
        font = _font(size)
        left, _, right, _ = draw.textbbox((0, 0), text, font=font)
        if right - left <= max_width:
            return font
        size = int(size * 0.9)
    return _font(10)


def make_placeholder(path: str, label: str, width: int, height: int) -> None:
    img = Image.new("RGB", (width, height), _colour(label))
    draw = ImageDraw.Draw(img)
    lines = [label, f"{width}x{height} placeholder"]
    fonts = [_fit_font(draw, lines[0], int(width * 0.9), max(width // 14, 12)),
             _fit_font(draw, lines[1], int(width * 0.9), max(width // 30, 10))]
    boxes = [draw.textbbox((0, 0), t, font=f) for t, f in zip(lines, fonts)]
    heights = [b[3] - b[1] for b in boxes]
    gap = heights[0] // 2
    y = (height - sum(heights) - gap) // 2
    for text, font, box, h in zip(lines, fonts, boxes, heights):
        x = (width - (box[2] - box[0])) // 2 - box[0]
        draw.text((x, y - box[1]), text, fill=(255, 255, 255), font=font)
        y += h + gap
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.save(path, "PNG", optimize=True)


def make_placeholders(plan: dict, base_dir: str = ".") -> list:
    """Create every referenced PNG that does not exist yet. Returns the paths created.

    The size comes from the filename (..._1080x1350.png). Files without a size in the
    name are left alone, and validate() reports them as missing.
    """
    created = []
    for rel, label in placeholder_specs(plan):
        full = os.path.join(base_dir, rel)
        if os.path.exists(full):
            continue
        m = SIZE_RE.search(rel)
        if not m:
            continue
        make_placeholder(full, label, int(m.group(1)), int(m.group(2)))
        created.append(rel)
    return created
