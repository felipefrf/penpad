"""Notebook: multi-page handwritten documents with text, math and sketches."""

from __future__ import annotations

import logging
import math
import os
import re
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor

from . import raster, vector
from .hand import Hand, _OU, hand_line
from .latex import escape_text, latex_to_markup
from .layout import Box, Item, Row, parse, stroke, words
from .paper import get_paper
from .styles import get_style, ink

log = logging.getLogger("penpad")

MIN_FIT = 0.62   # an equation may shrink to this fraction of the letter size to fit the line


def _render_page(args):
    w, h, paper_svg, highlights, strokes, inked, style, width_pt, dpi = args
    return raster.compose(w, h, paper_svg, strokes, inked, style, width_pt, dpi, highlights=highlights)


class Notebook:
    """A stack of handwritten pages.

    >>> nb = Notebook(paper="goodnotes", style="cursive", seed=3)
    >>> nb.title("1) Material derivative")
    >>> nb.text("Chain rule on f(x(t), y(t), z(t), t):")
    >>> nb.math(r"\\frac{Df}{Dt} = \\frac{\\partial f}{\\partial t} + \\vec{u}\\cdot\\nabla f", box=True)
    >>> nb.save("notes.pdf")

    ``text`` and ``eq`` take penpad markup (``x_1``, ``\\f{a}{b}``...), ``math`` takes LaTeX.
    Colors are ink names (black, blue, red, purple, green, gray) or #hex.
    """

    def __init__(self, paper="goodnotes", style="cursive", seed=1, footer=None, accent="blue",
                 title=None, **overrides):
        self.paper = get_paper(paper)
        self.style = get_style(style, **overrides)
        self.W, self.H = self.paper.size_px
        self.size = self.paper.text_size
        self.M = self.paper.margin
        self.G = self.paper.pitch
        self.pen = self.paper.pen_scale
        self.seed = seed
        self.footer = footer
        self.doc_title = title
        self.accent = ink(accent)
        self.pages = []
        self.new_page()

    # ------------------------------------------------------------------ pages
    def new_page(self):
        self.hand = Hand(self.style, self.seed * 1000 + len(self.pages))
        self._margin_ou = _OU(self.hand.rng, 0.78)
        self.page = {"highlights": [], "strokes": [], "inked": []}
        self.pages.append(self.page)
        self.cursor = self.G * 2.2
        self._last_desc = 0.0

    page_break = new_page

    def _bottom(self):
        return self.H - self.G * 2.5

    def _empty(self):
        return not (self.page["inked"] or self.page["strokes"] or self.page["highlights"])

    def _ensure(self, height):
        if self.cursor + height > self._bottom() and not self._empty():
            self.new_page()

    def _baseline(self, asc):
        # handwriting may touch the descenders of the line above, but never sits under a
        # fraction, an integral or a long subscript
        tol = 0.35 * self.size if self.paper.ruling == "grid" else 0.0
        if self._last_desc > 0.6 * self.size:
            tol = 0
        snapped = math.ceil((self.cursor + asc + 3 - tol) / self.G) * self.G
        bj = self.style.get("baseline_jitter", 0.0)
        if bj > 0:
            off = max(-0.14 * self.G, min(0.14 * self.G, self.hand.rng.gauss(0, bj * self.G)))
            snapped += off
        return snapped

    def _margin_offset(self):
        mj = self.style.get("margin_jitter", 0.0)
        if mj <= 0:
            return 0.0
        return max(-0.35 * self.M, min(0.45 * self.M, self._margin_ou.step(mj)))

    def place(self, item, x, y):
        for c, svg in item.at(x, y):
            if c == "__highlight__":
                self.page["highlights"].append(svg)
            else:
                self.page["inked"].append((c, svg))

    def _line(self, item, x, before=0, jitter_margin=True):
        self.cursor += before
        base = self._baseline(item.asc)
        if base + item.desc > self._bottom() and not self._empty():
            self.new_page()
            base = self._baseline(item.asc)
        mx = x + (self._margin_offset() if jitter_margin else 0.0)
        # Keep squeezed lines inside the right edge
        if mx + item.w > self.W - self.M * 0.4:
            mx = max(self.M * 0.5, min(mx, self.W - self.M * 0.4 - item.w))
        self.place(item, mx, base)
        self.cursor = base + item.desc
        self._last_desc = item.desc
        return base

    def _fit(self, build, size, room, what, progressive=False):
        """Build an item, squeezing the tail first (progressive) or shrinking to fit room px."""
        it = build(size)
        if it.w <= room:
            return it
        if progressive and isinstance(it, Row) and len(it.items) >= 3:
            squeezed = it.progressive_squeeze(room, rng=self.hand.rng, min_scale=MIN_FIT)
            if squeezed.w <= room:
                return squeezed
            it = squeezed
        k = 1.0
        while it.w > room and k > MIN_FIT:
            k = max(MIN_FIT, k * max(0.85, room / it.w * 0.98))
            it = build(size * k)
        if it.w > room:
            log.warning("line wider than the page even at %d%% size: %s", MIN_FIT * 100, what[:60])
        return it

    # ---------------------------------------------------------------- content
    def space(self, lines=1):
        self.cursor += lines * self.G

    def title(self, s, color=None, underline=False):
        if self.doc_title is None:
            self.doc_title = s
        color = ink(color) if color else self.accent
        self._ensure(self.G * 4)
        size = self.size * 1.2
        it = self._fit(lambda t: Row(parse(self.hand, s, t, color)), size, self.W - 2 * self.M, s)
        base = self._line(it, self.M, before=self.G * 0.6)
        y = base + 0.3 * size
        if underline:
            self.page["inked"].append(stroke(self.hand, [(self.M - 4, y), (self.M + it.w * 0.9, y + 1.5)],
                                             color, self.style["pen_width"] * size))
        self.cursor = y + 4

    def heading(self, s, color=None):
        color = ink(color or "purple")
        self._ensure(self.G * 3)
        it = self._fit(lambda t: Row(parse(self.hand, s, t, color)), self.size * 1.08, self.W - 2 * self.M, s)
        self._line(it, self.M, before=self.G * 0.3)

    def _false_start(self, word, size, color):
        """Occasional 2-3 letter false start crossed out with a quick hand stroke."""
        r = self.hand.rng
        prefix = word[:r.choice((2, 3))]
        it = Row(parse(self.hand, prefix, size, color))
        pw = self.style["pen_width"] * size
        ym = -0.30 * size + r.uniform(-0.08, 0.08) * size
        # Quick horizontal/slightly diagonal strikethrough
        it.parts.append(stroke(
            self.hand,
            [(-0.06 * size, ym + r.uniform(-0.06, 0.06) * size),
             (it.w + 0.08 * size, ym + r.uniform(-0.08, 0.08) * size)],
            color, pw * 0.95,
        ))
        if r.random() < 0.45:
            it.parts.append(stroke(
                self.hand,
                [(-0.02 * size, ym + 0.14 * size),
                 (it.w + 0.04 * size, ym - 0.14 * size)],
                color, pw * 0.85,
            ))
        return it

    def text(self, s, indent=0, hanging=0, color="black", size=None, markup=True):
        """Paragraph with word wrap. ``hanging`` indents the lines after the first.

        With ``markup=False`` the string is taken literally (no subscripts or commands).
        """
        color, size = ink(color), size or self.size
        if not markup:
            s = escape_text(s)
        width = self.W - 2 * self.M - indent
        space = 0.42 * size
        corr_rate = self.style.get("correction_rate", 0.0)
        line, w, first = [], 0.0, True
        for word in words(s):
            room = width - (0 if first else hanging)
            # Subtle occasional false-start + strikethrough on plain alphabetic prose words
            if (corr_rate > 0 and len(word) >= 5 and word.isalpha()
                    and self.hand.rng.random() < corr_rate):
                fs = self._false_start(word, size, color)
                if line and w + space + fs.w > room:
                    self._line(Row(line, space), self.M + indent + (0 if first else hanging))
                    line, w, first = [], 0.0, False
                w += (space if line else 0) + fs.w
                line.append(fs)
            it = self._fit(lambda t: Row(parse(self.hand, word, t, color)), size, room, word)
            if line and w + space + it.w > room:
                self._line(Row(line, space), self.M + indent + (0 if first else hanging))
                line, w, first = [], 0.0, False
            w += (space if line else 0) + it.w
            line.append(it)
        if line:
            self._line(Row(line, space), self.M + indent + (0 if first else hanging))

    def eq(self, s, indent=36, center=False, box=False, color="black", box_color="red", size=None):
        """One line of penpad math markup. Too wide? Squeezes the tail first, like on paper."""
        color, box_color, size = ink(color), ink(box_color), size or self.size
        indent = 0 if center else indent * self.size / 24
        room = self.W - 2 * self.M - indent
        if box:
            inner_room = max(room - 0.8 * size, room * 0.85)
            inner = self._fit(lambda t: Row(parse(self.hand, s, t, color)), size, inner_room, s, progressive=True)
            it = Box(self.hand, inner, size, box_color)
        else:
            it = self._fit(lambda t: Row(parse(self.hand, s, t, color)), size, room, s, progressive=True)
        x = (self.W - it.w) / 2 if center else self.M + indent
        self._line(it, x, before=4 if it.asc > size else 0, jitter_margin=not center)

    def math(self, latex, **kw):
        """Like eq, but takes LaTeX: ``nb.math(r"\\int_0^1 x^2\\,dx = \\frac{1}{3}")``."""
        self.eq(latex_to_markup(latex), **kw)

    def figure(self, height, draw):
        """Reserve height px and call draw(notebook, x0, y0) to sketch inside it."""
        self._ensure(height + self.G)
        y0 = math.ceil((self.cursor + self.G * 0.5) / self.G) * self.G
        draw(self, self.M, y0)
        self.cursor = y0 + height

    def step(self, s, indent=18, color="graphite", bar_color="#718096", size=None, markup=True):
        """Intermediate algebraic step / scratchpad note in graphite with a vertical left bar."""
        self.steps([s], indent=indent, color=color, bar_color=bar_color, size=size, markup=markup)

    aside = step

    def steps(self, lines, indent=18, color="graphite", bar_color="#718096", size=None, markup=True):
        """Multi-line algebraic derivation block sharing one continuous graphite left bar."""
        if not lines:
            return
        sz = size or self.size * 0.86
        ind = indent * self.size / 24.0
        bar_x = self.M + ind * 0.45
        bar_w = max(1.4, self.style["stroke_width"] * self.pen * 0.9)
        bar_col = ink(bar_color)

        def draw_bar(page_obj, y_top, y_bot):
            if y_bot <= y_top:
                return
            d = hand_line(bar_x, y_top, bar_x, y_bot, self.hand.rng,
                          min(self.style["roughness"] * 0.4, 0.7), 0.2)
            page_obj["strokes"].append(
                '<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                'stroke-linecap="round" opacity="0.55"/>' % (d, bar_col, bar_w)
            )

        seg_page = self.page
        seg_top = None
        seg_bot = None
        for ln in lines:
            before_page = self.page
            before_cursor = self.cursor
            self.text(ln, indent=ind, color=color, size=sz, markup=markup)
            if self.page is not before_page:
                if seg_top is not None:
                    draw_bar(seg_page, seg_top, seg_bot)
                seg_page = self.page
                seg_top = self.G * 2.2
                seg_bot = self.cursor
            else:
                if seg_top is None:
                    seg_top = max(self.G * 2.2, before_cursor + 2)
                seg_bot = self.cursor
        if seg_top is not None:
            draw_bar(seg_page, seg_top, seg_bot)

    def svg_figure(self, svg_text, height=None):
        """Embed a pure vector <svg> diagram into the Diagrams layer, scaled to fit the page."""
        try:
            root = ET.fromstring(svg_text.strip())
        except ET.ParseError as e:
            log.warning("invalid inline SVG diagram: %s", e)
            return
        vb = root.attrib.get("viewBox") or root.attrib.get("viewbox")
        vx = vy = 0.0
        if vb:
            nums = [float(x) for x in re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)", vb)]
            if len(nums) == 4:
                vx, vy, vw, vh = nums
            else:
                vw, vh = 400.0, 200.0
        else:
            def _dim(val, fallback):
                m = re.search(r"[+-]?(?:\d+\.?\d*|\.\d+)", val or "")
                return float(m.group(0)) if m else fallback
            vw = _dim(root.attrib.get("width"), 400.0)
            vh = _dim(root.attrib.get("height"), 200.0)
        if vw <= 0 or vh <= 0:
            return
        max_w = self.W - 2 * self.M
        scale = min(max_w / vw, (height / vh) if height else (max_w / vw))
        draw_w = vw * scale
        draw_h = vh * scale
        self._ensure(draw_h + self.G)
        y0 = math.ceil((self.cursor + self.G * 0.5) / self.G) * self.G
        x0 = self.M + (max_w - draw_w) / 2.0
        inner = "".join(ET.tostring(child, encoding="unicode") for child in root)
        tx = x0 - vx * scale
        ty = y0 - vy * scale
        self.page["strokes"].append(
            '<g transform="translate(%.2f %.2f) scale(%.5f)">%s</g>' % (tx, ty, scale, inner)
        )
        self.cursor = y0 + draw_h

    # ------------------------------------------------------- sketch primitives
    def write(self, x, y, s, size=None, color="black", anchor="start"):
        color, size = ink(color), size or self.size * 0.85
        it = Row(parse(self.hand, s, size, color))
        dx = {"start": 0, "middle": -it.w / 2, "end": -it.w}[anchor]
        self.place(it, x + dx, y)

    def line(self, x1, y1, x2, y2, color="black", width=None, dashed=False, roughness=None, bow=None):
        width = width or self.style["stroke_width"] * self.pen
        rough = self.style["roughness"] if roughness is None else roughness
        bow = self.style["bow"] if bow is None else bow
        d = hand_line(x1, y1, x2, y2, self.hand.rng, rough, bow)
        dash = ' stroke-dasharray="9 7"' if dashed else ""
        self.page["strokes"].append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                                    'stroke-linecap="round"%s/>' % (d, ink(color), width, dash))

    def arrow(self, x1, y1, x2, y2, color="black", width=None, head=11):
        width = width or self.style["stroke_width"] * self.pen * 0.9
        rough = min(self.style["roughness"] * 0.5, 1.4)
        self.line(x1, y1, x2, y2, color, width, roughness=rough, bow=0.45)
        a = math.atan2(y2 - y1, x2 - x1)
        for s in (-0.45, 0.45):
            self.line(x2, y2, x2 - head * math.cos(a + s), y2 - head * math.sin(a + s), color, width,
                      roughness=rough, bow=0.45)

    def curve(self, pts, color="black", width=None, closed=False, dashed=False):
        """Smooth Catmull-Rom curve through the points, with a slight tremor."""
        width = width or self.style["stroke_width"] * self.pen
        r = self.hand.rng
        q = [(x + r.uniform(-0.8, 0.8), y + r.uniform(-0.8, 0.8)) for x, y in pts]
        ext = q + q[:3] if closed else [q[0]] + q + [q[-1]]
        d = "M%.1f %.1f" % ext[1]
        for i in range(1, len(ext) - 2):
            p0, p1, p2, p3 = ext[i - 1], ext[i], ext[i + 1], ext[i + 2]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            d += " C%.1f %.1f %.1f %.1f %.1f %.1f" % (c1 + c2 + p2)
        dash = ' stroke-dasharray="9 7"' if dashed else ""
        self.page["strokes"].append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f" '
                                    'stroke-linecap="round" stroke-linejoin="round"%s/>' % (d, ink(color), width, dash))

    def circle(self, cx, cy, r, color="black", width=None):
        n = 14
        pts = [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        self.curve(pts, color, width, closed=True)

    # ------------------------------------------------------------------ output
    def _pages_with_footers(self):
        out = []
        for n, page in enumerate(self.pages, 1):
            inked = list(page["inked"])
            if self.footer:
                hand = Hand(self.style, self.seed * 7919 + n)
                svg, _ = hand.write(self.W - self.M, self.H - self.G * 1.2, "%s  %d" % (self.footer, n),
                                    ink("gray"), self.size * 0.7, "end")
                inked.append((ink("gray"), svg))
            out.append({
                "highlights": list(page["highlights"]),
                "strokes": list(page["strokes"]),
                "inked": inked,
            })
        return out

    def _jobs(self, dpi):
        paper_svg = self.paper.svg()
        return [
            (self.W, self.H, paper_svg, p["highlights"], p["strokes"], p["inked"],
             self.style, self.paper.width_pt, dpi)
            for p in self._pages_with_footers()
        ]

    def render_page(self, index=0, dpi=raster.DEFAULT_DPI):
        """Rasterize a single page (0-based) to a PIL image."""
        return _render_page(self._jobs(dpi)[index])

    def render(self, dpi=raster.DEFAULT_DPI, jobs=1):
        """Render every page to a PIL image. jobs > 1 renders pages in parallel processes."""
        work = self._jobs(dpi)
        jobs = min(jobs or os.cpu_count() or 1, len(work))
        if jobs <= 1:
            return [_render_page(w) for w in work]
        with ProcessPoolExecutor(jobs) as pool:
            return list(pool.map(_render_page, work))

    def render_pdf_bytes(self, vector_pdf=True, dpi=raster.DEFAULT_DPI, jobs=1, layers=False) -> bytes:
        """Return the document as PDF bytes (pure vector by default, or rasterized if vector_pdf=False)."""
        if vector_pdf:
            return vector.build_pdf_bytes(
                self._pages_with_footers(), self.paper.width_pt, self.paper.height_pt,
                self.W, self.H, self.paper.svg(), title=self.doc_title, layers=layers,
            )
        import io
        images = self.render(dpi, jobs)
        buf = io.BytesIO()
        images[0].save(buf, format="PDF", save_all=True, append_images=images[1:],
                       resolution=images[0].width / (self.paper.width_pt / 72))
        return buf.getvalue()

    def save(self, path, dpi=raster.DEFAULT_DPI, jobs=1, vector_pdf=True, layers=False):
        """Save as .pdf (pure vector by default), .svg (layered vector) or .png (one file per page)."""
        root, ext = os.path.splitext(path)
        ext_l = ext.lower()
        if ext_l == ".pdf":
            if vector_pdf:
                vector.save_pdf(
                    self._pages_with_footers(), path, self.paper.width_pt, self.paper.height_pt,
                    self.W, self.H, self.paper.svg(), title=self.doc_title, layers=layers,
                )
                return [path]
            images = self.render(dpi, jobs)
            raster.save_pdf(images, path, self.paper.width_pt)
            return [path]
        if ext_l == ".svg":
            return vector.save_svg(self._pages_with_footers(), path, self.W, self.H, self.paper.svg())
        images = self.render(dpi, jobs)
        if len(images) == 1:
            images[0].save(path)
            return [path]
        paths = ["%s-%02d%s" % (root, n, ext) for n in range(1, len(images) + 1)]
        for im, p in zip(images, paths):
            im.save(p)
        return paths
