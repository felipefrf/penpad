"""Handwriting styles (pen + letterforms) and ink colors.

A style is a plain dict, so any key can be overridden per call:
``Notebook(style="cursive", jitter=0.9, slant=6)``.
"""

from __future__ import annotations

# Goodnotes pen colors measured on exported pages.
INKS = {
    "black": "#1e1b1b",
    "blue": "#53aaff",
    "red": "#f33f3f",
    "purple": "#6f44a3",
    "green": "#2f9e44",
    "gray": "#adb5bd",
}

UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

CURSIVE = {
    # -- strokes drawn by penpad (lines, arrows, fraction bars) --
    "roughness": 0.9,         # how much a ruled line wobbles
    "bow": 0.6,               # how much a line arcs in the middle
    "stroke_width": 1.7,      # default line width, px on a 900 px wide page
    "pen_width": 0.07,        # drawn symbols (→ ∇ √ ...), fraction of the letter size
    # -- letterforms --
    "font": "Cedarville",     # main handwriting font
    "alternates": [],         # other fonts the hand borrows a letter from now and then
    "alternate_rate": 0.25,   # chance of borrowing, per letter
    "match_x_height": True,   # borrowed lowercase letters take the main font's x-height
    # Letters that are ambiguous in the main font are written with another one.
    # In Cedarville p looks like ρ, w like ω, b like ℓ, l like e, and the capitals are
    # too ornate (Z reads as 3, H as #).
    # Its comma also reads as a subscript 7.
    # Greek comes from Virgil (clear α ζ ω), except ρ, which reads as p there.
    "pinned": dict({c: "Bad Script" for c in UPPER + "pwbl,;"}, ρ="Mynerve"),
    "isolated_font": "Bad Script",   # single letters in formulas (s, t, x) in print style
    "isolated": {"b": "Handlee", "C": "Handlee"},   # lone b reads as 6, lone C as (
    "greek_font": "Virgil",
    "crossed_seven": False,
    # -- variation model (see hand.py) --
    "allographs": 4,          # distinct shapes the hand keeps for each letter
    "digit_allographs": 3,
    "glyph_noise": 0.012,     # outline deformation, fraction of the em
    "digit_noise": 3.6,       # digits deform more than letters in this font
    "instance_noise": 0.35,   # share of the deformation redrawn on every use
    "jitter": 0.7,            # rotation, slant, size and baseline wobble
    "looseness": 0.6,         # baseline drift, word tilt
    "slant": 0.0,             # mean slant in degrees (positive leans right)
    "fatigue": 0.0,           # extra jitter towards the end of long documents
    "pressure": 0.6,          # extra outline stroke, thickens the font
    "tone_variation": 0.0,    # Goodnotes ink is flat
    "retrace": 0.0,           # chance of going over a letter twice
    # -- ink --
    "ink_softness": 0.3,      # edge blur radius at 225 dpi
    "ink_gain": 1.3,
    # -- render --
    "supersample": 2.0,
}

PRINT = dict(
    CURSIVE,
    roughness=1.9, bow=1.1, stroke_width=2.6, pen_width=0.085,
    font="Handlee", alternates=[],
    # Caveat b looks like 6, cursive w like ω, Handlee p like ρ and u like υ
    pinned={"b": "Virgil", "w": "Bad Script", "W": "Walter Turncoat", "p": "Gochi Hand", "u": "Gochi Hand",
            "ρ": "Mynerve"},
    isolated_font=None, isolated={},
    glyph_noise=0.020, digit_noise=1.0, jitter=0.9, looseness=0.7,
    pressure=0.45, tone_variation=0.18, retrace=0.13, ink_softness=0.6, ink_gain=1.7,
)

CASUAL = dict(
    PRINT,
    roughness=1.2, stroke_width=2.0, pen_width=0.075,
    font="Caveat", alternates=[],
    pinned={"b": "Virgil", "w": "Bad Script", "ρ": "Mynerve"},
    glyph_noise=0.016, jitter=0.8, looseness=0.6, slant=3.0,
    pressure=0.35, tone_variation=0.0, retrace=0.0, ink_softness=0.35, ink_gain=1.4,
)

STYLES = {"cursive": CURSIVE, "print": PRINT, "casual": CASUAL}


def get_style(style=None, **overrides) -> dict:
    """Resolve a preset name or dict and apply overrides."""
    if style is None:
        base = CURSIVE
    elif isinstance(style, str):
        if style not in STYLES:
            raise ValueError("unknown style %r, choose from %s" % (style, ", ".join(STYLES)))
        base = STYLES[style]
    else:
        base = style
    return dict(base, **overrides)


def ink(color) -> str:
    """Ink name ('blue') or any hex color ('#123456')."""
    if color is None:
        return INKS["black"]
    if color in INKS:
        return INKS[color]
    if isinstance(color, str) and color.startswith("#") and len(color) in (4, 7):
        return color
    raise ValueError("unknown ink %r: use %s or a #hex color" % (color, ", ".join(INKS)))
