"""Font registry: bundled handwriting fonts, user fonts and cached glyph outlines."""

from __future__ import annotations

import os
from functools import lru_cache

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


def resolve(name: str, ch: str) -> str | None:
    """First font in (name, *FALLBACKS) that has ch."""
    for f in (name,) + FALLBACKS:
        if has(f, ch):
            return f
    return None
