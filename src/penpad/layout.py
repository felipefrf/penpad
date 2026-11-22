"""Math-aware line layout: text runs, fractions, scripts, integrals, roots, delimiters, boxes.

Every item knows its width and its height above (asc) and below (desc) the baseline,
and keeps its SVG drawn at the origin (baseline at y = 0).
"""

from __future__ import annotations

import math
import random

from .hand import _stable_seed, hand_line

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


def _ribbon_from_pts(pts, width, rng, closed=False):
    """Sweep a sampled centerline into a tapered, variable-pressure filled SVG path."""
    if len(pts) < 2:
        return ""
    half = max(0.35, width * 0.5)
    n = len(pts)
    phase = rng.uniform(0, 6.28)
    top, bot = [], []
    for i, (x, y) in enumerate(pts):
        if i == 0:
            dx, dy = pts[1][0] - x, pts[1][1] - y
        elif i == n - 1:
            dx, dy = x - pts[-2][0], y - pts[-2][1]
        else:
            dx, dy = pts[i + 1][0] - pts[i - 1][0], pts[i + 1][1] - pts[i - 1][1]
        L = math.hypot(dx, dy)
        if L < 1e-6:
            nx, ny = 0.0, 1.0
        else:
            nx, ny = -dy / L, dx / L
        t = i / max(1, n - 1)
        taper = 1.0 if closed else (0.55 + 0.45 * math.sin(math.pi * min(1.0, max(0.0, t))) ** 0.45)
        press = 0.85 + 0.25 * math.sin(3.4 * t + phase)
        hw = half * taper * press
        top.append((x + nx * hw, y + ny * hw))
        bot.append((x - nx * hw, y - ny * hw))
    cmds = ["M%.1f %.1f" % top[0]]
    cmds += ["L%.1f %.1f" % p for p in top[1:]]
    cmds += ["L%.1f %.1f" % p for p in reversed(bot)]
    cmds.append("Z")
    return "".join(cmds)


def _sample_cubic(p0, p1, p2, p3, steps=8):
    out = []
    for k in range(steps + 1):
        t = k / steps
        mt = 1.0 - t
        out.append((
            mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0],
            mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1],
        ))
    return out


def _sample_quad(p0, p1, p2, steps=8):
    out = []
    for k in range(steps + 1):
        t = k / steps
        mt = 1.0 - t
        out.append((
            mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0],
            mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1],
        ))
    return out


def stroke(hand, pts, color, width, closed=False):
    """Variable-pressure filled hand-drawn stroke along a polyline."""
    r = hand.rng
    seq = list(pts) + ([pts[0]] if closed else [])
    sampled = []
    for idx, (a, b) in enumerate(zip(seq, seq[1:])):
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        off = min(0.7, length / 12.0)
        div = 0.24 + r.random() * 0.16
        mx = 0.25 * 2.0 * (b[1] - a[1]) / 200.0
        my = 0.25 * 2.0 * (a[0] - b[0]) / 200.0
        p0 = (a[0] + r.uniform(-off, off), a[1] + r.uniform(-off, off))
        c1 = (mx + a[0] + (b[0] - a[0]) * div + r.uniform(-off, off),
              my + a[1] + (b[1] - a[1]) * div + r.uniform(-off, off))
        c2 = (mx + a[0] + 2 * (b[0] - a[0]) * div + r.uniform(-off, off),
              my + a[1] + 2 * (b[1] - a[1]) * div + r.uniform(-off, off))
        p3 = (b[0] + r.uniform(-off, off), b[1] + r.uniform(-off, off))
        seg_pts = _sample_cubic(p0, c1, c2, p3, steps=max(4, min(10, int(length / 8) + 4)))
        sampled.extend(seg_pts if idx == 0 else seg_pts[1:])
    d = _ribbon_from_pts(sampled, width, r, closed=closed)
    return (color, '<path d="%s" fill="%s"/>' % (d, color))


def curve(d, color, width):
    return (color, '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" stroke-linecap="round"/>'
            % (d, color, width))


