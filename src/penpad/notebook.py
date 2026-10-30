"""Notebook: multi-page handwritten documents with text, math and sketches."""

from __future__ import annotations

import logging
import math
import os
from concurrent.futures import ProcessPoolExecutor

from . import raster
from .hand import Hand, hand_line
from .latex import escape_text, latex_to_markup
from .layout import Box, Row, parse, stroke, words
from .paper import get_paper
from .styles import get_style, ink

log = logging.getLogger("penpad")

MIN_FIT = 0.62   # an equation may shrink to this fraction of the letter size to fit the line


def _render_page(args):
    w, h, paper_svg, strokes, inked, style, width_pt, dpi = args
    return raster.compose(w, h, paper_svg, strokes, inked, style, width_pt, dpi)


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

    def __init__(self, paper="goodnotes", style="cursive", seed=1, footer=None, accent="blue", **overrides):
        self.paper = get_paper(paper)
        self.style = get_style(style, **overrides)
        self.W, self.H = self.paper.size_px
        self.size = self.paper.text_size
        self.M = self.paper.margin
        self.G = self.paper.pitch
        self.pen = self.paper.pen_scale
        self.seed = seed
        self.footer = footer
        self.accent = ink(accent)
        self.pages = []
        self.new_page()

    # ------------------------------------------------------------------ pages
    def new_page(self):
        self.hand = Hand(self.style, self.seed * 1000 + len(self.pages))
        self.page = {"strokes": [], "inked": []}
        self.pages.append(self.page)
        self.cursor = self.G * 2.2
        self._last_desc = 0.0

    page_break = new_page

    def _bottom(self):
        return self.H - self.G * 2.5

    def _empty(self):
        return not (self.page["inked"] or self.page["strokes"])

    def _ensure(self, height):
        if self.cursor + height > self._bottom() and not self._empty():
            self.new_page()

    def _baseline(self, asc):
        # handwriting may touch the descenders of the line above, but never sits under a
        # fraction, an integral or a long subscript
        tol = 0.35 * self.size if self.paper.ruling == "grid" else 0.0
        if self._last_desc > 0.6 * self.size:
            tol = 0
        return math.ceil((self.cursor + asc + 3 - tol) / self.G) * self.G

    def place(self, item, x, y):
        self.page["inked"] += item.at(x, y)

    def _line(self, item, x, before=0):
        self.cursor += before
        base = self._baseline(item.asc)
        if base + item.desc > self._bottom() and not self._empty():
            self.new_page()
            base = self._baseline(item.asc)
        self.place(item, x, base)
        self.cursor = base + item.desc
        self._last_desc = item.desc
        return base

    def _fit(self, build, size, room, what):
        """Build an item, shrinking the letters (as people do) until it fits in room px."""
        it = build(size)
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

    def text(self, s, indent=0, hanging=0, color="black", size=None, markup=True):
        """Paragraph with word wrap. ``hanging`` indents the lines after the first.

        With ``markup=False`` the string is taken literally (no subscripts or commands).
        """
        color, size = ink(color), size or self.size
        if not markup:
            s = escape_text(s)
        width = self.W - 2 * self.M - indent
        space = 0.42 * size
        line, w, first = [], 0.0, True
        for word in words(s):
            room = width - (0 if first else hanging)
            it = self._fit(lambda t: Row(parse(self.hand, word, t, color)), size, room, word)
            if line and w + space + it.w > room:
                self._line(Row(line, space), self.M + indent + (0 if first else hanging))
                line, w, first = [], 0.0, False
            w += (space if line else 0) + it.w
            line.append(it)
        if line:
            self._line(Row(line, space), self.M + indent + (0 if first else hanging))

    def eq(self, s, indent=36, center=False, box=False, color="black", box_color="red", size=None):
        """One line of penpad math markup. Too wide? The letters get smaller, like on paper."""
        color, box_color, size = ink(color), ink(box_color), size or self.size
        indent = 0 if center else indent * self.size / 24

        def build(t):
            it = Row(parse(self.hand, s, t, color))
            return Box(self.hand, it, t, box_color) if box else it

        it = self._fit(build, size, self.W - 2 * self.M - indent, s)
        x = (self.W - it.w) / 2 if center else self.M + indent
        self._line(it, x, before=4 if it.asc > size else 0)

    def math(self, latex, **kw):
        """Like eq, but takes LaTeX: ``nb.math(r"\\int_0^1 x^2\\,dx = \\frac{1}{3}")``."""
        self.eq(latex_to_markup(latex), **kw)

    def figure(self, height, draw):
        """Reserve height px and call draw(notebook, x0, y0) to sketch inside it."""
        self._ensure(height + self.G)
        y0 = math.ceil((self.cursor + self.G * 0.5) / self.G) * self.G
        draw(self, self.M, y0)
        self.cursor = y0 + height

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
    def _jobs(self, dpi):
        paper_svg = self.paper.svg()
        out = []
        for n, page in enumerate(self.pages, 1):
            inked = list(page["inked"])
            if self.footer:
                hand = Hand(self.style, self.seed * 7919 + n)
                svg, _ = hand.write(self.W - self.M, self.H - self.G * 1.2, "%s  %d" % (self.footer, n),
                                    ink("gray"), self.size * 0.7, "end")
                inked.append((ink("gray"), svg))
            out.append((self.W, self.H, paper_svg, page["strokes"], inked, self.style, self.paper.width_pt, dpi))
        return out

    def render(self, dpi=raster.DEFAULT_DPI, jobs=1):
        """Render every page to a PIL image. jobs > 1 renders pages in parallel processes."""
        work = self._jobs(dpi)
        jobs = min(jobs or os.cpu_count() or 1, len(work))
        if jobs <= 1:
            return [_render_page(w) for w in work]
        with ProcessPoolExecutor(jobs) as pool:
            return list(pool.map(_render_page, work))

    def save(self, path, dpi=raster.DEFAULT_DPI, jobs=1):
        """Save as .pdf (all pages, real paper size) or .png (one file per page: name-01.png...)."""
        images = self.render(dpi, jobs)
        root, ext = os.path.splitext(path)
        if ext.lower() == ".pdf":
            raster.save_pdf(images, path, self.paper.width_pt)
            return [path]
        if len(images) == 1:
            images[0].save(path)
            return [path]
        paths = ["%s-%02d%s" % (root, n, ext) for n in range(1, len(images) + 1)]
        for im, p in zip(images, paths):
            im.save(p)
        return paths
