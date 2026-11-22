"""Pure-Python SVG -> Vector PDF 1.5 compiler and SVG exporter.

Every paper rule, highlight, diagram stroke and handwritten glyph becomes an
independent vector path in the PDF (zero embedded bitmaps), organized into
Optional Content Groups (PDF layers): Paper, Highlights, Diagrams, Handwriting.
"""

from __future__ import annotations

import datetime
import math
import os
import re
import xml.etree.ElementTree as ET
import zlib

from . import __version__

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
    """Compact path coordinate (1 decimal place — 0.02 mm precision on A4, high compression)."""
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
def _path_to_pdf(d: str) -> list[str]:
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
            ops.append("%s %s m" % (_fc(cx), _fc(cy)))
            last_c2 = last_q1 = None
            prev_cmd = cmd
            while is_num(i):
                x, y = next_f(), next_f()
                if cmd == "m":
                    x += cx
                    y += cy
                cx, cy = x, y
                ops.append("%s %s l" % (_fc(cx), _fc(cy)))
                prev_cmd = "l" if cmd == "m" else "L"
        elif cmd in ("L", "l"):
            while is_num(i):
                x, y = next_f(), next_f()
                if cmd == "l":
                    x += cx
                    y += cy
                cx, cy = x, y
                ops.append("%s %s l" % (_fc(cx), _fc(cy)))
            last_c2 = last_q1 = None
            prev_cmd = cmd
        elif cmd in ("H", "h"):
            while is_num(i):
                x = next_f()
                cx = cx + x if cmd == "h" else x
                ops.append("%s %s l" % (_fc(cx), _fc(cy)))
            last_c2 = last_q1 = None
            prev_cmd = cmd
        elif cmd in ("V", "v"):
            while is_num(i):
                y = next_f()
                cy = cy + y if cmd == "v" else y
                ops.append("%s %s l" % (_fc(cx), _fc(cy)))
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
                ops.append("%s %s %s %s %s %s c" % (
                    _fc(x1), _fc(y1), _fc(x2), _fc(y2), _fc(x), _fc(y)))
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
                ops.append("%s %s %s %s %s %s c" % (
                    _fc(x1), _fc(y1), _fc(x2), _fc(y2), _fc(x), _fc(y)))
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
                ops.append("%s %s %s %s %s %s c" % (
                    _fc(c1x), _fc(c1y), _fc(c2x), _fc(c2y), _fc(x), _fc(y)))
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
                ops.append("%s %s %s %s %s %s c" % (
                    _fc(c1x), _fc(c1y), _fc(c2x), _fc(c2y), _fc(x), _fc(y)))
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


def _ellipse_to_pdf(cx: float, cy: float, rx: float, ry: float) -> list[str]:
    kx, ky = rx * _KAPPA, ry * _KAPPA
    return [
        "%s %s m" % (_fmt(cx + rx), _fmt(cy)),
        "%s %s %s %s %s %s c" % (
            _fmt(cx + rx), _fmt(cy + ky), _fmt(cx + kx), _fmt(cy + ry), _fmt(cx), _fmt(cy + ry)),
        "%s %s %s %s %s %s c" % (
            _fmt(cx - kx), _fmt(cy + ry), _fmt(cx - rx), _fmt(cy + ky), _fmt(cx - rx), _fmt(cy)),
        "%s %s %s %s %s %s c" % (
            _fmt(cx - rx), _fmt(cy - ky), _fmt(cx - kx), _fmt(cy - ry), _fmt(cx), _fmt(cy - ry)),
        "%s %s %s %s %s %s c" % (
            _fmt(cx + kx), _fmt(cy - ry), _fmt(cx + rx), _fmt(cy - ky), _fmt(cx + rx), _fmt(cy)),
        "h",
    ]