def _ribbon_path(pts, color, width, rng, closed=False):
    d = _ribbon_from_pts(pts, width, rng, closed=closed)
    return (color, '<path d="%s" fill="%s"/>' % (d, color))


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
    """Symbol drawn stroke by stroke with per-writer allographs and variable pen pressure."""

    def __init__(self, hand, ch, size, color):
        super().__init__()
        t, r = size, hand.rng
        e = _pen(hand, t) * r.uniform(0.92, 1.12)
        # Writer-specific allograph (3 variants per symbol) + instance jitter
        var = r.randrange(3)
        vr = random.Random(_stable_seed(hand.seed, "sym", ch, var))
        slant = vr.uniform(-0.08, 0.14) + r.uniform(-0.04, 0.04)

        def j(v, amp=0.025):
            return v + r.uniform(-amp, amp) * t

        def P(x, y):
            # Apply slight writer slant around midline (-0.3 * t)
            sx = x + slant * (-0.3 - y)
            return (j(sx * t), j(y * t))

        p = self.parts
        if ch == "→":
            ym = vr.uniform(-0.34, -0.26)
            bow = vr.uniform(-0.05, 0.05)
            end_x = vr.uniform(0.84, 0.96)
            shaft = _sample_quad(P(0.08, ym), P(0.48, ym + bow), P(end_x, ym + r.uniform(-0.03, 0.03)), steps=8)
            p.append(_ribbon_path(shaft, color, e, r))
            barb_len = vr.uniform(0.16, 0.25)
            spread = vr.uniform(0.11, 0.17)
            asym = vr.uniform(-0.03, 0.03)
            tip = P(end_x + 0.02, ym)
            if var == 0:
                p.append(stroke(hand, [P(end_x - barb_len, ym - spread), tip, P(end_x - barb_len + asym, ym + spread)], color, e))
            else:
                # Two quick flicks for top and bottom barbs
                p.append(_ribbon_path(_sample_quad(P(end_x - barb_len, ym - spread), P(end_x - barb_len * 0.4, ym - spread * 0.45), tip, 6), color, e * 0.95, r))
                p.append(_ribbon_path(_sample_quad(tip, P(end_x - barb_len * 0.45, ym + spread * 0.4), P(end_x - barb_len + asym, ym + spread), 6), color, e * 0.95, r))
            self.w = (end_x + 0.14) * t
        elif ch == "⇒":
            gap = vr.uniform(0.085, 0.115)
            ym = vr.uniform(-0.33, -0.28)
            end_x = vr.uniform(0.82, 0.90)
            p.append(stroke(hand, [P(0.10, ym + gap), P(end_x, ym + gap)], color, e * 0.95))
            p.append(stroke(hand, [P(0.10, ym - gap), P(end_x, ym - gap)], color, e * 0.95))
            head_x = end_x + vr.uniform(0.10, 0.16)
            sp = vr.uniform(0.21, 0.27)
            p.append(stroke(hand, [P(end_x - 0.16, ym - sp), P(head_x, ym), P(end_x - 0.18, ym + sp)], color, e))
            self.w = (head_x + 0.14) * t
        elif ch == "∇":
            w_top = vr.uniform(0.62, 0.74)
            y_top = vr.uniform(-0.74, -0.66)
            over = vr.uniform(0.0, 0.05) if var == 1 else 0.0
            a, b, c_pt = P(0.08, y_top), P(0.08 + w_top, y_top), P(0.08 + w_top * vr.uniform(0.45, 0.55), vr.uniform(-0.02, 0.04))
            if var == 2:
                # Two-stroke nabla (top bar + V)
                p.append(stroke(hand, [a, b], color, e))
                p.append(stroke(hand, [a, c_pt, b], color, e))
            else:
                p.append(stroke(hand, [a, (b[0] + over * t, b[1]), c_pt, a], color, e))
            self.w = (0.18 + w_top) * t
        elif ch == "‖":
            dx = vr.uniform(0.13, 0.18)
            p.append(stroke(hand, [P(0.12, -0.82), P(0.11, 0.18)], color, e))
            p.append(stroke(hand, [P(0.12 + dx, -0.82), P(0.11 + dx, 0.18)], color, e))
            self.w = (0.28 + dx) * t
        elif ch == "·":
            p.append((color, '<circle cx="%.1f" cy="%.1f" r="%.2f" fill="%s"/>' % (j(0.2 * t), j(-0.3 * t), 0.06 * t, color)))
            self.w = 0.4 * t
        elif ch == "≠":
            y1, y2 = vr.uniform(-0.24, -0.18), vr.uniform(-0.45, -0.39)
            w_bar = vr.uniform(0.46, 0.56)
            p.append(stroke(hand, [P(0.08, y1), P(0.08 + w_bar, y1 + r.uniform(-0.03, 0.03))], color, e))
            p.append(stroke(hand, [P(0.08, y2), P(0.08 + w_bar, y2 + r.uniform(-0.03, 0.03))], color, e))
            sx_top = vr.uniform(0.44, 0.54)
            sx_bot = vr.uniform(0.18, 0.28)
            p.append(stroke(hand, [P(sx_top, vr.uniform(-0.68, -0.58)), P(sx_bot, vr.uniform(-0.04, 0.05))], color, e))
            self.w = (0.22 + w_bar) * t
        elif ch == "≈":
            amp = vr.uniform(0.09, 0.15)
            w_w = vr.uniform(0.54, 0.64)
            for y in (vr.uniform(-0.22, -0.17), vr.uniform(-0.45, -0.39)):
                sgn = 1.0 if var != 2 else -1.0
                q1 = _sample_quad(P(0.06, y), P(0.06 + w_w * 0.26, y - sgn * amp), P(0.06 + w_w * 0.5, y), 6)
                q2 = _sample_quad(q1[-1], P(0.06 + w_w * 0.74, y + sgn * amp), P(0.06 + w_w, y + r.uniform(-0.02, 0.02)), 6)
                p.append(_ribbon_path(q1 + q2[1:], color, e, r))
            self.w = (0.14 + w_w) * t
        elif ch in "∫∮":
            curl_top = vr.uniform(0.14, 0.24)
            curl_bot = vr.uniform(0.14, 0.24)
            lean = vr.uniform(0.16, 0.28)
            top_pt = P(0.28 + lean, vr.uniform(-0.96, -0.84))
            mid_top = P(0.24 + lean * 0.35, -0.56)
            mid_bot = P(0.22 - lean * 0.15, 0.04)
            bot_pt = P(0.06 - lean * 0.2, vr.uniform(0.20, 0.30))
            s1 = _sample_cubic(top_pt, P(0.28 + lean - curl_top, -1.06), P(0.24 + lean * 0.3, -0.92), mid_top, 8)
            s2 = _sample_quad(mid_top, P(0.23 + vr.uniform(-0.04, 0.04), -0.26), mid_bot, 6)
            s3 = _sample_cubic(mid_bot, P(0.20 - lean * 0.1, 0.30), P(0.12 + curl_bot * 0.3, 0.38), bot_pt, 8)
            p.append(_ribbon_path(s1 + s2[1:] + s3[1:], color, e * 1.05, r))
            if ch == "∮":
                cx, cy = 0.25 * t, -0.32 * t
                rx, ry = vr.uniform(0.13, 0.18) * t, vr.uniform(0.15, 0.21) * t
                start_a = vr.uniform(0, 6.28)
                n_pts = 14
                pts = [(cx + rx * math.cos(start_a + 2 * math.pi * i / n_pts) + r.uniform(-0.5, 0.5),
                        cy + ry * math.sin(start_a + 2 * math.pi * i / n_pts) + r.uniform(-0.5, 0.5))
                       for i in range(n_pts + 2)]   # slight natural overshoot at closure
                p.append(_ribbon_path(pts, color, e * 0.85, r, closed=False))
            self.w = (0.48 + lean * 0.45) * t
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
        self.gap = gap
        x = 0.0
        for it in items:
            self.parts += it.at(x, 0)
            x += it.w + gap
        self.items = items
        self.w = x
        self.asc = max([it.asc for it in items] or [0])
        self.desc = max([it.desc for it in items] or [0])

    def progressive_squeeze(self, room, rng=None, min_scale=0.58):
        """Keep the start of a long line at full size and progressively squeeze the tail,
        the way a human compresses the end of a line when running out of paper."""
        if self.w <= room or len(self.items) < 2:
            return self
        t0 = (rng.uniform(0.36, 0.48) if rng else 0.42)
        total = self.w

        def eval_layout(alpha, g_scale=1.0):
            x = 0.0
            orig_x = 0.0
            placed = []
            for it in self.items:
                if orig_x <= t0 * total:
                    u = 0.0
                else:
                    u = min(1.0, (orig_x - t0 * total) / max(1e-6, (1.0 - t0) * total))
                p = u ** 1.25
                sx = max(min_scale * 0.88, (1.0 - alpha * p) * g_scale)
                sy = max(min_scale, (1.0 - 0.75 * alpha * p) * g_scale)
                placed.append((it, x, sx, sy))
                x += (it.w + self.gap) * sx
                orig_x += it.w + self.gap
            return x, placed

        # Solve tail compression alpha via binary search
        lo, hi = 0.0, 1.0
        for _ in range(14):
            mid = 0.5 * (lo + hi)
            if eval_layout(mid)[0] > room:
                lo = mid
            else:
                hi = mid
        w_tail, placed = eval_layout(hi)
        if w_tail > room:
            # Extremely long line: combine tail squeeze with a gentle overall scale
            g = max(min_scale, (room / w_tail) * 0.98)
            w_tail, placed = eval_layout(hi, g_scale=g)

        out = Item()
        for it, ix, sx, sy in placed:
            if abs(sx - 1.0) < 1e-3 and abs(sy - 1.0) < 1e-3:
                out.parts += it.at(ix, 0)
            else:
                for c, s in it.parts:
                    out.parts.append((c, '<g transform="translate(%.2f 0) scale(%.4f %.4f)">%s</g>' % (ix, sx, sy, s)))
            out.asc = max(out.asc, it.asc * sy)
            out.desc = max(out.desc, it.desc * sy)
        out.w = w_tail
        return out


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
    """Square root: tick, long stroke up, bar over the radicand, with writer variation."""

    def __init__(self, hand, inside, size, color):
        super().__init__()
        r = hand.rng
        vr = random.Random(_stable_seed(hand.seed, "sym", "√", r.randrange(3)))
        e = _pen(hand, size) * r.uniform(0.92, 1.1)
        top = -inside.asc - vr.uniform(0.09, 0.16) * size
        bot = inside.desc + vr.uniform(-0.02, 0.06) * size
        lw = vr.uniform(0.48, 0.60) * size
        roof_droop = vr.uniform(-0.04, 0.05) * size + r.uniform(-0.7, 0.7)
        over = vr.uniform(0.08, 0.20) * size
        pts = [
            (vr.uniform(0.0, 0.05) * size, vr.uniform(-0.34, -0.25) * size),
            (vr.uniform(0.11, 0.18) * size, vr.uniform(-0.42, -0.32) * size),
            (vr.uniform(0.26, 0.35) * size, bot),
            (lw, top),
            (lw + inside.w + over, top + roof_droop),
        ]
        self.parts.append(stroke(hand, pts, color, e))
        self.parts += inside.at(lw + 0.04 * size, 0)
        self.w = lw + inside.w + over + 0.08 * size
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


