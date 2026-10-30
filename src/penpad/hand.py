"""The writer model: how one person's letters vary while staying recognizable.

Three layers of variation, from slow to fast:

1. **Writer state**: slant, rotation, size and baseline follow mean-reverting
   (Ornstein-Uhlenbeck) processes, so neighbouring letters lean and float
   together the way a real hand does, instead of every letter jittering on its own.
2. **Allographs**: each character has a small fixed repertoire of shapes
   (``allographs``) derived from the seed. The same writer always draws an "a" in
   one of, say, four ways and never repeats the same shape twice in a row.
3. **Instance noise**: every use redraws a fraction (``instance_noise``) of the
   outline deformation, so no two letters are pixel-identical.

Everything is clamped to keep letters legible, and everything is deterministic for
a given seed.
"""

from __future__ import annotations

import math
import random
import zlib

from fontTools.pens.svgPathPen import SVGPathPen

from . import fonts

DIGITS = frozenset("0123456789")
SPACES = {" ": 0.52, "\u2009": 0.17, "\u2005": 0.25, "\u2003": 1.0}   # widths in letter sizes
GREEK = range(0x0370, 0x0400)

# legibility limits, applied after all variation
MAX_ROTATION = 9.0    # degrees
MAX_SKEW = 14.0       # degrees around the writer's slant
MAX_SCALE = 0.18      # relative size change


def _stable_seed(*parts) -> int:
    return zlib.crc32("|".join(map(str, parts)).encode())


class _OU:
    """Discrete Ornstein-Uhlenbeck process with stationary deviation ``sigma``."""

    def __init__(self, rng, rho):
        self.rng, self.rho, self.x = rng, rho, 0.0

    def step(self, sigma):
        self.x = self.rho * self.x + math.sqrt(1 - self.rho ** 2) * self.rng.gauss(0, sigma)
        return self.x


def hand_line(x1, y1, x2, y2, r, roughness, bow):
    """SVG path of one pen pass between two points, with wobble and a slight arc."""
    length2 = (x1 - x2) ** 2 + (y1 - y2) ** 2
    length = math.sqrt(length2)
    off = 2.0 * roughness
    if off * off * 100 > length2:
        off = length / 10.0
    div = 0.2 + r.random() * 0.2
    mx = bow * 2.0 * (y2 - y1) / 200.0
    my = bow * 2.0 * (x1 - x2) / 200.0

    def d():
        return r.uniform(-off, off)

    return "M%.2f %.2f C%.2f %.2f %.2f %.2f %.2f %.2f" % (
        x1 + d(), y1 + d(),
        mx + x1 + (x2 - x1) * div + d(), my + y1 + (y2 - y1) * div + d(),
        mx + x1 + 2 * (x2 - x1) * div + d(), my + y1 + 2 * (y2 - y1) * div + d(),
        x2 + d(), y2 + d(),
    )