def _rect_to_pdf(x: float, y: float, w: float, h: float, rx: float = 0.0, ry: float = 0.0) -> list[str]:
    if w <= 0 or h <= 0:
        return []
    if rx <= 0 and ry <= 0:
        return ["%s %s %s %s re" % (_fmt(x), _fmt(y), _fmt(w), _fmt(h))]
    rx = min(rx or ry, w / 2.0)
    ry = min(ry or rx, h / 2.0)
    kx, ky = rx * _KAPPA, ry * _KAPPA
    x0, y0, x1, y1 = x, y, x + w, y + h
    return [
        "%s %s m" % (_fmt(x0 + rx), _fmt(y0)),
        "%s %s l" % (_fmt(x1 - rx), _fmt(y0)),
        "%s %s %s %s %s %s c" % (_fmt(x1 - rx + kx), _fmt(y0), _fmt(x1), _fmt(y0 + ry - ky), _fmt(x1), _fmt(y0 + ry)),
        "%s %s l" % (_fmt(x1), _fmt(y1 - ry)),
        "%s %s %s %s %s %s c" % (_fmt(x1), _fmt(y1 - ry + ky), _fmt(x1 - rx + kx), _fmt(y1), _fmt(x1 - rx), _fmt(y1)),
        "%s %s l" % (_fmt(x0 + rx), _fmt(y1)),
        "%s %s %s %s %s %s c" % (_fmt(x0 + rx - kx), _fmt(y1), _fmt(x0), _fmt(y1 - ry + ky), _fmt(x0), _fmt(y1 - ry)),
        "%s %s l" % (_fmt(x0), _fmt(y0 + ry)),
        "%s %s %s %s %s %s c" % (_fmt(x0), _fmt(y0 + ry - ky), _fmt(x0 + rx - kx), _fmt(y0), _fmt(x0 + rx), _fmt(y0)),
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
    """Walks parsed SVG fragments and emits PDF content stream commands."""

    def __init__(self):
        self.cmds: list[str] = []
        self.extgstates: dict[tuple[float, float], str] = {}

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
            self._walk(child, _DEFAULT_STATE)

    def _walk(self, elem: ET.Element, inherited: dict):
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
        if tag in ("g", "svg"):
            if tr:
                self.cmds.append("q %s cm" % " ".join(_fmt(v) for v in tr))
            for child in elem:
                self._walk(child, st)
            if tr:
                self.cmds.append("Q")
            return

        path_ops = self._shape_ops(tag, elem)
        if not path_ops:
            return

        fill_rgba = _parse_color(st["fill"])
        stroke_rgba = _parse_color(st["stroke"])
        if fill_rgba is None and stroke_rgba is None:
            return

        fill_a = (fill_rgba[3] * st["fill-opacity"] * st["opacity"]) if fill_rgba else 1.0
        stroke_a = (stroke_rgba[3] * st["stroke-opacity"] * st["opacity"]) if stroke_rgba else 1.0
        gs = self._gs_name(fill_a, stroke_a)

        self.cmds.append("q")
        if tr:
            self.cmds.append("%s cm" % " ".join(_fmt(v) for v in tr))
        if gs:
            self.cmds.append("/%s gs" % gs)
        if fill_rgba is not None:
            self.cmds.append("%s %s %s rg" % (_frgb(fill_rgba[0]), _frgb(fill_rgba[1]), _frgb(fill_rgba[2])))
        if stroke_rgba is not None:
            self.cmds.append("%s %s %s RG" % (_frgb(stroke_rgba[0]), _frgb(stroke_rgba[1]), _frgb(stroke_rgba[2])))
            sw = _parse_float(st["stroke-width"], 1.0)
            self.cmds.append("%s w" % _fc(sw))
            cap = _CAP_MAP.get(st["stroke-linecap"].strip().lower())
            if cap is not None:
                self.cmds.append("%d J" % cap)
            join = _JOIN_MAP.get(st["stroke-linejoin"].strip().lower())
            if join is not None:
                self.cmds.append("%d j" % join)
            dash = st["stroke-dasharray"].strip().lower()
            if dash and dash != "none":
                nums = [_fmt(float(x)) for x in _NUM_RE.findall(dash)]
                if nums:
                    self.cmds.append("[%s] 0 d" % " ".join(nums))

        self.cmds.extend(path_ops)

        even_odd = st["fill-rule"].strip().lower() == "evenodd"
        if fill_rgba is not None and stroke_rgba is not None:
            self.cmds.append("B*" if even_odd else "B")
        elif fill_rgba is not None:
            self.cmds.append("f*" if even_odd else "f")
        else:
            self.cmds.append("S")
        self.cmds.append("Q")

    def _shape_ops(self, tag: str, elem: ET.Element) -> list[str]:
        a = elem.attrib
        if tag == "path":
            return _path_to_pdf(a.get("d", ""))
        if tag == "rect":
            return _rect_to_pdf(
                _parse_float(a.get("x")), _parse_float(a.get("y")),
                _parse_float(a.get("width")), _parse_float(a.get("height")),
                _parse_float(a.get("rx")), _parse_float(a.get("ry")),
            )
        if tag == "circle":
            r = _parse_float(a.get("r"))
            if r <= 0:
                return []
            return _ellipse_to_pdf(_parse_float(a.get("cx")), _parse_float(a.get("cy")), r, r)
        if tag == "ellipse":
            rx, ry = _parse_float(a.get("rx")), _parse_float(a.get("ry"))
            if rx <= 0 or ry <= 0:
                return []
            return _ellipse_to_pdf(_parse_float(a.get("cx")), _parse_float(a.get("cy")), rx, ry)
        if tag == "line":
            return [
                "%s %s m" % (_fmt(_parse_float(a.get("x1"))), _fmt(_parse_float(a.get("y1")))),
                "%s %s l" % (_fmt(_parse_float(a.get("x2"))), _fmt(_parse_float(a.get("y2")))),
            ]
        if tag in ("polyline", "polygon"):
            nums = [float(x) for x in _NUM_RE.findall(a.get("points", ""))]
            if len(nums) < 4:
                return []
            ops = ["%s %s m" % (_fmt(nums[0]), _fmt(nums[1]))]
            for j in range(2, len(nums) - 1, 2):
                ops.append("%s %s l" % (_fmt(nums[j]), _fmt(nums[j + 1])))
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
                    w: int, h: int, paper_svg: str, title: str | None = None) -> bytes:
    """Compile notebook pages into a compressed, layered vector PDF 1.5."""
    objects: list[bytes] = []

    def add_obj(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    # Reserve object 1 (Catalog) and 2 (Pages)
    add_obj(b"")
    add_obj(b"")

    # Optional Content Groups (PDF layers)
    layer_names = [
        ("L_Paper", "Paper"),
        ("L_Highlights", "Highlights"),
        ("L_Diagrams", "Diagrams"),
        ("L_Handwriting", "Handwriting"),
    ]
    ocg_ids: dict[str, int] = {}
    for res_name, label in layer_names:
        ocg_ids[res_name] = add_obj(
            b"<< /Type /OCG /Name " + _pdf_str(label) + b" >>"
        )

    sx = width_pt / float(w)
    sy = height_pt / float(h)
    page_ids: list[int] = []

    for page in pages:
        sb = _StreamBuilder()
        sb.cmds.append("q")
        sb.cmds.append("%s 0 0 %s 0 %s cm" % (_fmt(sx), _fmt(-sy), _fmt(height_pt)))

        sb.cmds.append("/OC /L_Paper BDC")
        sb.emit_svg_fragment(paper_svg)
        sb.cmds.append("EMC")

        highlights = page.get("highlights") or []
        if highlights:
            sb.cmds.append("/OC /L_Highlights BDC")
            sb.emit_svg_fragment("".join(highlights))
            sb.cmds.append("EMC")

        strokes = page.get("strokes") or []
        if strokes:
            sb.cmds.append("/OC /L_Diagrams BDC")
            sb.emit_svg_fragment("".join(strokes))
            sb.cmds.append("EMC")

        inked = page.get("inked") or []
        if inked:
            sb.cmds.append("/OC /L_Handwriting BDC")
            sb.emit_svg_fragment("".join(svg for _, svg in inked))
            sb.cmds.append("EMC")

        sb.cmds.append("Q")

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

        props = b" ".join(b"/%s %d 0 R" % (k.encode("ascii"), oid) for k, oid in ocg_ids.items())
        res_parts = [b"/Properties << " + props + b" >>"]
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

    # Info dictionary (clean metadata)
    info_parts = [
        b"/Creator " + _pdf_str("penpad"),
        b"/Producer " + _pdf_str("penpad %s" % __version__),
        b"/CreationDate " + _pdf_date(),
    ]
    if title:
        info_parts.insert(0, b"/Title " + _pdf_str(title))
    info_id = add_obj(b"<< " + b" ".join(info_parts) + b" >>")

    # Fill in Pages (obj 2) and Catalog (obj 1)
    kids = b" ".join(b"%d 0 R" % pid for pid in page_ids)
    objects[1] = b"<< /Type /Pages /Count %d /Kids [ %s ] >>" % (len(page_ids), kids)

    ocg_refs = b" ".join(b"%d 0 R" % ocg_ids[k] for k, _ in layer_names)
    oc_props = (
        b"<< /OCGs [ %s ] "
        b"/D << /Name (Default) /BaseState /ON /Order [ %s ] >> >>" % (ocg_refs, ocg_refs)
    )
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R /OCProperties %s >>" % oc_props

    # Serialize PDF with xref table
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
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
             w: int, h: int, paper_svg: str, title: str | None = None) -> str:
    data = build_pdf_bytes(pages, width_pt, height_pt, w, h, paper_svg, title=title)
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
