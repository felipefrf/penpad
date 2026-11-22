"""Pure-Python SVG -> Vector PDF compiler and SVG exporter.

Every paper rule, highlight, diagram stroke and handwritten stroke becomes a
native page-space vector path in the PDF (zero embedded bitmaps, zero per-glyph
transformation matrices, flat content stream by default).
"""

from __future__ import annotations

import datetime
import math
import os
import re
import xml.etree.ElementTree as ET
import zlib

_NUM_RE = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_PATH_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtZz]|[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")

_KAPPA = 0.5522847498307936  # 4 * (sqrt(2) - 1) / 3 for cubic Bezier circle approximation

_NAMED_COLORS = {
    "black": (0.0, 0.0, 0.0, 1.0),
    "white": (1.0, 1.0, 1.0, 1.0),
    "red": (1.0, 0.0, 0.0, 1.0),
    "green": (0.0, 0.502, 0.0, 1.0),
    "blue": (0.0, 0.0, 1.0, 1.0),
    "gray": (0.502, 0.502, 0.502, 1.0),
    "grey": (0.502, 0.502, 0.502, 1.0),
    "yellow": (1.0, 1.0, 0.0, 1.0),
    "transparent": None,
    "none": None,
}


def _fmt(v: float, prec: int = 4) -> str:
    if abs(v) < 1e-6:
        return "0"
    s = ("%.*f" % (prec, v)).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _fc(v: float) -> str:
    """Compact path coordinate (1 decimal place in PDF pt — 0.035 mm precision, high compression)."""
    return _fmt(v, 1)


def _frgb(v: float) -> str:
    return _fmt(v, 3)


def _parse_color(val: str | None) -> tuple[float, float, float, float] | None:
    if not val:
        return None
    s = val.strip().lower()
    if s in _NAMED_COLORS:
        return _NAMED_COLORS[s]
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            return (int(h[0] * 2, 16) / 255.0, int(h[1] * 2, 16) / 255.0, int(h[2] * 2, 16) / 255.0, 1.0)
        if len(h) == 6:
            return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0, int(h[4:6], 16) / 255.0, 1.0)
        if len(h) == 8:
            return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                    int(h[4:6], 16) / 255.0, int(h[6:8], 16) / 255.0)
        return None
    if s.startswith("rgb"):
        nums = [float(x) for x in _NUM_RE.findall(s)]
        if len(nums) >= 3:
            r, g, b = nums[0] / 255.0, nums[1] / 255.0, nums[2] / 255.0
            a = nums[3] if len(nums) >= 4 else 1.0
            return (max(0.0, min(1.0, r)), max(0.0, min(1.0, g)),
                    max(0.0, min(1.0, b)), max(0.0, min(1.0, a)))
    return None


def _parse_float(val: str | None, default: float = 0.0) -> float:
    if val is None:
        return default
    m = _NUM_RE.search(val)
    return float(m.group(0)) if m else default


def _parse_style_attr(style_str: str | None) -> dict[str, str]:
    out = {}
    if not style_str:
        return out
    for part in style_str.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    return out


# ------------------------------------------------------------------ 2D affine
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