class Hand:
    """One writer. Keep a single Hand per page so the letters stay coherent."""

    def __init__(self, style: dict, seed: int = 1):
        self.style = style
        self.seed = seed
        self.rng = random.Random(seed)
        self._slant = _OU(self.rng, 0.9)
        self._rot = _OU(self.rng, 0.85)
        self._size = _OU(self.rng, 0.92)
        self._drift = _OU(self.rng, 0.93)
        self._tilt = _OU(self.rng, 0.6)
        self._last = {}
        self.glyphs = 0

    # ------------------------------------------------------------ font choice
    def font_for(self, s: str, i: int) -> str | None:
        """Font pinned for s[i], or None to pick between the main font and alternates."""
        e, ch = self.style, s[i]
        lone = ch.isascii() and ch.isalpha() and not (i > 0 and s[i - 1].isalpha()) \
            and not (i + 1 < len(s) and s[i + 1].isalpha())
        if lone and e.get("isolated_font"):
            return e.get("isolated", {}).get(ch, e["isolated_font"])
        if ch in e.get("pinned", {}):
            return e["pinned"][ch]
        if ord(ch) in GREEK and fonts.has(e.get("greek_font"), ch):
            return e["greek_font"]
        return None

    def _pick_font(self, s, i):
        e, ch = self.style, s[i]
        if ch in DIGITS:
            f = e["font"]
        else:
            f = self.font_for(s, i)
            if f is None:
                f = e["font"]
                pool = [a for a in e["alternates"] if fonts.has(a, ch)]
                if pool and fonts.has(f, ch) and self.rng.random() < e.get("alternate_rate", 0.25):
                    f = self.rng.choice(pool)
        return fonts.resolve(f, ch)

    # ------------------------------------------------------------ glyph shape
    def _variant(self, font, ch):
        """Pick one of the writer's allographs for ch, never the same one twice in a row."""
        k = self.style["digit_allographs" if ch in DIGITS else "allographs"]
        k = max(1, int(k))
        last = self._last.get((font, ch))
        v = self.rng.randrange(k)
        if k > 1 and v == last:
            v = (v + 1 + self.rng.randrange(k - 1)) % k
        self._last[(font, ch)] = v
        return random.Random(_stable_seed(self.seed, font, ch, v))

    def _outline(self, font, ch, vr, noise):
        """Glyph outline deformed by the allograph (vr) plus fresh instance noise."""
        rec = fonts.outline(font, ch)
        if rec is None:
            return None, 0
        ops, adv = rec
        upem = fonts.units_per_em(font)
        share = self.style["instance_noise"]
        r = self.rng
        a1 = upem * noise * vr.uniform(0.6, 1.4)
        a2 = upem * noise * vr.uniform(0.6, 1.4)
        w1, w2 = vr.uniform(1.2, 2.6), vr.uniform(1.2, 2.6)
        f1, f2 = vr.uniform(0, 6.28), vr.uniform(0, 6.28)
        # the instance part re-phases the waves a little and adds fine tremor
        f1 += r.gauss(0, 0.6 * share)
        f2 += r.gauss(0, 0.6 * share)
        fine = upem * noise * 0.12 * (0.5 + share)
        shift = [0.0, 0.0]

        def p(pt):
            x, y = pt
            return (x + a1 * math.sin(w1 * y / upem + f1) + shift[0] + r.gauss(0, fine),
                    y + a2 * math.sin(w2 * x / upem + f2) + shift[1] + r.gauss(0, fine))

        # SVGPathPen resolves TrueType implied on-curve points, including contours made
        # only of off-curve points (dots of i, j, period, colon) that have no moveTo
        pen = SVGPathPen(None, ntos=lambda v: "%.1f" % v)
        for op, args in ops:
            if op == "moveTo" or (op == "qCurveTo" and args and args[-1] is None):
                # each contour (pen lift) lands slightly off, mostly per allograph
                for j in (0, 1):
                    shift[j] = vr.gauss(0, upem * noise * 0.45) + r.gauss(0, upem * noise * 0.2 * share)
            getattr(pen, op)(*[None if a is None else p(a) for a in args])
        return pen.getCommands(), adv

    # ------------------------------------------------------------ writing
    def write(self, x, y, s, color, size=18, anchor="start"):
        """SVG of the handwritten string with baseline at y, and the pen advance."""
        e, r = self.style, self.rng
        fat = 1 + e.get("fatigue", 0) * min(1.0, self.glyphs / 3000)
        jit, loose = e["jitter"] * fat, e["looseness"]
        u = size / 24.0   # variation amplitudes were tuned at 24 px letters

        glyphs = []
        for i, ch in enumerate(s):
            if ch in SPACES:
                glyphs.append((ch, None, size * SPACES[ch], 1, None))
                continue
            font = self._pick_font(s, i)
            if font is None:
                glyphs.append((ch, None, size * 0.5, 1, None))
                continue
            vr = self._variant(font, ch)
            noise = e["glyph_noise"] * (e["digit_noise"] if ch in DIGITS else 1.0)
            d, adv = self._outline(font, ch, vr, noise)
            upem = fonts.units_per_em(font)
            scale = size / upem
            if font != e["font"] and e.get("match_x_height", True) and ch.islower():
                # a borrowed letter is drawn at the main font's x-height, as one hand would
                k = fonts.x_height(e["font"]) / fonts.x_height(font)
                scale *= max(0.75, min(1.35, k))
            glyphs.append((ch, d, (adv or upem * 0.5) * scale, scale, vr))

        total = sum(g[2] for g in glyphs) + 0.45 * jit * u * len(glyphs)
        cx = x - total / 2 if anchor == "middle" else (x - total if anchor == "end" else x)
        start = cx
        phase = r.uniform(0, 6.28)
        out = []
        for i, (ch, d, w, scale, vr) in enumerate(glyphs):
            if not d:
                cx += (w if ch in SPACES else max(w, size * 0.52)) + r.uniform(0.0, 1.6) * jit * u
                continue
            self.glyphs += 1
            # allograph bias + slow writer state + a little per-letter noise
            rot = vr.uniform(-2.5, 2.5) * jit + self._rot.step(2.2 * jit) + r.uniform(-1.5, 1.5) * jit
            skew = (e["slant"] + vr.uniform(-2.5, 2.5) * jit + self._slant.step(3.2 * jit)
                    + r.uniform(-2.0, 2.0) * jit)
            grow = vr.uniform(-0.05, 0.05) * jit + self._size.step(0.07 * jit) + r.uniform(-0.03, 0.03) * jit
            rot = max(-MAX_ROTATION, min(MAX_ROTATION, rot))
            skew = max(e["slant"] - MAX_SKEW, min(e["slant"] + MAX_SKEW, skew))
            grow = max(-MAX_SCALE, min(MAX_SCALE, grow))
            dy = (self._drift.step(2.4 * loose) + math.sin(phase + i * 0.8) * jit
                  + r.uniform(-1.0, 1.0) * jit) * u
            sx = scale * (1 + grow)
            sy = scale * (1 + grow + vr.uniform(-0.05, 0.05))
            c = _tone(color, r, e["tone_variation"] * loose) if e["tone_variation"] else color
            sw = r.uniform(0.3, 1.0) * e["pressure"] / max(sx, 1e-6)
            tr = "translate(%.2f %.2f) rotate(%.2f) skewX(%.2f) scale(%.5f %.5f)" % (cx, y + dy, rot, skew, sx, -sy)
            out.append('<path d="%s" fill="%s" stroke="%s" stroke-width="%.1f" stroke-linejoin="round" '
                       'transform="%s"/>' % (d, c, c, sw, tr))
            if ch == "7" and e.get("crossed_seven"):
                em = size / scale
                adv = w / sx
                bar = "M%.1f %.1f L%.1f %.1f" % (adv * 0.10, em * 0.315, adv * 0.88, em * 0.29)
                out.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.1f" stroke-linecap="round" '
                           'transform="%s"/>' % (bar, c, em * 0.045, tr))
            if e["retrace"] and ch not in DIGITS and r.random() < e["retrace"] * loose:
                tr2 = "translate(%.2f %.2f) rotate(%.2f) skewX(%.2f) scale(%.5f %.5f)" % (
                    cx + r.uniform(-0.8, 0.8), y + dy + r.uniform(-0.8, 0.8), rot + r.uniform(-2, 2), skew, sx, -sy)
                out.append('<path d="%s" fill="%s" opacity="0.75" transform="%s"/>' % (d, c, tr2))
            cx += w * (1 + grow * 0.5) + r.uniform(-0.5, 1.2) * jit * u
        tilt = self._tilt.step(1.0 * loose)
        return '<g transform="rotate(%.2f %.2f %.2f)">%s</g>' % (tilt, x, y, "".join(out)), cx - start


def _tone(color, r, k):
    color = color.lstrip("#")
    rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    f = 1 + r.uniform(-k, k)
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c * f))) for c in rgb)
