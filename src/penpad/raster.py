"""SVG layers -> page image, with a soft ink edge. Uses resvg (pure pip wheel, no system libraries)."""

from __future__ import annotations

import io

import resvg_py
from PIL import Image, ImageFilter

DEFAULT_DPI = 225


def _svg(w, h, body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">%s</svg>'
            % (w, h, w, h, body))


def rasterize(w, h, body, width_px, background=None) -> Image.Image:
    data = resvg_py.svg_to_bytes(svg_string=_svg(w, h, body), width=int(width_px),
                                 background=background, skip_system_fonts=True)
    return Image.open(io.BytesIO(bytes(data))).convert("RGBA")


def compose(w, h, paper_svg, strokes, inked, style, width_pt, dpi=DEFAULT_DPI) -> Image.Image:
    """Paper, then crisp strokes, then one soft-edged ink layer per color.

    ``inked`` is a list of (color, svg) pairs; handwriting goes there.
    """
    out_w = max(1, round(width_pt / 72 * dpi))
    ss = style.get("supersample", 2.0)
    big = out_w * ss
    img = rasterize(w, h, paper_svg, big, "white")
    if strokes:
        img = Image.alpha_composite(img, rasterize(w, h, "".join(strokes), big))
    by_color = {}
    for color, svg in inked:
        by_color.setdefault(color, []).append(svg)
    radius = style["ink_softness"] * ss * dpi / DEFAULT_DPI
    gain = style["ink_gain"]
    for color, parts in by_color.items():
        layer = rasterize(w, h, "".join(parts), big)
        alpha = layer.split()[3]
        if radius > 0:
            alpha = alpha.filter(ImageFilter.GaussianBlur(radius))
        alpha = alpha.point(lambda v: min(255, int(v * gain)))
        rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        ink = Image.new("RGBA", layer.size, rgb + (0,))
        ink.putalpha(alpha)
        img = Image.alpha_composite(img, ink)
    return img.convert("RGB").resize((out_w, round(out_w * h / w)), Image.LANCZOS)


def save_pdf(images, path, width_pt):
    dpi = images[0].width / (width_pt / 72)
    images[0].save(path, save_all=True, append_images=images[1:], resolution=dpi)
    return path
