"""The writer model: how one person's letters vary while staying recognizable.

Three layers of variation, from slow to fast:

1. **Writer state**: slant, rotation, size, baseline and pen pressure follow
   mean-reverting (Ornstein-Uhlenbeck) processes, so neighbouring letters lean,
   float and vary in weight together the way a real hand does.
2. **Allographs**: each character has a small fixed repertoire of shapes
   (``allographs``) derived from the seed. The same writer always draws an "a" in
   one of, say, four ways and never repeats the same shape twice in a row.
3. **Instance noise**: every use redraws a fraction (``instance_noise``) of the
   outline deformation, so no two letters are pixel-identical.

Consecutive lowercase letters within a word are grouped into natural pen-stroke
runs (syllables), linked by tapered variable-width connecting strokes and merged
into single continuous filled paths in page coordinates (no per-letter transforms
or stroked font outlines). Everything is deterministic for a given seed.
"""

from __future__ import annotations

import math
import random
import zlib

try:
    import pathops as _pathops
except ImportError:  # pragma: no cover
    _pathops = None

from . import fonts

DIGITS = frozenset("0123456789")
SPACES = {" ": 0.52, "\u2009": 0.17, "\u2005": 0.25, "\u2003": 1.0}   # widths in letter sizes
GREEK = range(0x0370, 0x0400)
_HIGH_EXIT = frozenset("ovwrb")
_LIFT_AFTER = frozenset("xzpbfs")

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