class Highlight(Item):
    """Translucent vector highlighter stroke drawn behind the ink."""

    def __init__(self, hand, inside, size, color="#ffeb64", opacity=0.52):
        super().__init__()
        r = hand.rng
        pad = 0.14 * size
        y_mid = (-inside.asc + inside.desc) * 0.42
        h = max(0.82 * size, (inside.asc + inside.desc) * 0.78)
        dy1 = r.uniform(-0.04, 0.04) * size
        dy2 = r.uniform(-0.04, 0.04) * size
        d = "M%.2f %.2f L%.2f %.2f" % (-pad, y_mid + dy1, inside.w + pad, y_mid + dy2)
        hl_svg = (
            '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
            'stroke-linecap="round" opacity="%.2f"/>' % (d, color, h, opacity)
        )
        self.parts.append(("__highlight__", hl_svg))
        self.parts += inside.at(0, 0)
        self.w, self.asc, self.desc = inside.w, inside.asc, inside.desc


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
        if ch == " " and run:
            run += ch
            items.append(Text(hand, run, size, color))
            run = ""
            i += 1
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
        if ch == "\\" and i + 1 < len(s) and s[i + 1] in "fipqrh":
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
            elif cmd == "h":
                a, i = _group(s, i)
                items.append(Highlight(hand, row(a, size), size))
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