def _apply_ctm(ctm, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = ctm
    return a * x + c * y + e, b * x + d * y + f


def _ctm_scale(ctm) -> float:
    a, b, c, d, _, _ = ctm
    return math.sqrt(abs(a * d - b * c))


def _parse_transform(s: str | None):
    if not s:
        return None
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    found = False
    for name, arg_str in _TRANSFORM_RE.findall(s):
        args = [float(x) for x in _NUM_RE.findall(arg_str)]
        op = name.lower()
        found = True
        if op == "translate" and args:
            tx = args[0]
            ty = args[1] if len(args) > 1 else 0.0
            t = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif op == "scale" and args:
            sx = args[0]
            sy = args[1] if len(args) > 1 else sx
            t = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif op == "rotate" and args:
            rad = math.radians(args[0])
            cs, sn = math.cos(rad), math.sin(rad)
            rot = (cs, sn, -sn, cs, 0.0, 0.0)
            if len(args) >= 3:
                cx, cy = args[1], args[2]
                t = _mat_mul(_mat_mul((1.0, 0.0, 0.0, 1.0, cx, cy), rot), (1.0, 0.0, 0.0, 1.0, -cx, -cy))
            else:
                t = rot
        elif op == "skewx" and args:
            t = (1.0, 0.0, math.tan(math.radians(args[0])), 1.0, 0.0, 0.0)
        elif op == "skewy" and args:
            t = (1.0, math.tan(math.radians(args[0])), 0.0, 1.0, 0.0, 0.0)
        elif op == "matrix" and len(args) >= 6:
            t = tuple(args[:6])
        else:
            continue
        m = _mat_mul(m, t)
    return m if found else None


# ------------------------------------------------------------ SVG path -> PDF
def _path_to_pdf(d: str, ctm) -> list[str]:
    tokens = _PATH_TOKEN_RE.findall(d)
    if not tokens:
        return []
    ops: list[str] = []
    i = 0
    n = len(tokens)
    cmd = ""
    cx = cy = 0.0
    sx = sy = 0.0
    last_c2 = None
    last_q1 = None
    prev_cmd = ""
    a, b, c, dm, e, f = ctm

    def pt(x: float, y: float) -> tuple[str, str]:
        return _fc(a * x + c * y + e), _fc(b * x + dm * y + f)

    def next_f() -> float:
        nonlocal i
        v = float(tokens[i])
        i += 1
        return v

    def is_num(idx: int) -> bool:
        return idx < n and not tokens[idx].isalpha()

    while i < n:
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        elif cmd == "M":
            cmd = "L"
        elif cmd == "m":
            cmd = "l"

        if cmd in ("M", "m"):
            x, y = next_f(), next_f()
            if cmd == "m":
                x += cx
                y += cy
            cx, cy = x, y
            sx, sy = x, y
            ops.append("%s %s m" % pt(cx, cy))
            last_c2 = last_q1 = None
            prev_cmd = cmd
            while is_num(i):
                x, y = next_f(), next_f()
                if cmd == "m":
                    x += cx
                    y += cy
                cx, cy = x, y
                ops.append("%s %s l" % pt(cx, cy))
                prev_cmd = "l" if cmd == "m" else "L"
        elif cmd in ("L", "l"):
            while is_num(i):
                x, y = next_f(), next_f()
                if cmd == "l":
                    x += cx
                    y += cy
                cx, cy = x, y
                ops.append("%s %s l" % pt(cx, cy))
            last_c2 = last_q1 = None
            prev_cmd = cmd
        elif cmd in ("H", "h"):
            while is_num(i):
                x = next_f()
                cx = cx + x if cmd == "h" else x
                ops.append("%s %s l" % pt(cx, cy))
            last_c2 = last_q1 = None
            prev_cmd = cmd
        elif cmd in ("V", "v"):
            while is_num(i):
                y = next_f()
                cy = cy + y if cmd == "v" else y
                ops.append("%s %s l" % pt(cx, cy))
            last_c2 = last_q1 = None
            prev_cmd = cmd
        elif cmd in ("C", "c"):
            while is_num(i):
                x1, y1, x2, y2, x, y = next_f(), next_f(), next_f(), next_f(), next_f(), next_f()
                if cmd == "c":
                    x1 += cx
                    y1 += cy
                    x2 += cx
                    y2 += cy
                    x += cx
                    y += cy
                p1x, p1y = pt(x1, y1)
                p2x, p2y = pt(x2, y2)
                px, py = pt(x, y)
                ops.append("%s %s %s %s %s %s c" % (p1x, p1y, p2x, p2y, px, py))
                last_c2 = (x2, y2)
                cx, cy = x, y
            last_q1 = None
            prev_cmd = cmd
        elif cmd in ("S", "s"):
            while is_num(i):
                x2, y2, x, y = next_f(), next_f(), next_f(), next_f()
                if cmd == "s":
                    x2 += cx
                    y2 += cy
                    x += cx
                    y += cy
                if prev_cmd in ("C", "c", "S", "s") and last_c2 is not None:
                    x1 = 2 * cx - last_c2[0]
                    y1 = 2 * cy - last_c2[1]
                else:
                    x1, y1 = cx, cy
                p1x, p1y = pt(x1, y1)
                p2x, p2y = pt(x2, y2)
                px, py = pt(x, y)
                ops.append("%s %s %s %s %s %s c" % (p1x, p1y, p2x, p2y, px, py))
                last_c2 = (x2, y2)
                cx, cy = x, y
                prev_cmd = cmd
            last_q1 = None
        elif cmd in ("Q", "q"):
            while is_num(i):
                qx, qy, x, y = next_f(), next_f(), next_f(), next_f()
                if cmd == "q":
                    qx += cx
                    qy += cy
                    x += cx
                    y += cy
                # Exact degree elevation from quadratic Bezier to cubic Bezier
                c1x = cx + (2.0 / 3.0) * (qx - cx)
                c1y = cy + (2.0 / 3.0) * (qy - cy)
                c2x = x + (2.0 / 3.0) * (qx - x)
                c2y = y + (2.0 / 3.0) * (qy - y)
                p1x, p1y = pt(c1x, c1y)
                p2x, p2y = pt(c2x, c2y)
                px, py = pt(x, y)
                ops.append("%s %s %s %s %s %s c" % (p1x, p1y, p2x, p2y, px, py))
                last_q1 = (qx, qy)
                cx, cy = x, y
                prev_cmd = cmd
            last_c2 = None
        elif cmd in ("T", "t"):
            while is_num(i):
                x, y = next_f(), next_f()
                if cmd == "t":
                    x += cx
                    y += cy
                if prev_cmd in ("Q", "q", "T", "t") and last_q1 is not None:
                    qx = 2 * cx - last_q1[0]
                    qy = 2 * cy - last_q1[1]
                else:
                    qx, qy = cx, cy
                c1x = cx + (2.0 / 3.0) * (qx - cx)
                c1y = cy + (2.0 / 3.0) * (qy - cy)
                c2x = x + (2.0 / 3.0) * (qx - x)
                c2y = y + (2.0 / 3.0) * (qy - y)
                p1x, p1y = pt(c1x, c1y)
                p2x, p2y = pt(c2x, c2y)
                px, py = pt(x, y)
                ops.append("%s %s %s %s %s %s c" % (p1x, p1y, p2x, p2y, px, py))
                last_q1 = (qx, qy)
                cx, cy = x, y
                prev_cmd = cmd
            last_c2 = None
        elif cmd in ("Z", "z"):
            ops.append("h")
            cx, cy = sx, sy
            last_c2 = last_q1 = None
            prev_cmd = cmd
        else:
            break
    return ops


def _ellipse_to_pdf(cx: float, cy: float, rx: float, ry: float, ctm) -> list[str]:
    kx, ky = rx * _KAPPA, ry * _KAPPA

    def p(x: float, y: float) -> tuple[str, str]:
        px, py = _apply_ctm(ctm, x, y)
        return _fc(px), _fc(py)

    return [
        "%s %s m" % p(cx + rx, cy),
        "%s %s %s %s %s %s c" % (*p(cx + rx, cy + ky), *p(cx + kx, cy + ry), *p(cx, cy + ry)),
        "%s %s %s %s %s %s c" % (*p(cx - kx, cy + ry), *p(cx - rx, cy + ky), *p(cx - rx, cy)),
        "%s %s %s %s %s %s c" % (*p(cx - rx, cy - ky), *p(cx - kx, cy - ry), *p(cx, cy - ry)),
        "%s %s %s %s %s %s c" % (*p(cx + kx, cy - ry), *p(cx + rx, cy - ky), *p(cx + rx, cy)),
        "h",
    ]


def _rect_to_pdf(x: float, y: float, w: float, h: float, rx: float, ry: float, ctm) -> list[str]:
    if w <= 0 or h <= 0:
        return []
    if rx <= 0 and ry <= 0:
        a, b, c, dm, _, _ = ctm
        if abs(b) < 1e-9 and abs(c) < 1e-9:
            x0, y0 = _apply_ctm(ctm, x, y)
            x1, y1 = _apply_ctm(ctm, x + w, y + h)
            rx0, ry0 = min(x0, x1), min(y0, y1)
            rw, rh = abs(x1 - x0), abs(y1 - y0)
            return ["%s %s %s %s re" % (_fc(rx0), _fc(ry0), _fc(rw), _fc(rh))]
        pts = [_apply_ctm(ctm, px, py) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
        return [
            "%s %s m" % (_fc(pts[0][0]), _fc(pts[0][1])),
            "%s %s l" % (_fc(pts[1][0]), _fc(pts[1][1])),
            "%s %s l" % (_fc(pts[2][0]), _fc(pts[2][1])),
            "%s %s l" % (_fc(pts[3][0]), _fc(pts[3][1])),
            "h",
        ]
    rx = min(rx or ry, w / 2.0)
    ry = min(ry or rx, h / 2.0)
    kx, ky = rx * _KAPPA, ry * _KAPPA
    x0, y0, x1, y1 = x, y, x + w, y + h

    def p(px: float, py: float) -> tuple[str, str]:
        tx, ty = _apply_ctm(ctm, px, py)
        return _fc(tx), _fc(ty)

    return [
        "%s %s m" % p(x0 + rx, y0),
        "%s %s l" % p(x1 - rx, y0),
        "%s %s %s %s %s %s c" % (*p(x1 - rx + kx, y0), *p(x1, y0 + ry - ky), *p(x1, y0 + ry)),
        "%s %s l" % p(x1, y1 - ry),
        "%s %s %s %s %s %s c" % (*p(x1, y1 - ry + ky), *p(x1 - rx + kx, y1), *p(x1 - rx, y1)),
        "%s %s l" % p(x0 + rx, y1),
        "%s %s %s %s %s %s c" % (*p(x0 + rx - kx, y1), *p(x0, y1 - ry + ky), *p(x0, y1 - ry)),
        "%s %s l" % p(x0, y0 + ry),
        "%s %s %s %s %s %s c" % (*p(x0, y0 + ry - ky), *p(x0 + rx - kx, y0), *p(x0 + rx, y0)),
        "h",
    ]


# ------------------------------------------------------------ SVG tree -> PDF
_DEFAULT_STATE = {
    "fill": "black",
    "stroke": "none",
    "stroke-width": "1",
    "stroke-linecap": "butt",
    "stroke-linejoin": "miter",
    "stroke-dasharray": "none",
    "fill-rule": "nonzero",
    "opacity": 1.0,
    "fill-opacity": 1.0,
    "stroke-opacity": 1.0,
}

_CAP_MAP = {"butt": 0, "round": 1, "square": 2}
_JOIN_MAP = {"miter": 0, "round": 1, "bevel": 2}


class _StreamBuilder:
    """Walks parsed SVG fragments and emits page-space PDF content stream commands."""

    def __init__(self, root_ctm):
        self.root_ctm = root_ctm
        self.cmds: list[str] = []
        self.extgstates: dict[tuple[float, float], str] = {}
        self._reset_state()

    def _reset_state(self):
        self._fill_rgb = None
        self._stroke_rgb = None
        self._lw = None
        self._cap = None
        self._join = None
        self._dash = None

    def _gs_name(self, fill_a: float, stroke_a: float) -> str | None:
        fa = round(max(0.0, min(1.0, fill_a)), 3)
        sa = round(max(0.0, min(1.0, stroke_a)), 3)
        if fa >= 0.999 and sa >= 0.999:
            return None
        key = (fa, sa)
        if key not in self.extgstates:
            self.extgstates[key] = "GS%d" % (len(self.extgstates) + 1)
        return self.extgstates[key]

    def emit_svg_fragment(self, fragment: str):
        if not fragment or not fragment.strip():
            return
        root = ET.fromstring("<svg>%s</svg>" % fragment)
        for child in root:
            self._walk(child, _DEFAULT_STATE, self.root_ctm)

    def _walk(self, elem: ET.Element, inherited: dict, ctm):
        tag = elem.tag.split("}")[-1].lower()
        if tag in ("defs", "style", "title", "desc", "metadata"):
            return

        inline_style = _parse_style_attr(elem.attrib.get("style"))

        def attr(k: str):
            return inline_style.get(k, elem.attrib.get(k))

        st = dict(inherited)
        for k in ("fill", "stroke", "stroke-width", "stroke-linecap",
                  "stroke-linejoin", "stroke-dasharray", "fill-rule"):
            v = attr(k)
            if v is not None:
                st[k] = v
        if attr("opacity") is not None:
            st["opacity"] = inherited["opacity"] * _parse_float(attr("opacity"), 1.0)
        if attr("fill-opacity") is not None:
            st["fill-opacity"] = _parse_float(attr("fill-opacity"), 1.0)
        if attr("stroke-opacity") is not None:
            st["stroke-opacity"] = _parse_float(attr("stroke-opacity"), 1.0)

        tr = _parse_transform(elem.attrib.get("transform"))
        cur_ctm = _mat_mul(ctm, tr) if tr else ctm

        if tag in ("g", "svg"):
            for child in elem:
                self._walk(child, st, cur_ctm)
            return

        path_ops = self._shape_ops(tag, elem, cur_ctm)
        if not path_ops:
            return

        fill_rgba = None if tag == "line" else _parse_color(st["fill"])
        stroke_rgba = _parse_color(st["stroke"])
        if fill_rgba is None and stroke_rgba is None:
            return

        fill_a = (fill_rgba[3] * st["fill-opacity"] * st["opacity"]) if fill_rgba else 1.0
        stroke_a = (stroke_rgba[3] * st["stroke-opacity"] * st["opacity"]) if stroke_rgba else 1.0
        gs = self._gs_name(fill_a, stroke_a)

        saved_state = None
        if gs:
            saved_state = (self._fill_rgb, self._stroke_rgb, self._lw, self._cap, self._join, self._dash)
            self.cmds.append("q")
            self.cmds.append("/%s gs" % gs)

        if fill_rgba is not None:
            frgb = (_frgb(fill_rgba[0]), _frgb(fill_rgba[1]), _frgb(fill_rgba[2]))
            if frgb != self._fill_rgb:
                self.cmds.append("%s %s %s rg" % frgb)
                self._fill_rgb = frgb

        if stroke_rgba is not None:
            srgb = (_frgb(stroke_rgba[0]), _frgb(stroke_rgba[1]), _frgb(stroke_rgba[2]))
            if srgb != self._stroke_rgb:
                self.cmds.append("%s %s %s RG" % srgb)
                self._stroke_rgb = srgb
            scale = _ctm_scale(cur_ctm)
            sw = _fc(_parse_float(st["stroke-width"], 1.0) * scale)
            if sw != self._lw:
                self.cmds.append("%s w" % sw)
                self._lw = sw
            cap = _CAP_MAP.get(st["stroke-linecap"].strip().lower())
            if cap is not None and cap != self._cap:
                self.cmds.append("%d J" % cap)
                self._cap = cap
            join = _JOIN_MAP.get(st["stroke-linejoin"].strip().lower())
            if join is not None and join != self._join:
                self.cmds.append("%d j" % join)
                self._join = join
            dash = st["stroke-dasharray"].strip().lower()
            if dash and dash != "none":
                nums = [_fc(float(x) * scale) for x in _NUM_RE.findall(dash)]
                dash_cmd = "[%s] 0 d" % " ".join(nums) if nums else "[] 0 d"
            else:
                dash_cmd = "[] 0 d"
            if dash_cmd != self._dash and (dash_cmd != "[] 0 d" or self._dash is not None):
                self.cmds.append(dash_cmd)
                self._dash = dash_cmd

        self.cmds.extend(path_ops)

        even_odd = st["fill-rule"].strip().lower() == "evenodd"
        if fill_rgba is not None and stroke_rgba is not None:
            self.cmds.append("B*" if even_odd else "B")
        elif fill_rgba is not None:
            self.cmds.append("f*" if even_odd else "f")
        else:
            self.cmds.append("S")

        if gs:
            self.cmds.append("Q")
            (self._fill_rgb, self._stroke_rgb, self._lw,
             self._cap, self._join, self._dash) = saved_state

    def _shape_ops(self, tag: str, elem: ET.Element, ctm) -> list[str]:
        a = elem.attrib
        if tag == "path":
            return _path_to_pdf(a.get("d", ""), ctm)
        if tag == "rect":
            return _rect_to_pdf(
                _parse_float(a.get("x")), _parse_float(a.get("y")),
                _parse_float(a.get("width")), _parse_float(a.get("height")),
                _parse_float(a.get("rx")), _parse_float(a.get("ry")),
                ctm,
            )
        if tag == "circle":
            r = _parse_float(a.get("r"))
            if r <= 0:
                return []
            return _ellipse_to_pdf(_parse_float(a.get("cx")), _parse_float(a.get("cy")), r, r, ctm)
        if tag == "ellipse":
            rx, ry = _parse_float(a.get("rx")), _parse_float(a.get("ry"))
            if rx <= 0 or ry <= 0:
                return []
            return _ellipse_to_pdf(_parse_float(a.get("cx")), _parse_float(a.get("cy")), rx, ry, ctm)
        if tag == "line":
            x1, y1 = _apply_ctm(ctm, _parse_float(a.get("x1")), _parse_float(a.get("y1")))
            x2, y2 = _apply_ctm(ctm, _parse_float(a.get("x2")), _parse_float(a.get("y2")))
            return [
                "%s %s m" % (_fc(x1), _fc(y1)),
                "%s %s l" % (_fc(x2), _fc(y2)),
            ]
        if tag in ("polyline", "polygon"):
            nums = [float(x) for x in _NUM_RE.findall(a.get("points", ""))]
            if len(nums) < 4:
                return []
            x0, y0 = _apply_ctm(ctm, nums[0], nums[1])
            ops = ["%s %s m" % (_fc(x0), _fc(y0))]
            for j in range(2, len(nums) - 1, 2):
                px, py = _apply_ctm(ctm, nums[j], nums[j + 1])
                ops.append("%s %s l" % (_fc(px), _fc(py)))
            if tag == "polygon":
                ops.append("h")
            return ops
        return []


# ------------------------------------------------------------ PDF writer
def _pdf_str(s: str) -> bytes:
    """Encode a Python string as a PDF literal or UTF-16BE hex string."""
    try:
        raw = s.encode("ascii")
        esc = raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
        return b"(" + esc + b")"
    except UnicodeEncodeError:
        return b"<FEFF" + s.encode("utf-16-be").hex().upper().encode("ascii") + b">"


def _pdf_date() -> bytes:
    now = datetime.datetime.now(datetime.timezone.utc)
    return _pdf_str(now.strftime("D:%Y%m%d%H%M%SZ"))


def page_svg(w: int, h: int, paper_svg: str, highlights: list[str],
             strokes: list[str], inked: list[tuple[str, str]]) -> str:
    """Assemble one complete, layered standalone SVG document for a page."""
    layers = ['<g id="paper">%s</g>' % paper_svg]
    if highlights:
        layers.append('<g id="highlights">%s</g>' % "".join(highlights))
    if strokes:
        layers.append('<g id="diagrams">%s</g>' % "".join(strokes))
    if inked:
        layers.append('<g id="handwriting">%s</g>' % "".join(svg for _, svg in inked))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">%s</svg>'
        % (w, h, w, h, "".join(layers))
    )


def build_pdf_bytes(pages: list[dict], width_pt: float, height_pt: float,
                    w: int, h: int, paper_svg: str, title: str | None = None,
                    *, layers: bool = False, creator: str | None = None,
                    producer: str | None = None) -> bytes:
    """Compile notebook pages into a compressed vector PDF (flat by default)."""
    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # Reserve object 1 (Catalog) and 2 (Pages)
    add_obj(b"")
    add_obj(b"")

    layer_names = [
        ("L_Paper", "Paper"),
        ("L_Highlights", "Highlights"),
        ("L_Diagrams", "Diagrams"),
        ("L_Handwriting", "Handwriting"),
    ]
    ocg_ids: dict[str, int] = {}
    if layers:
        for res_name, label in layer_names:
            ocg_ids[res_name] = add_obj(
                b"<< /Type /OCG /Name " + _pdf_str(label) + b" >>"
            )

    sx = width_pt / float(w)
    sy = height_pt / float(h)
    root_ctm = (sx, 0.0, 0.0, -sy, 0.0, height_pt)
    page_ids: list[int] = []

    for page in pages:
        sb = _StreamBuilder(root_ctm)

        if layers:
            sb.cmds.append("/OC /L_Paper BDC")
        sb.emit_svg_fragment(paper_svg)
        if layers:
            sb.cmds.append("EMC")

        highlights = page.get("highlights") or []
        if highlights:
            if layers:
                sb.cmds.append("/OC /L_Highlights BDC")
            sb.emit_svg_fragment("".join(highlights))
            if layers:
                sb.cmds.append("EMC")

        strokes = page.get("strokes") or []
        if strokes:
            if layers:
                sb.cmds.append("/OC /L_Diagrams BDC")
            sb.emit_svg_fragment("".join(strokes))
            if layers:
                sb.cmds.append("EMC")

        inked = page.get("inked") or []
        if inked:
            if layers:
                sb.cmds.append("/OC /L_Handwriting BDC")
            sb.emit_svg_fragment("".join(svg for _, svg in inked))
            if layers:
                sb.cmds.append("EMC")

        raw_stream = "\n".join(sb.cmds).encode("ascii")
        comp = zlib.compress(raw_stream, level=9)
        stream_id = add_obj(
            b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp)
            + comp + b"\nendstream"
        )

        gs_entries = []
        for (fa, sa), name in sb.extgstates.items():
            gs_id = add_obj(
                b"<< /Type /ExtGState /ca %s /CA %s >>" % (
                    _fmt(fa).encode("ascii"), _fmt(sa).encode("ascii"))
            )
            gs_entries.append(b"/%s %d 0 R" % (name.encode("ascii"), gs_id))

        res_parts = []
        if layers:
            props = b" ".join(b"/%s %d 0 R" % (k.encode("ascii"), oid) for k, oid in ocg_ids.items())
            res_parts.append(b"/Properties << " + props + b" >>")
        if gs_entries:
            res_parts.append(b"/ExtGState << " + b" ".join(gs_entries) + b" >>")

        page_id = add_obj(
            b"<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 %s %s] "
            b"/Resources << %s >> "
            b"/Contents %d 0 R >>" % (
                _fmt(width_pt).encode("ascii"),
                _fmt(height_pt).encode("ascii"),
                b" ".join(res_parts),
                stream_id,
            )
        )
        page_ids.append(page_id)

    # Info dictionary (minimal, neutral metadata by default)
    info_parts = [b"/CreationDate " + _pdf_date()]
    if title:
        info_parts.insert(0, b"/Title " + _pdf_str(title))
    if creator:
        info_parts.append(b"/Creator " + _pdf_str(creator))
    if producer:
        info_parts.append(b"/Producer " + _pdf_str(producer))
    info_id = add_obj(b"<< " + b" ".join(info_parts) + b" >>")

    # Fill in Pages (obj 2) and Catalog (obj 1)
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objects[1] = b"<< /Type /Pages /Count %d /Kids [ %s ] >>" % (len(page_ids), kids)

    if layers:
        ocg_refs = b" ".join(b"%d 0 R" % ocg_ids[k] for k, _ in layer_names)
        oc_props = (
            b"<< /OCGs [ %s ] "
            b"/D << /Name (Default) /BaseState /ON /Order [ %s ] >> >>" % (ocg_refs, ocg_refs)
        )
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R /OCProperties %s >>" % oc_props
    else:
        objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"

    # Serialize PDF with xref table
    version = b"%PDF-1.5\n" if layers else b"%PDF-1.4\n"
    out = bytearray(version + b"%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % idx + body + b"\nendobj\n"

    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += b"%010d 00000 n \n" % off
    out += (
        b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\n"
        b"startxref\n%d\n%%%%EOF\n" % (len(objects) + 1, info_id, xref_pos)
    )
    return bytes(out)


def save_pdf(pages: list[dict], path: str, width_pt: float, height_pt: float,
             w: int, h: int, paper_svg: str, title: str | None = None,
             *, layers: bool = False, creator: str | None = None,
             producer: str | None = None) -> str:
    data = build_pdf_bytes(
        pages, width_pt, height_pt, w, h, paper_svg, title=title,
        layers=layers, creator=creator, producer=producer,
    )
    with open(path, "wb") as f:
        f.write(data)
    return path


def save_svg(pages: list[dict], path: str, w: int, h: int, paper_svg: str) -> list[str]:
    root, ext = os.path.splitext(path)
    if len(pages) == 1:
        p = pages[0]
        with open(path, "w", encoding="utf-8") as f:
            f.write(page_svg(w, h, paper_svg, p.get("highlights") or [], p.get("strokes") or [], p.get("inked") or []))
        return [path]
    paths = []
    for n, p in enumerate(pages, 1):
        out_path = "%s-%02d%s" % (root, n, ext)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page_svg(w, h, paper_svg, p.get("highlights") or [], p.get("strokes") or [], p.get("inked") or []))
        paths.append(out_path)
    return paths
