"""Measure a page you wrote by hand and compare it with the bundled fonts.

    penpad calibrate my_page.pdf --out calibration

Writes calibration/measures.json (page size, paper and grid colors, ink colors, as
Paper and style keys) and calibration/comparison.png (a strip of your page, then the
same phrase in the current style and in every font). Needs ``pip install penpad[calibrate]``.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import Counter

from PIL import Image, ImageDraw

from . import fonts
from .notebook import Notebook
from .paper import Paper

DPI = 300   # lower resolutions blend the grid into the ink edges


def _page_image(pdf, page):
    try:
        import pypdfium2 as pdfium
    except ImportError as e:
        raise ImportError("calibrate needs pypdfium2: pip install 'penpad[calibrate]'") from e
    doc = pdfium.PdfDocument(pdf)
    return doc[page - 1].render(scale=DPI / 72).to_pil().convert("RGB")


def _hex(c):
    return "#%02x%02x%02x" % c


def _blend(c, paper, inkc):
    """True if c is the ink lightened by the paper (anti-aliased edge)."""
    d = [t - f for t, f in zip(inkc, paper)]
    norm = sum(v * v for v in d)
    if not norm:
        return False
    k = sum((x - f) * v for x, f, v in zip(c, paper, d)) / norm
    rest = sum((x - (f + k * v)) ** 2 for x, f, v in zip(c, paper, d)) ** 0.5
    return 0 < k < 1 and rest < 25


def measure(im):
    W, H = im.size
    px = im.load()
    paper = Counter(im.getdata()).most_common(1)[0][0]

    def grayish(p):
        return max(p) - min(p) < 22 and sum(paper) - sum(p) > 25

    steps, grid_colors, widths = [], Counter(), []
    for fy in (0.15, 0.5, 0.85, 0.95):
        y = int(H * fy)
        xs = [x for x in range(W) if grayish(px[x, y])]
        groups = []
        for x in xs:
            if groups and x - groups[-1][-1] <= 1:
                groups[-1].append(x)
            else:
                groups.append([x])
        centers = [sum(g) / len(g) for g in groups if len(g) <= 4]
        steps += [b - a for a, b in zip(centers, centers[1:]) if 8 < b - a < 240]
        widths += [len(g) for g in groups if len(g) <= 4]
        grid_colors.update(px[x, y] for x in xs)
    step_px = 0
    if steps:
        mode = Counter(round(v) for v in steps).most_common(1)[0][0]
        step_px = statistics.median(v for v in steps if abs(v - mode) <= 2)
    grid = grid_colors.most_common(1)[0][0] if grid_colors else (210, 210, 210)

    # pen colors: flat inks are exact and frequent; edge pixels are many rare blends
    inkpx = [p for p in im.getdata() if sum(abs(a - b) for a, b in zip(p, paper)) > 90 and not grayish(p)
             or sum(p) < 200]
    inks = []
    for c, n in Counter(inkpx).most_common(40):
        if n < 0.01 * len(inkpx):
            break
        if all(sum(abs(a - b) for a, b in zip(c, t)) > 60 for t in inks) and not any(_blend(c, paper, t) for t in inks):
            inks.append(c)

    rule = (sum(widths) / len(widths) / DPI * 72) if widths else 0.6
    return {
        "paper": {"width_pt": round(W / DPI * 72, 2), "height_pt": round(H / DPI * 72, 2),
                  "step_pt": round(step_px / DPI * 72, 2), "color": _hex(grid), "rule_pt": round(rule, 2),
                  "background": _hex(paper)},
        "inks": [_hex(c) for c in inks],
        "note": "Exported PDFs often have a cream background; on the tablet it may look white (#ffffff).",
    }


def _densest_band(im, height=0.12):
    W, H = im.size
    g = im.convert("L").point(lambda v: 255 if v < 140 else 0)
    rows = [sum(g.crop((0, y, W, y + 1)).getdata()) for y in range(H)]
    win = int(H * height)
    s = sum(rows[:win])
    best, y0 = s, 0
    for y in range(1, H - win):
        s += rows[y + win - 1] - rows[y - 1]
        if s > best:
            best, y0 = s, y
    return y0 / H, (y0 + win) / H


def _sample(phrase, label, color, **style):
    paper = Paper(455.04, 40, "blank", 16, margin_pt=6)
    nb = Notebook(paper=paper, seed=5, **style)
    nb.cursor = 0
    nb.text(phrase, color=color, markup=False)
    im = nb.render()[0].resize((900, int(900 * 40 / 455.04)))
    ImageDraw.Draw(im).text((892, im.height - 4), label, fill=(140, 140, 140), anchor="rd")
    return im


def calibrate(pdf, page=1, band=None, phrase="", out="calibration"):
    os.makedirs(out, exist_ok=True)
    im = _page_image(pdf, page)
    m = measure(im)
    with open(os.path.join(out, "measures.json"), "w") as f:
        json.dump(m, f, indent=2)
    print(json.dumps(m, indent=2))

    W, H = im.size
    y0, y1 = band or _densest_band(im)
    strip = im.crop((0, int(y0 * H), W, int(y1 * H)))
    strip = strip.resize((900, int(strip.height * 900 / W)))
    color = m["inks"][0] if m["inks"] else "black"
    rows = [_sample(phrase, "current style", color)]
    for name in sorted(fonts.FONTS):
        rows.append(_sample(phrase, name, color, font=name, alternates=[], pinned={}, isolated_font=None))
    sheet = Image.new("RGB", (900, strip.height + 8 + sum(r.height for r in rows)), "white")
    sheet.paste(strip, (0, 0))
    y = strip.height + 8
    for r in rows:
        sheet.paste(r, (0, y))
        y += r.height
    path = os.path.join(out, "comparison.png")
    sheet.save(path)
    print("comparison saved to", path)
