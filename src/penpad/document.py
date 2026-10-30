"""Markdown-with-LaTeX notes -> Notebook.

Supported, one construct per line:

    # Title                      accent color (blue)
    ## Heading                   purple
    Plain text with $inline$ LaTeX math.
    $$ \\frac{a}{b} = c $$        equation on its own line
    $$! E = mc^2 $$              boxed result
    - bullet / * bullet          hanging indent
    1. item / (a) item / a) item hanging indent, label kept
    > note                       written in purple
    {red} any line               ink for that line (black, blue, red, purple, green, gray)
    ---                          new page
    blank line                   one empty ruling line
"""

from __future__ import annotations

import re

from .latex import escape_text, latex_to_markup
from .notebook import Notebook
from .styles import INKS

_COLOR = re.compile(r"^\{(%s|#[0-9a-fA-F]{3,6})\}\s*" % "|".join(INKS))
_ITEM = re.compile(r"^(\d+[.)]|\([a-z0-9]+\)|[a-z][.)])\s+(.*)$")
_DISPLAY = re.compile(r"^\$\$(!?)\s*(.*?)\s*\$\$$")


def inline(s: str) -> str:
    """Escape literal text, translate $...$ spans from LaTeX."""
    out, pos = [], 0
    for m in re.finditer(r"(?<!\\)\$(.+?)(?<!\\)\$", s):
        out.append(escape_text(s[pos:m.start()].replace("\\$", "$")))
        out.append(latex_to_markup(m.group(1)))
        pos = m.end()
    out.append(escape_text(s[pos:].replace("\\$", "$")))
    return "".join(out)


def render_markdown(source: str, notebook: Notebook | None = None, **kw) -> Notebook:
    nb = notebook or Notebook(**kw)
    hang = 1.6 * nb.size
    blank = 0
    lines = source.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip()
        i += 1
        # multi-line display math: $$ on its own line
        if raw.strip() in ("$$", "$$!"):
            boxed = raw.strip() == "$$!"
            body = []
            while i < len(lines) and lines[i].strip() != "$$":
                body.append(lines[i].strip())
                i += 1
            i += 1
            raw = "$$%s %s $$" % ("!" if boxed else "", " ".join(body))
        line = raw.strip()
        if not line:
            blank += 1
            if blank == 1 and nb.cursor > nb.G * 2.5:
                nb.space(1)
            continue
        blank = 0
        color = "black"
        m = _COLOR.match(line)
        if m:
            color, line = m.group(1), line[m.end():]
        if line.startswith("<!--"):
            continue
        if line in ("---", "***", "___"):
            nb.page_break()
            continue
        if line.startswith("## "):
            nb.heading(inline(line[3:]), color=None if color == "black" else color)
            continue
        if line.startswith("# "):
            nb.title(inline(line[2:]), color=None if color == "black" else color)
            continue
        m = _DISPLAY.match(line)
        if m:
            nb.eq(latex_to_markup(m.group(2)), box=bool(m.group(1)), color=color)
            continue
        if line.startswith("> "):
            nb.text(inline(line[2:]), color="purple" if color == "black" else color)
            continue
        if line[:2] in ("- ", "* "):
            nb.text(inline("– " + line[2:]), hanging=hang * 0.6, color=color)
            continue
        m = _ITEM.match(line)
        if m:
            nb.text(inline(m.group(1) + " " + m.group(2)), hanging=hang, color=color)
            continue
        nb.text(inline(line), color=color)
    return nb
