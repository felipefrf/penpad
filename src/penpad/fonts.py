"""Font registry: bundled handwriting fonts, user fonts and cached glyph outlines."""

from __future__ import annotations

import os
from functools import lru_cache

from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")

# name -> file inside FONT_DIR (or an absolute path for user fonts)
FONTS = {
    "Cedarville": "Cedarville-Cursive.ttf",
    "Bad Script": "BadScript-Regular.ttf",
    "Handlee": "Handlee-Regular.ttf",
    "Caveat": "Caveat.ttf",
    "Gochi Hand": "GochiHand-Regular.ttf",
    "Walter Turncoat": "WalterTurncoat-Regular.ttf",
    "Virgil": "Virgil.woff2",
    "Mynerve": "Mynerve-Regular.ttf",
}

# Tried in order when the chosen font lacks a character. Mynerve covers Greek and
# most math symbols, so output never depends on fonts installed on the machine.
FALLBACKS = ("Virgil", "Mynerve")


def register_font(name: str, path: str) -> None:
    """Make a .ttf/.otf/.woff2 usable by name in styles (``font``, ``pinned``...)."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    FONTS[name] = os.path.abspath(path)
    _load.cache_clear()
    outline.cache_clear()


def font_path(name: str) -> str:
    return os.path.join(FONT_DIR, FONTS[name])


@lru_cache(maxsize=None)
def _load(name: str):
    f = TTFont(font_path(name), fontNumber=0, lazy=True)
    return f.getGlyphSet(), f.getBestCmap(), f["head"].unitsPerEm, f["hmtx"]


def has(name: str | None, ch: str) -> bool:
    return bool(name) and name in FONTS and ord(ch) in _load(name)[1]


def units_per_em(name: str) -> int:
    return _load(name)[2]


@lru_cache(maxsize=4096)
def outline(name: str, ch: str):
    """Recorded drawing operations and advance width of a glyph, or None."""
    gset, cmap, _, hmtx = _load(name)
    g = cmap.get(ord(ch))
    if g is None:
        return None
    pen = DecomposingRecordingPen(gset)
    gset[g].draw(pen)
    return tuple(pen.value), hmtx[g][0]


@lru_cache(maxsize=None)
def x_height(name: str) -> float:
    """Height of the lowercase x as a fraction of the em (measured, OS/2 values are unreliable)."""
    gset, cmap, upem, _ = _load(name)
    g = cmap.get(ord("x"))
    if g is None:
        return 0.5
    pen = BoundsPen(gset)
    gset[g].draw(pen)
    if not pen.bounds:
        return 0.5
    return max(0.2, (pen.bounds[3] - max(pen.bounds[1], 0)) / upem)


@lru_cache(maxsize=None)
def stroke_weight(name: str) -> float:
    """Estimated stroke thickness as a fraction of the em (for harmonizing borrowed glyphs)."""
    gset, cmap, upem, _ = _load(name)
    vals = []
    for ch in ("o", "a", "e", "n"):
        g = cmap.get(ord(ch))
        if g is None:
            continue
        ap = AreaPen(gset)
        bp = BoundsPen(gset)
        gset[g].draw(ap)
        gset[g].draw(bp)
        if bp.bounds:
            w = bp.bounds[2] - bp.bounds[0]
            h = bp.bounds[3] - bp.bounds[1]
            if w + h > 0:
                vals.append(abs(ap.value) / (2.2 * (w + h)) / upem)
    return sum(vals) / len(vals) if vals else 0.036


@lru_cache(maxsize=None)
def natural_slant(name: str) -> float:
    """Estimated built-in slant angle in degrees (positive leans right)."""
    gset, cmap, _, _ = _load(name)
    angles = []
    for ch in ("l", "I", "h", "t"):
        g = cmap.get(ord(ch))
        if g is None:
            continue
        pen = DecomposingRecordingPen(gset)
        gset[g].draw(pen)
        pts = [pt for _, args in pen.value for pt in args if pt is not None]
        if len(pts) < 4:
            continue
        ys = [p[1] for p in pts]
        ymin, ymax = min(ys), max(ys)
        span = ymax - ymin
        if span < 50:
            continue
        top = [p[0] for p in pts if p[1] >= ymin + 0.72 * span]
        bot = [p[0] for p in pts if p[1] <= ymin + 0.28 * span]
        if top and bot:
            dx = (sum(top) / len(top)) - (sum(bot) / len(bot))
            dy = 0.72 * span
            import math
            ang = math.degrees(math.atan2(dx, dy))
            if abs(ang) < 28.0:
                angles.append(ang)
    return sum(angles) / len(angles) if angles else 0.0


def resolve(name: str, ch: str) -> str | None:
    """First font in (name, *FALLBACKS) that has ch."""
    for f in (name,) + FALLBACKS:
        if has(f, ch):
            return f
    return None
