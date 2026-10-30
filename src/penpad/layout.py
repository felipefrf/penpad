"""Math-aware line layout: text runs, fractions, scripts, integrals, roots, delimiters, boxes.

Every item knows its width and its height above (asc) and below (desc) the baseline,
and keeps its SVG drawn at the origin (baseline at y = 0).
"""

from __future__ import annotations

import math

from .hand import hand_line

DRAWN = frozenset("→⇒∇‖∮∫≠·≈")
ACCENTS = {"̂": "hat", "̇": "dot", "̈": "ddot", "⃗": "vec", "̄": "bar", "̃": "tilde"}
SHORT = set("acemnorsuvwxzıαεικνοπστυωηρμγ")   # letters without ascenders: accents sit lower
OPERATORS = {"=", ">", "<", "≠", "≈", "+", "−", "-", "~", "≤", "≥", "→", "⇒", "·", "×"}
ESCAPABLE = "\\_^{}"


class Item:
    def __init__(self):
        self.w, self.asc, self.desc = 0.0, 0.0, 0.0
        self.parts = []   # (color, svg)

    def at(self, x, y):
        return [(c, '<g transform="translate(%.2f %.2f)">%s</g>' % (x, y, s)) for c, s in self.parts]


def stroke(hand, pts, color, width, closed=False):
    if closed:
        pts = list(pts) + [pts[0]]
    d = " ".join(hand_line(a[0], a[1], b[0], b[1], hand.rng, 0.35, 0.25) for a, b in zip(pts, pts[1:]))
    return (color, '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                   'stroke-linecap="round" stroke-linejoin="round"/>' % (d, color, width))


def curve(d, color, width):
    return (color, '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" stroke-linecap="round"/>'
            % (d, color, width))


def _pen(hand, size):
    return hand.style["pen_width"] * size


class Text(Item):
    """Plain run of handwriting."""

    def __init__(self, hand, s, size, color):
        super().__init__()
        svg, w = hand.write(0, 0, s, color, size)
        self.parts.append((color, svg))
        self.w, self.asc, self.desc = w, 0.78 * size, 0.26 * size


class Symbol(Item):
    """Symbol drawn stroke by stroke (fonts rarely have a convincing one)."""

    def __init__(self, hand, ch, size, color):
        super().__init__()
        t, r = size, hand.rng
        e = _pen(hand, t)

        def j(v):
            return v + r.uniform(-0.02, 0.02) * t

        def P(x, y):
            return (j(x * t), j(y * t))

        p = self.parts
        if ch == "→":
            p.append(stroke(hand, [P(0.12, -0.3), P(0.9, -0.3)], color, e))
            p.append(stroke(hand, [P(0.72, -0.43), P(0.92, -0.3), P(0.72, -0.17)], color, e))
            self.w = 1.05 * t
        elif ch == "⇒":
            p.append(stroke(hand, [P(0.12, -0.21), P(0.85, -0.21)], color, e))
            p.append(stroke(hand, [P(0.12, -0.41), P(0.85, -0.41)], color, e))
            p.append(stroke(hand, [P(0.7, -0.55), P(0.98, -0.31), P(0.7, -0.07)], color, e))
            self.w = 1.12 * t
        elif ch == "∇":
            p.append(stroke(hand, [P(0.08, -0.7), P(0.74, -0.7), P(0.41, 0.0)], color, e, closed=True))
            self.w = 0.84 * t
        elif ch == "‖":
            p.append(stroke(hand, [P(0.12, -0.82), P(0.11, 0.18)], color, e))
            p.append(stroke(hand, [P(0.27, -0.82), P(0.26, 0.18)], color, e))
            self.w = 0.42 * t
        elif ch == "·":
            p.append((color, '<circle cx="%.1f" cy="%.1f" r="%.2f" fill="%s"/>' % (j(0.2 * t), j(-0.3 * t), 0.06 * t, color)))
            self.w = 0.4 * t
        elif ch == "≠":
            p.append(stroke(hand, [P(0.1, -0.22), P(0.6, -0.22)], color, e))
            p.append(stroke(hand, [P(0.1, -0.42), P(0.6, -0.42)], color, e))
            p.append(stroke(hand, [P(0.46, -0.64), P(0.24, 0.0)], color, e))
            self.w = 0.74 * t
        elif ch == "≈":
            for y in (-0.2, -0.42):
                d = "M%.1f %.1f Q%.1f %.1f %.1f %.1f T%.1f %.1f" % (
                    j(0.08 * t), j(y * t), j(0.2 * t), j((y - 0.12) * t), j(0.36 * t), j(y * t), j(0.64 * t), j(y * t))
                p.append(curve(d, color, e))
            self.w = 0.74 * t
        elif ch in "∫∮":
            d = "M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f L%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f" % (
                j(0.46 * t), j(-0.9 * t), j(0.33 * t), j(-1.05 * t), j(0.27 * t), j(-0.9 * t),
                j(0.27 * t), j(-0.6 * t), j(0.23 * t), j(0.05 * t),
                j(0.22 * t), j(0.3 * t), j(0.12 * t), j(0.36 * t), j(0.03 * t), j(0.22 * t))
            p.append(curve(d, color, e))
            if ch == "∮":
                cx, cy, rr = 0.25 * t, -0.33 * t, 0.15 * t
                pts = [(cx + rr * math.cos(a) + r.uniform(-0.6, 0.6), cy + rr * 1.1 * math.sin(a))
                       for a in [2 * math.pi * i / 10 for i in range(10)]]
                p.append(stroke(hand, pts, color, e * 0.8, closed=True))
            self.w = 0.55 * t
            self.asc, self.desc = 1.0 * t, 0.4 * t
            return
        self.asc, self.desc = 0.78 * t, 0.26 * t


class Accent(Item):
    """Letter with a math accent on top."""

    def __init__(self, hand, ch, marks, size, color):
        super().__init__()
        base = Text(hand, ch, size, color)
        self.parts = list(base.parts)
        self.w, self.asc, self.desc = base.w, base.asc, base.desc
        t, r, e = size, hand.rng, _pen(hand, size) * 0.85
        cx = base.w * 0.5
        top = -0.66 * t if ch in SHORT else -0.9 * t
        for m in marks:
            kind = ACCENTS[m]
            if kind == "hat":
                self.parts.append(stroke(hand, [(cx - 0.15 * t, top - 0.02 * t), (cx, top - 0.16 * t),
                                                (cx + 0.15 * t, top - 0.02 * t)], color, e))
            elif kind in ("dot", "ddot"):
                for x in ([cx] if kind == "dot" else [cx - 0.11 * t, cx + 0.11 * t]):
                    self.parts.append((color, '<circle cx="%.1f" cy="%.1f" r="%.2f" fill="%s"/>' % (
                        x + r.uniform(-0.5, 0.5), top - 0.08 * t, 0.055 * t, color)))
            elif kind == "vec":
                y = top - 0.16 * t
                self.parts.append(stroke(hand, [(cx - 0.22 * t, y), (cx + 0.22 * t, y)], color, e))
                self.parts.append(stroke(hand, [(cx + 0.1 * t, y - 0.08 * t), (cx + 0.23 * t, y),
                                                (cx + 0.1 * t, y + 0.08 * t)], color, e))
            elif kind == "bar":
                y = top - 0.1 * t
                self.parts.append(stroke(hand, [(cx - 0.2 * t, y), (cx + 0.2 * t, y)], color, e))
            elif kind == "tilde":
                y = top - 0.1 * t
                d = "M%.1f %.1f Q%.1f %.1f %.1f %.1f T%.1f %.1f" % (
                    cx - 0.2 * t, y, cx - 0.1 * t, y - 0.12 * t, cx, y, cx + 0.2 * t, y)
                self.parts.append(curve(d, color, e))
            self.asc = max(self.asc, -top + 0.2 * t)


class Row(Item):
    """Items side by side."""

    def __init__(self, items, gap=0.0):
        super().__init__()
        x = 0.0
        for it in items:
            self.parts += it.at(x, 0)
            x += it.w + gap
        self.items = items
        self.w = x
        self.asc = max([it.asc for it in items] or [0])
        self.desc = max([it.desc for it in items] or [0])


class Scripts(Item):
    """Subscript and/or superscript placed after the base."""

    def __init__(self, sub, sup, size, down=0.3, up=0.45):
        super().__init__()
        if sup is not None:
            self.parts += sup.at(0.1 * size, -up * size)
            self.w = max(self.w, sup.w + 0.12 * size)
            self.asc = up * size + sup.asc
        if sub is not None:
            self.parts += sub.at(0.02 * size, down * size)
            self.w = max(self.w, sub.w + 0.06 * size)
            self.desc = down * size + sub.desc


class Frac(Item):
    def __init__(self, hand, num, den, size, color):
        super().__init__()
        axis, gap, pad = -0.3 * size, 0.14 * size, 0.1 * size
        w = max(num.w, den.w) + 0.25 * size
        yn = axis - gap - num.desc
        yd = axis + gap + den.asc
        self.parts += num.at(pad + (w - num.w) / 2, yn)
        self.parts += den.at(pad + (w - den.w) / 2, yd)
        self.parts.append(stroke(hand, [(pad + 0.03 * size, axis), (pad + w - 0.03 * size, axis)],
                                 color, _pen(hand, size) * 0.9))
        self.w = w + 2 * pad
        self.asc = -yn + num.asc
        self.desc = yd + den.desc


class Root(Item):
    """Square root: tick, long stroke up, bar over the radicand."""

    def __init__(self, hand, inside, size, color):
        super().__init__()
        e = _pen(hand, size)
        top = -inside.asc - 0.12 * size
        bot = inside.desc + 0.02 * size
        lw = 0.55 * size
        r = hand.rng
        pts = [(0.02 * size, -0.3 * size), (0.14 * size, -0.36 * size), (0.3 * size, bot),
               (lw, top), (lw + inside.w + 0.12 * size, top + r.uniform(-0.6, 0.6))]
        self.parts.append(stroke(hand, pts, color, e))
        self.parts += inside.at(lw + 0.04 * size, 0)
        self.w = lw + inside.w + 0.2 * size
        self.asc, self.desc = -top + 0.05 * size, max(inside.desc, bot)


class Delim(Item):
    """Parentheses or brackets as tall as their content."""

    def __init__(self, hand, inside, size, color, kind="("):
        super().__init__()
        tall = inside.asc + inside.desc
        if tall <= 1.15 * size:
            o, c = ("(", ")") if kind == "(" else ("[", "]")
            a, f = Text(hand, o, size, color), Text(hand, c, size, color)
            self.parts += a.at(0, 0) + inside.at(a.w, 0) + f.at(a.w + inside.w, 0)
            self.w = a.w + inside.w + f.w
            self.asc, self.desc = max(a.asc, inside.asc), max(a.desc, inside.desc)
            return
        e = _pen(hand, size)
        top, bot = -inside.asc - 0.08 * size, inside.desc + 0.08 * size
        lw = 0.32 * size
        x2 = lw + inside.w + 0.05 * size
        if kind == "(":
            for x0, s in ((lw * 0.85, -1), (x2 + lw * 0.15, 1)):
                d = "M%.1f %.1f Q%.1f %.1f %.1f %.1f" % (x0, top, x0 + s * lw * 0.75, (top + bot) / 2, x0, bot)
                self.parts.append(curve(d, color, e))
        else:
            self.parts.append(stroke(hand, [(lw * 0.8, top), (lw * 0.2, top), (lw * 0.2, bot), (lw * 0.8, bot)], color, e))
            self.parts.append(stroke(hand, [(x2 + lw * 0.2, top), (x2 + lw * 0.8, top),
                                            (x2 + lw * 0.8, bot), (x2 + lw * 0.2, bot)], color, e))
        self.parts += inside.at(lw, 0)
        self.w = x2 + lw
        self.asc, self.desc = -top, bot


class Box(Item):
    def __init__(self, hand, inside, size, color):
        super().__init__()
        p = 0.3 * size
        x0, y0, x1, y1 = 0, -inside.asc - p, inside.w + 2 * p, inside.desc + p
        over = 0.12 * size
        e = _pen(hand, size) * 1.1
        for a, b in (((x0 - over, y0), (x1 + over, y0)), ((x1, y0 - over), (x1, y1 + over)),
                     ((x1 + over, y1), (x0 - over, y1)), ((x0, y1 + over), (x0, y0 - over))):
            self.parts.append(stroke(hand, [a, b], color, e))
        self.parts += inside.at(p, 0)
        self.w, self.asc, self.desc = x1, -y0, y1


# ---------------------------------------------------------------------------- parser
def _group(s, i):
    """Read {...} or one character (with its combining accents) starting at i."""
    if i < len(s) and s[i] == "{":
        n, j = 1, i + 1
        while j < len(s) and n:
            if s[j] == "\\" and j + 1 < len(s):
                j += 2
                continue
            n += {"{": 1, "}": -1}.get(s[j], 0)
            j += 1
        return s[i + 1:j - 1], j
    if i < len(s) and s[i] == "\\" and i + 1 < len(s) and s[i + 1] in ESCAPABLE:
        return s[i:i + 2], i + 2
    j = i + 1
    while j < len(s) and s[j] in ACCENTS:
        j += 1
    return s[i:j], j


def _runs(hand, s, size, color):
    """Literal text -> Text runs, with drawn symbols and accented letters split out."""
    items, run, i = [], "", 0
    while i < len(s):
        ch = s[i]
        marks = ""
        while i + 1 + len(marks) < len(s) and s[i + 1 + len(marks)] in ACCENTS:
            marks += s[i + 1 + len(marks)]
        if ch in DRAWN or marks:
            if run:
                items.append(Text(hand, run, size, color))
                run = ""
            items.append(Symbol(hand, ch, size, color) if ch in DRAWN and not marks
                         else Accent(hand, ch, marks, size, color))
            i += 1 + len(marks)
            continue
        run += ch
        i += 1
    if run:
        items.append(Text(hand, run, size, color))
    return items


def parse(hand, s, size, color):
    """penpad markup -> list of items."""
    items, buf, i = [], "", 0

    def flush():
        nonlocal buf
        if buf:
            items.extend(_runs(hand, buf, size, color))
            buf = ""

    def row(text, sz):
        return Row(parse(hand, text, sz, color))

    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in ESCAPABLE:
            buf += s[i + 1]
            i += 2
            continue
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in "fipqr":
            flush()
            cmd = s[i + 1]
            i += 2
            if cmd == "f":
                a, i = _group(s, i)
                b, i = _group(s, i)
                items.append(Frac(hand, row(a, size * 0.86), row(b, size * 0.86), size, color))
            elif cmd == "i":
                a, i = _group(s, i)
                b, i = _group(s, i)
                items.append(Symbol(hand, "∫", size * 1.45, color))
                items.append(Scripts(row(a, size * 0.66) if a else None, row(b, size * 0.66) if b else None,
                                     size, 0.5, 1.05))
            elif cmd == "r":
                a, i = _group(s, i)
                items.append(Root(hand, row(a, size), size, color))
            else:
                a, i = _group(s, i)
                items.append(Delim(hand, row(a, size), size, color, "(" if cmd == "p" else "["))
            continue
        if ch in "_^":
            flush()
            sub = sup = None
            while i < len(s) and s[i] in "_^":
                g, j = _group(s, i + 1)
                if s[i] == "_":
                    sub = row(g, size * 0.72)
                else:
                    sup = row(g, size * 0.72)
                i = j
            items.append(Scripts(sub, sup, size))
            continue
        buf += ch
        i += 1
    flush()
    return items


def words(s):
    """Split on spaces outside braces, keeping 'a = b' together so lines never break at an operator."""
    out, cur, depth, i = [], "", 0, 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            cur += s[i:i + 2]
            i += 2
            continue
        depth += {"{": 1, "}": -1}.get(ch, 0)
        if ch == " " and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    if cur:
        out.append(cur)
    k = 0
    while k < len(out):
        if out[k] in OPERATORS and 0 < k < len(out) - 1:
            out[k - 1:k + 2] = [out[k - 1] + " " + out[k] + " " + out[k + 1]]
            k -= 1
        else:
            k += 1
    return out
