"""Regenerate the README images: python docs/make_gallery.py"""

import os
import runpy
import sys

from PIL import Image, ImageDraw

from penpad import PAPERS, Notebook, Paper, render_markdown

HERE = os.path.dirname(os.path.abspath(__file__))
IMG = os.path.join(HERE, "img")
ROOT = os.path.dirname(HERE)
os.makedirs(IMG, exist_ok=True)


def crop(im, top, bottom):
    return im.crop((0, int(im.height * top), im.width, int(im.height * bottom)))


def save(im, name, width=None):
    if width and im.width > width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    im.save(os.path.join(IMG, name), optimize=True)


def label(im, text):
    ImageDraw.Draw(im).text((12, im.height - 10), text, fill=(120, 120, 120), anchor="ld")
    return im


with open(os.path.join(ROOT, "examples", "physics-notes.md"), encoding="utf-8") as f:
    notes = f.read()

# hero: the physics example on a Goodnotes page
nb = render_markdown(notes, seed=4)
save(crop(nb.render()[0], 0.06, 0.93), "hero.png", 1100)

# the three styles
short = notes.split("(a)")[0]
tiles = []
for style, paper in (("cursive", "goodnotes"), ("print", "a4-lines"), ("casual", "a4-dots")):
    im = render_markdown(short, style=style, paper=paper, seed=4).render(dpi=150)[0]
    tiles.append(label(crop(im, 0.0, 0.5), "--style %s  --paper %s" % (style, paper)))
h = min(t.height for t in tiles)
tiles = [t.resize((round(t.width * h / t.height), h)) for t in tiles]
sheet = Image.new("RGB", (sum(t.width for t in tiles) + 20 * (len(tiles) - 1), h), "white")
x = 0
for t in tiles:
    sheet.paste(t, (x, 0))
    x += t.width + 20
save(sheet, "styles.png", 1600)

# variation: one sentence, written six times by the same hand
strip = Paper(455.04, 128, "grid", 16, margin_pt=10)
nb = Notebook(paper=strip, seed=8)
nb.cursor = 0
for _ in range(6):
    nb.text("Same hand, never the same letter twice: 0123456789", markup=False)
save(nb.render()[0], "variation.png", 1100)

# paper presets
thumbs = []
for name in ("goodnotes", "a4-grid", "a4-lines", "a4-dots", "letter-lines", "remarkable"):
    nb = Notebook(paper=name, seed=2)
    nb.title(name)
    nb.text("Quick brown fox, E = mc^2.")
    im = nb.render(dpi=72)[0]
    thumbs.append(crop(im, 0, 0.35).resize((300, round(300 * im.height * 0.35 / im.width))))
h = max(t.height for t in thumbs)
sheet = Image.new("RGB", (len(thumbs) * 310, h), "white")
for i, t in enumerate(thumbs):
    sheet.paste(t, (i * 310, 0))
save(sheet, "papers.png")

# sketch example (the script takes the output path)
sys.argv = ["sketch.py", os.path.join(IMG, "sketch-full.png")]
runpy.run_path(os.path.join(ROOT, "examples", "sketch.py"))
full = Image.open(sys.argv[1])
save(crop(full, 0.04, 0.62), "sketch.png", 1100)
os.remove(sys.argv[1])
print("gallery written to", IMG)