# ------------------------------------------------------- 2D affine & contours
def _mat_mul(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _letter_matrix(tx, ty, rot_deg, skew_deg, sx, sy):
    rad = math.radians(rot_deg)
    cs, sn = math.cos(rad), math.sin(rad)
    tk = math.tan(math.radians(skew_deg))
    # translate(tx, ty) * rotate(rot) * skewX(skew) * scale(sx, sy)
    a = cs * sx
    b = sn * sx
    c = (cs * tk - sn) * sy
    d = (sn * tk + cs) * sy
    return (a, b, c, d, tx, ty)


def _rotate_around(deg, cx, cy):
    rad = math.radians(deg)
    cs, sn = math.cos(rad), math.sin(rad)
    return (cs, sn, -sn, cs, cx - cs * cx + sn * cy, cy - sn * cx - cs * cy)


def _xform_pt(m, pt):
    a, b, c, d, e, f = m
    x, y = pt
    return (a * x + c * y + e, b * x + d * y + f)


def _mid(p1, p2):
    return (0.5 * (p1[0] + p2[0]), 0.5 * (p1[1] + p2[1]))


def _extract_contours(ops, deform_pt, shift_fn):
    """Convert fontTools recording ops into explicit closed segment contours."""
    contours = []
    segs = []
    start = None
    cur = None

    def flush():
        nonlocal segs, start, cur
        if segs:
            if start is not None and cur is not None:
                if abs(cur[0] - start[0]) > 1e-4 or abs(cur[1] - start[1]) > 1e-4:
                    segs.append(("L", cur, start))
            contours.append(segs)
        segs = []
        start = None
        cur = None

    for op, args in ops:
        if op == "moveTo":
            flush()
            shift_fn()
            p0 = deform_pt(args[0])
            start = cur = p0
        elif op == "lineTo":
            if cur is None:
                continue
            p1 = deform_pt(args[0])
            if abs(p1[0] - cur[0]) > 1e-4 or abs(p1[1] - cur[1]) > 1e-4:
                segs.append(("L", cur, p1))
                cur = p1
        elif op == "curveTo":
            if cur is None:
                continue
            p1, p2, p3 = deform_pt(args[0]), deform_pt(args[1]), deform_pt(args[2])
            segs.append(("C", cur, p1, p2, p3))
            cur = p3
        elif op == "qCurveTo":
            if not args:
                continue
            if args[-1] is None:
                # All-off-curve closed TrueType contour (e.g., dots of i, j, period, colon)
                flush()
                shift_fn()
                pts = [deform_pt(pt) for pt in args[:-1]]
                n = len(pts)
                if n >= 2:
                    p_prev = _mid(pts[-1], pts[0])
                    start = p_prev
                    for k in range(n):
                        c = pts[k]
                        p_next = _mid(pts[k], pts[(k + 1) % n])
                        segs.append(("Q", p_prev, c, p_next))
                        p_prev = p_next
                    cur = p_prev
                    flush()
            else:
                if cur is None:
                    continue
                pts = [deform_pt(pt) for pt in args]
                if len(pts) == 1:
                    p1 = pts[0]
                    if abs(p1[0] - cur[0]) > 1e-4 or abs(p1[1] - cur[1]) > 1e-4:
                        segs.append(("L", cur, p1))
                        cur = p1
                else:
                    for k in range(len(pts) - 2):
                        c = pts[k]
                        on = _mid(pts[k], pts[k + 1])
                        segs.append(("Q", cur, c, on))
                        cur = on
                    segs.append(("Q", cur, pts[-2], pts[-1]))
                    cur = pts[-1]
        elif op in ("closePath", "endPath"):
            flush()
    flush()
    return contours


def _transform_contours(contours, mat):
    out = []
    for segs in contours:
        t_segs = []
        for s in segs:
            kind = s[0]
            if kind == "L":
                t_segs.append(("L", _xform_pt(mat, s[1]), _xform_pt(mat, s[2])))
            elif kind == "Q":
                t_segs.append(("Q", _xform_pt(mat, s[1]), _xform_pt(mat, s[2]), _xform_pt(mat, s[3])))
            else:
                t_segs.append(("C", _xform_pt(mat, s[1]), _xform_pt(mat, s[2]),
                               _xform_pt(mat, s[3]), _xform_pt(mat, s[4])))
        out.append(t_segs)
    return out


def _unit_vec(dx, dy):
    L = math.hypot(dx, dy)
    return (dx / L, dy / L) if L > 1e-9 else None


def _seg_tangents(seg):
    kind = seg[0]
    if kind == "L":
        t = _unit_vec(seg[2][0] - seg[1][0], seg[2][1] - seg[1][1])
        return t, t
    if kind == "Q":
        p0, c, p1 = seg[1], seg[2], seg[3]
        t0 = _unit_vec(c[0] - p0[0], c[1] - p0[1]) or _unit_vec(p1[0] - p0[0], p1[1] - p0[1])
        t1 = _unit_vec(p1[0] - c[0], p1[1] - c[1]) or _unit_vec(p1[0] - p0[0], p1[1] - p0[1])
        return t0, t1
    p0, c1, c2, p1 = seg[1], seg[2], seg[3], seg[4]
    t0 = (_unit_vec(c1[0] - p0[0], c1[1] - p0[1])
          or _unit_vec(c2[0] - p0[0], c2[1] - p0[1])
          or _unit_vec(p1[0] - p0[0], p1[1] - p0[1]))
    t1 = (_unit_vec(p1[0] - c2[0], p1[1] - c2[1])
          or _unit_vec(p1[0] - c1[0], p1[1] - c1[1])
          or _unit_vec(p1[0] - p0[0], p1[1] - p0[1]))
    return t0, t1


def _contour_signed_area(segs):
    pts = []
    for s in segs:
        kind = s[0]
        pts.append(s[1])
        if kind == "Q":
            p0, c, p1 = s[1], s[2], s[3]
            pts.append((0.25 * p0[0] + 0.5 * c[0] + 0.25 * p1[0],
                        0.25 * p0[1] + 0.5 * c[1] + 0.25 * p1[1]))
        elif kind == "C":
            p0, c1, c2, p1 = s[1], s[2], s[3], s[4]
            pts.append((0.125 * p0[0] + 0.375 * c1[0] + 0.375 * c2[0] + 0.125 * p1[0],
                        0.125 * p0[1] + 0.375 * c1[1] + 0.375 * c2[1] + 0.125 * p1[1]))
    n = len(pts)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return 0.5 * area


def _expand_pressure(contours, pressure_px, phase):
    """Offset glyph contours outward (or slightly inward) with variable pen pressure (pure fill)."""
    if not contours or abs(pressure_px) <= 1e-4:
        return contours
    areas = [_contour_signed_area(s) for s in contours]
    max_area = max(areas, key=abs)
    if abs(max_area) < 1e-6:
        return contours
    outer_sign = 1.0 if max_area >= 0.0 else -1.0
    sign_p = 1.0 if pressure_px >= 0.0 else -1.0
    mag_p = abs(pressure_px)

    expanded = []
    for segs in contours:
        n = len(segs)
        if n == 0:
            continue
        tangents = [_seg_tangents(s) for s in segs]
        disps = []
        for i in range(n):
            v = segs[i][1]
            t_in = tangents[(i - 1) % n][1]
            t_out = tangents[i][0]
            if t_in is None and t_out is None:
                disps.append((0.0, 0.0))
                continue
            if t_in is None:
                t_in = t_out
            if t_out is None:
                t_out = t_in
            n_in = (outer_sign * t_in[1], -outer_sign * t_in[0])
            n_out = (outer_sign * t_out[1], -outer_sign * t_out[0])
            nx, ny = n_in[0] + n_out[0], n_in[1] + n_out[1]
            nl = math.hypot(nx, ny)
            if nl > 1e-6:
                nx, ny = nx / nl, ny / nl
                dot = n_in[0] * nx + n_in[1] * ny
                miter = min(1.35, 1.0 / max(0.55, dot))
            else:
                nx, ny = n_out
                miter = 1.0
            # Nib anisotropy (heavier on vertical strokes, lighter on horizontal hairlines)
            ty_avg = 0.5 * (abs(t_in[1]) + abs(t_out[1]))
            dir_mod = 0.72 + 0.56 * ty_avg
            wave = 1.0 + 0.16 * math.sin(0.32 * v[0] + 0.24 * v[1] + phase)
            d = sign_p * min(mag_p * 1.45, mag_p * dir_mod * wave * miter)
            disps.append((nx * d, ny * d))

        new_segs = []
        for i, s in enumerate(segs):
            d0 = disps[i]
            d1 = disps[(i + 1) % n]
            kind = s[0]
            p0 = (s[1][0] + d0[0], s[1][1] + d0[1])
            if kind == "L":
                p1 = (s[2][0] + d1[0], s[2][1] + d1[1])
                new_segs.append(("L", p0, p1))
            elif kind == "Q":
                dcx, dcy = 0.5 * (d0[0] + d1[0]), 0.5 * (d0[1] + d1[1])
                c = (s[2][0] + dcx, s[2][1] + dcy)
                p1 = (s[3][0] + d1[0], s[3][1] + d1[1])
                new_segs.append(("Q", p0, c, p1))
            else:
                c1 = (s[2][0] + d0[0], s[2][1] + d0[1])
                c2 = (s[3][0] + d1[0], s[3][1] + d1[1])
                p1 = (s[4][0] + d1[0], s[4][1] + d1[1])
                new_segs.append(("C", p0, c1, c2, p1))
        expanded.append(new_segs)
    return expanded


def _on_curve_points(contours):
    pts = []
    for segs in contours:
        for s in segs:
            pts.append(s[1])
    return pts


def _find_exit_point(contours, ch, y_base, size):
    pts = _on_curve_points(contours)
    if not pts:
        return None
    xh = 0.48 * size
    y_top = y_base - (0.95 if ch in _HIGH_EXIT else 0.65) * xh
    y_bot = y_base + 0.22 * xh
    cand = [p for p in pts if y_top <= p[1] <= y_bot]
    if not cand:
        cand = [p for p in pts if y_base - 1.05 * xh <= p[1] <= y_base + 0.35 * xh]
    return max(cand, key=lambda p: p[0]) if cand else None


def _find_entry_point(contours, y_base, size):
    pts = _on_curve_points(contours)
    if not pts:
        return None
    xh = 0.48 * size
    y_top = y_base - 0.85 * xh
    y_bot = y_base + 0.22 * xh
    cand = [p for p in pts if y_top <= p[1] <= y_bot]
    if not cand:
        cand = [p for p in pts if y_base - 1.05 * xh <= p[1] <= y_base + 0.35 * xh]
    return min(cand, key=lambda p: p[0]) if cand else None


def _ligature_ribbon(p_exit, p_entry, ch_prev, size, half_w, rng):
    """Build a tapered calligraphic Bezier S-curve/garland connecting two adjacent letters."""
    dx = p_entry[0] - p_exit[0]
    dy = p_entry[1] - p_exit[1]
    dist = math.hypot(dx, dy)
    if dist < 0.03 * size or dx < -0.08 * size or dx > 0.62 * size or abs(dy) > 0.75 * size:
        return None
    # Tuck endpoints inside both letter bodies so boolean union fuses cleanly without seams
    tuck = 0.055 * size
    p0 = (p_exit[0] - tuck, p_exit[1])
    p1 = (p_entry[0] + tuck, p_entry[1])
    span = math.hypot(p1[0] - p0[0], p1[1] - p0[1])

    # Calligraphic curve: high-exit letters (o, v, w, r, b) bridge with a gentle dip;
    # baseline-exit letters sweep down-and-up in a garland under-curve.
    high_exit = ch_prev in _HIGH_EXIT
    sag = (0.06 if high_exit else 0.17) * min(span, 0.55 * size) + rng.uniform(0.0, 0.03 * size)
    c1 = (p0[0] + 0.38 * (p1[0] - p0[0]), p0[1] + sag)
    c2 = (p0[0] + 0.68 * (p1[0] - p0[0]), p1[1] + 0.45 * sag)

    # Sample centerline into 5 points to sweep a smooth variable-width tapered ribbon
    pts = []
    for k in range(5):
        t = k / 4.0
        mt = 1.0 - t
        bx = mt**3 * p0[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * p1[0]
        by = mt**3 * p0[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * p1[1]
        pts.append((bx, by))

    top, bot = [], []
    # Hairline taper in the middle of the upstroke/connector, matching nib at junctions
    profile = (0.95, 0.72, 0.60, 0.72, 0.95)
    for k, p in enumerate(pts):
        if k == 0:
            tv = _unit_vec(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
        elif k == len(pts) - 1:
            tv = _unit_vec(pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1])
        else:
            tv = _unit_vec(pts[k + 1][0] - pts[k - 1][0], pts[k + 1][1] - pts[k - 1][1])
        tv = tv or (1.0, 0.0)
        nx, ny = -tv[1] * half_w * profile[k], tv[0] * half_w * profile[k]
        top.append((p[0] + nx, p[1] + ny))
        bot.append((p[0] - nx, p[1] - ny))

    segs = []
    for a, b in zip(top, top[1:]):
        segs.append(("L", a, b))
    segs.append(("L", top[-1], bot[-1]))
    for a, b in zip(reversed(bot), reversed(bot[:-1])):
        segs.append(("L", a, b))
    segs.append(("L", bot[0], top[0]))
    return segs


def _bar_ribbon(p0, p1, half_w):
    """Filled stroke ribbon for a straight bar (e.g., crossed seven)."""
    t = _unit_vec(p1[0] - p0[0], p1[1] - p0[1])
    if t is None:
        return None
    nx, ny = -t[1] * half_w, t[0] * half_w
    a = (p0[0] + nx, p0[1] + ny)
    b = (p1[0] + nx, p1[1] + ny)
    c = (p1[0] - nx, p1[1] - ny)
    d = (p0[0] - nx, p0[1] - ny)
    return [("L", a, b), ("L", b, c), ("L", c, d), ("L", d, a)]


def _fmt_pt(v: float) -> str:
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _contours_to_svg_d(contours, unify: bool = True) -> str:
    """Serialize contours into an SVG path 'd', unifying overlaps via Skia PathOps."""
    if not contours:
        return ""
    if unify and _pathops is not None:
        try:
            p = _pathops.Path()
            for segs in contours:
                if not segs:
                    continue
                p.moveTo(*segs[0][1])
                for s in segs:
                    kind = s[0]
                    if kind == "L":
                        p.lineTo(*s[2])
                    elif kind == "Q":
                        p.quadTo(*s[2], *s[3])
                    else:
                        p.cubicTo(*s[2], *s[3], *s[4])
                p.close()
            unified = _pathops.simplify(p, clockwise=True)
            cmds = []
            for verb, pts in unified:
                if verb == _pathops.PathVerb.MOVE:
                    cmds.append("M%s %s" % (_fmt_pt(pts[0][0]), _fmt_pt(pts[0][1])))
                elif verb == _pathops.PathVerb.LINE:
                    cmds.append("L%s %s" % (_fmt_pt(pts[0][0]), _fmt_pt(pts[0][1])))
                elif verb == _pathops.PathVerb.QUAD:
                    cmds.append("Q%s %s %s %s" % (
                        _fmt_pt(pts[0][0]), _fmt_pt(pts[0][1]),
                        _fmt_pt(pts[1][0]), _fmt_pt(pts[1][1])))
                elif verb == _pathops.PathVerb.CUBIC:
                    cmds.append("C%s %s %s %s %s %s" % (
                        _fmt_pt(pts[0][0]), _fmt_pt(pts[0][1]),
                        _fmt_pt(pts[1][0]), _fmt_pt(pts[1][1]),
                        _fmt_pt(pts[2][0]), _fmt_pt(pts[2][1])))
                elif verb == _pathops.PathVerb.CLOSE:
                    cmds.append("Z")
            if cmds:
                return "".join(cmds)
        except Exception:
            pass

    cmds = []
    for segs in contours:
        if not segs:
            continue
        cmds.append("M%s %s" % (_fmt_pt(segs[0][1][0]), _fmt_pt(segs[0][1][1])))
        for s in segs:
            kind = s[0]
            if kind == "L":
                cmds.append("L%s %s" % (_fmt_pt(s[2][0]), _fmt_pt(s[2][1])))
            elif kind == "Q":
                cmds.append("Q%s %s %s %s" % (
                    _fmt_pt(s[2][0]), _fmt_pt(s[2][1]),
                    _fmt_pt(s[3][0]), _fmt_pt(s[3][1])))
            else:
                cmds.append("C%s %s %s %s %s %s" % (
                    _fmt_pt(s[2][0]), _fmt_pt(s[2][1]),
                    _fmt_pt(s[3][0]), _fmt_pt(s[3][1]),
                    _fmt_pt(s[4][0]), _fmt_pt(s[4][1])))
        cmds.append("Z")
    return "".join(cmds)


def _is_joinable(ch: str) -> bool:
    return ch.isalpha() and ch.islower() and ord(ch) not in GREEK


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
        self._press = _OU(self.rng, 0.85)
        self._last = {}
        self.glyphs = 0
        self._history = []   # (rot, skew, grow) per glyph for diagnostics/tests

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

    def _deformed_contours(self, font, ch, vr, noise):
        """Glyph contours deformed by the allograph (vr) plus fresh instance noise."""
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
        f1 += r.gauss(0, 0.6 * share)
        f2 += r.gauss(0, 0.6 * share)
        fine = upem * noise * 0.12 * (0.5 + share)
        shift = [0.0, 0.0]

        def bump_shift():
            for j in (0, 1):
                shift[j] = vr.gauss(0, upem * noise * 0.45) + r.gauss(0, upem * noise * 0.2 * share)

        def deform(pt):
            px, py = pt
            return (px + a1 * math.sin(w1 * py / upem + f1) + shift[0] + r.gauss(0, fine),
                    py + a2 * math.sin(w2 * px / upem + f2) + shift[1] + r.gauss(0, fine))

        contours = _extract_contours(ops, deform, bump_shift)
        return contours, adv

    # ------------------------------------------------------------ writing
    def write(self, x, y, s, color, size=18, anchor="start"):
        """SVG of the handwritten string with baseline at y, and the pen advance."""
        e, r = self.style, self.rng
        fat = 1 + e.get("fatigue", 0) * min(1.0, self.glyphs / 3000)
        jit, loose = e["jitter"] * fat, e["looseness"]
        u = size / 24.0   # variation amplitudes were tuned at 24 px letters
        join_rate = e.get("join_rate", 0.7)
        max_run = max(1, int(e.get("max_stroke_len", 4)))

        glyphs = []
        main_font = e["font"]
        main_sw = fonts.stroke_weight(main_font)
        main_slant = fonts.natural_slant(main_font)
        for i, ch in enumerate(s):
            if ch in SPACES:
                glyphs.append((ch, None, size * SPACES[ch], 1.0, None, None))
                continue
            font = self._pick_font(s, i)
            if font is None:
                glyphs.append((ch, None, size * 0.5, 1.0, None, None))
                continue
            vr = self._variant(font, ch)
            noise = e["glyph_noise"] * (e["digit_noise"] if ch in DIGITS else 1.0)
            contours, adv = self._deformed_contours(font, ch, vr, noise)
            upem = fonts.units_per_em(font)
            scale = size / upem
            if font != main_font and e.get("match_x_height", True) and ch.islower():
                k = fonts.x_height(main_font) / fonts.x_height(font)
                scale *= max(0.75, min(1.35, k))
            glyphs.append((ch, contours, (adv or upem * 0.5) * scale, scale, vr, font))

        total = sum(g[2] for g in glyphs) + 0.45 * jit * u * len(glyphs)
        cx = x - total / 2 if anchor == "middle" else (x - total if anchor == "end" else x)
        start = cx
        phase = r.uniform(0, 6.28)
        tilt = self._tilt.step(1.0 * loose)
        tilt_mat = _rotate_around(tilt, x, y)

        out = []
        # Active continuous stroke group: list of (ch, contours, y_base, half_w)
        run_items = []
        run_color = None
        run_retraces = []

        def flush_run():
            nonlocal run_items, run_color, run_retraces
            if not run_items:
                return
            merged = []
            for idx, (rch, rcont, ry_base, rhw) in enumerate(run_items):
                merged.extend(rcont)
                if idx + 1 < len(run_items):
                    _, next_cont, next_y, next_hw = run_items[idx + 1]
                    p_exit = _find_exit_point(rcont, rch, ry_base, size)
                    p_entry = _find_entry_point(next_cont, next_y, size)
                    if p_exit is not None and p_entry is not None:
                        rib = _ligature_ribbon(p_exit, p_entry, rch, size, 0.5 * (rhw + next_hw), r)
                        if rib is not None:
                            merged.append(rib)
            d_str = _contours_to_svg_d(merged, unify=True)
            if d_str:
                out.append('<path d="%s" fill="%s"/>' % (d_str, run_color))
            for rt_d, rt_c in run_retraces:
                out.append('<path d="%s" fill="%s" opacity="0.75"/>' % (rt_d, rt_c))
            run_items = []
            run_color = None
            run_retraces = []

        for i, (ch, contours, w, scale, vr, font) in enumerate(glyphs):
            if not contours:
                flush_run()
                cx += (w if ch in SPACES else max(w, size * 0.52)) + r.uniform(0.0, 1.6) * jit * u
                continue
            self.glyphs += 1
            rot = vr.uniform(-2.5, 2.5) * jit + self._rot.step(2.2 * jit) + r.uniform(-1.5, 1.5) * jit
            skew = (e["slant"] + vr.uniform(-2.5, 2.5) * jit + self._slant.step(3.2 * jit)
                    + r.uniform(-2.0, 2.0) * jit)
            grow = vr.uniform(-0.05, 0.05) * jit + self._size.step(0.07 * jit) + r.uniform(-0.03, 0.03) * jit
            rot = max(-MAX_ROTATION, min(MAX_ROTATION, rot))
            skew = max(e["slant"] - MAX_SKEW, min(e["slant"] + MAX_SKEW, skew))
            grow = max(-MAX_SCALE, min(MAX_SCALE, grow))
            self._history.append((rot, skew, grow))

            # Harmonize borrowed glyphs so their slant and nib thickness match the main font
            harm_skew = 0.0
            harm_press = 0.0
            if font and font != main_font:
                harm_skew = max(-10.0, min(10.0, main_slant - fonts.natural_slant(font)))
                sw_diff = main_sw - fonts.stroke_weight(font)
                harm_press = max(-0.35 * u, min(0.35 * u, sw_diff * size * 0.48))

            dy = (self._drift.step(2.4 * loose) + math.sin(phase + i * 0.8) * jit
                  + r.uniform(-1.0, 1.0) * jit) * u
            sx = scale * (1 + grow)
            sy = scale * (1 + grow + vr.uniform(-0.05, 0.05))
            c = _tone(color, r, e["tone_variation"] * loose) if e["tone_variation"] else color

            can_join = (
                bool(run_items)
                and len(run_items) < max_run
                and _is_joinable(run_items[-1][0])
                and _is_joinable(ch)
                and r.random() < (join_rate * (0.45 if run_items[-1][0] in _LIFT_AFTER else 1.0))
            )
            if can_join and join_rate >= 0.5:
                # Tighten tracking slightly inside connected cursive strokes so ligatures flow naturally
                cx -= size * (0.045 if join_rate >= 0.75 else 0.02)

            # Variable pen pressure (baked into contour thickness — pure fill, no stroke)
            p_wave = max(0.25, 0.65 + self._press.step(0.22))
            pressure_px = 0.45 * e["pressure"] * p_wave * u + harm_press
            mat = _mat_mul(tilt_mat, _letter_matrix(cx, y + dy, rot, skew + harm_skew, sx, -sy))
            placed = _transform_contours(contours, mat)
            placed = _expand_pressure(placed, pressure_px, phase + i * 1.1)

            if ch == "7" and e.get("crossed_seven"):
                em = size / scale
                adv = w / sx
                p0 = _xform_pt(mat, (adv * 0.10, em * 0.315))
                p1 = _xform_pt(mat, (adv * 0.88, em * 0.29))
                bar = _bar_ribbon(p0, p1, max(0.35, em * 0.022 * sx))
                if bar is not None:
                    placed.append(bar)

            y_local = _xform_pt(tilt_mat, (cx, y + dy))[1]
            lig_hw = max(0.38, (0.029 * size) * p_wave)

            if not can_join:
                flush_run()
                run_color = c

            run_items.append((ch, placed, y_local, lig_hw))

            if e["retrace"] and ch not in DIGITS and r.random() < e["retrace"] * loose:
                mat2 = _mat_mul(
                    tilt_mat,
                    _letter_matrix(
                        cx + r.uniform(-0.8, 0.8),
                        y + dy + r.uniform(-0.8, 0.8),
                        rot + r.uniform(-2, 2),
                        skew + harm_skew,
                        sx,
                        -sy,
                    ),
                )
                rt_cont = _transform_contours(contours, mat2)
                rt_d = _contours_to_svg_d(rt_cont, unify=True)
                if rt_d:
                    run_retraces.append((rt_d, c))

            cx += w * (1 + grow * 0.5) + r.uniform(-0.5, 1.2) * jit * u

        flush_run()
        return "<g>%s</g>" % "".join(out), cx - start


def _tone(color, r, k):
    color = color.lstrip("#")
    rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
    f = 1 + r.uniform(-k, k)
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c * f))) for c in rgb)
