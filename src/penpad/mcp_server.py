"""penpad as an MCP server: any MCP client (Claude, ChatGPT, editors) can write handwritten pages.

    pip install "penpad[mcp]"
    penpad mcp                       # stdio, for Claude Desktop / Claude Code
    penpad mcp --http --port 8000    # streamable HTTP, PDFs come back embedded in the result

The assistant does the reading and summarizing (lecture slides, Classroom material, a chat);
penpad only turns its Markdown into pages the student imports into Goodnotes, Notability,
reMarkable and so on.
"""

from __future__ import annotations

import base64
import io
import logging
import os
import re
import threading
import unicodedata

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import BlobResourceContents, EmbeddedResource, TextContent, ToolAnnotations

from . import __version__, raster
from .document import render_markdown
from .paper import PAPERS
from .styles import INKS, STYLES

SYNTAX = r"""Markdown, one construct per line:
  # Title                 (blue)        ## Heading   (purple)
  plain text with $inline$ LaTeX math and ==vector highlight==
  $$ display equation $$                $$! boxed result $$
  - bullet   1. item   (a) item         > note       (purple)
  | intermediate algebraic step         (graphite ink + vertical left bar)
  ```svg ... ```                        pure vector SVG diagram (use stroke-linecap="round")
  {red} any line in that ink: black, navy, blue, red, purple, green, graphite, gray
  ---  new page                         blank line = one empty ruling line
LaTeX subset: \frac, \int_a^b, \oint, \sum, \sqrt, \sqrt[n], \vec, \hat, \dot, \ddot, \bar,
\left( \right), Greek letters, \partial, \nabla, \cdot, \times, \leq, \geq, \neq, \approx,
\infty, \to, \Rightarrow, \text{...}, sin/cos/log/lim/... Not supported: matrices,
\begin{...} environments, \mathbb, tables, images, code blocks, bold/italic markers.
Write like a thorough student:
1. Never skip intermediate algebra: show derivations, substitutions, product rules and
   rearrangements line by line in `| ...` graphite blocks.
2. Separate math from physical intuition: after each key result, add a short line with
   `==Physical meaning:==` explaining the mechanism, what cancels and which assumption matters.
3. Draw free-body diagrams and plots directly as ```svg``` blocks with stroke-linecap="round".
Long lines shrink to fit, so split long equations into steps."""

INSTRUCTIONS = """penpad turns Markdown + LaTeX notes into 100% vector, editable handwritten notebook pages (PDF)
with separate layers for paper ruling, highlights, diagrams and handwriting.
Use it when the user wants study notes, summaries, worked solutions or flashcards as
handwritten pages for a tablet notebook app (Goodnotes, Notability, reMarkable, Kindle
Scribe, Boox) or for printing. You write the content; render_notes draws it.

""" + SYNTAX + """

After rendering, look at the preview and the warnings. If a line was too wide or a LaTeX
command was unsupported, rewrite that part and render again. Then tell the user where the
PDF is and that they can import it into their notebook app and annotate on top of it.
Do not use penpad to pass generated pages off as the user's own handwriting where
handwriting is required (exams, handwritten assignments, signatures)."""

PREVIEW_WIDTH = 640   # px; enough for the model to read the page, cheap in tokens

server = MCPServer("penpad", title="penpad handwritten notes", version=__version__,
                   instructions=INSTRUCTIONS, website_url="https://felipefrf.com/penpad/")

_lock = threading.Lock()     # one render at a time: rendering is CPU bound and logs are captured
_embed_default = False       # set by main(): over HTTP the client cannot read our disk


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        msg = record.getMessage()
        if msg not in self.messages:
            self.messages.append(msg)


def output_dir() -> str:
    d = os.environ.get("PENPAD_OUTPUT_DIR")
    if not d:
        docs = os.path.expanduser("~/Documents")
        d = os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "penpad")
    os.makedirs(d, exist_ok=True)
    return d


def _slug(s: str) -> str:
    s = re.sub(r"\$[^$]*\$", "", s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w\s-]", "", s).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:60].strip("-")


def _filename(markdown: str, filename: str | None) -> str:
    if filename:
        name = _slug(os.path.splitext(os.path.basename(filename))[0])
    else:
        title = next((ln.lstrip("#").strip() for ln in markdown.splitlines() if ln.startswith("#")), "")
        name = _slug(title)
    return (name or "notes") + ".pdf"


def _unique(path: str) -> str:
    root, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = "%s-%d%s" % (root, n, ext)
        n += 1
    return path


def render(markdown, paper="goodnotes", style="cursive", seed=1, footer=None,
           dpi=raster.DEFAULT_DPI, preview=True):
    """Markdown -> (vector pdf bytes, page count, preview image or None, warnings)."""
    collect = _Collect()
    log = logging.getLogger("penpad")
    with _lock:
        log.addHandler(collect)
        try:
            nb = render_markdown(markdown, paper=paper, style=style, seed=seed, footer=footer or None)
            pdf_bytes = nb.render_pdf_bytes(vector_pdf=True)
            preview_img = nb.render_page(0, dpi=dpi) if preview else None
        finally:
            log.removeHandler(collect)
    return pdf_bytes, len(nb.pages), preview_img, collect.messages


def _preview(img) -> Image:
    small = img.resize((PREVIEW_WIDTH, round(PREVIEW_WIDTH * img.height / img.width)))
    buf = io.BytesIO()
    small.convert("RGB").save(buf, format="PNG", optimize=True)
    return Image(data=buf.getvalue(), format="png")


@server.tool(
    title="Render handwritten notes",
    description="Render Markdown + LaTeX notes as realistic handwritten notebook pages (PDF). "
                "Returns where the PDF was saved (or the PDF itself over HTTP), a preview of the "
                "first page, and warnings to fix (lines too wide, unsupported LaTeX).\n\n" + SYNTAX,
    annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True,
                                open_world_hint=False),
)
def render_notes(markdown: str, filename: str | None = None, paper: str = "goodnotes",
                 style: str = "cursive", seed: int = 1, footer: str | None = None,
                 preview: bool = True, embed_pdf: bool | None = None) -> list:
    """Render the notes.

    Args:
        markdown: the notes, in penpad Markdown (see the tool description).
        filename: name for the PDF, e.g. "thermo-lecture-3". Defaults to the first title.
        paper: goodnotes (default), a4-grid, a4-lines, a4-dots, a4-blank, letter-lines,
            letter-grid, remarkable. Call list_options for details.
        style: cursive (default), print or casual.
        seed: same seed, same handwriting. Change it for a different-looking page.
        footer: text written at the bottom of every page before the page number, e.g. "Physics II".
        preview: include a small PNG of the first page so you can check the result.
        embed_pdf: return the PDF bytes in the result. Defaults to true over HTTP, false over stdio.
    """
    if paper not in PAPERS:
        raise ToolError("unknown paper %r, choose from %s" % (paper, ", ".join(PAPERS)))
    if style not in STYLES:
        raise ToolError("unknown style %r, choose from %s" % (style, ", ".join(STYLES)))
    if not markdown.strip():
        raise ToolError("markdown is empty")
    pdf, n, preview_img, warnings = render(markdown, paper, style, seed, footer, preview=preview)
    embed = _embed_default if embed_pdf is None else embed_pdf
    name = _filename(markdown, filename)
    lines = ["%d page%s, paper %s, style %s, seed %d." % (n, "" if n == 1 else "s", paper, style, seed)]
    out = []
    if not embed:
        path = _unique(os.path.join(output_dir(), name))
        with open(path, "wb") as f:
            f.write(pdf)
        lines.insert(0, "Saved %s" % path)
    if warnings:
        lines.append("Warnings (fix and render again):")
        lines += ["- " + w for w in warnings]
    else:
        lines.append("No warnings.")
    out.append(TextContent(text="\n".join(lines)))
    if preview and preview_img is not None:
        out.append(_preview(preview_img))
    if embed:
        out.append(EmbeddedResource(resource=BlobResourceContents(
            uri="penpad://notes/" + name, mime_type="application/pdf",
            blob=base64.b64encode(pdf).decode("ascii"))))
    return out


@server.tool(title="List penpad options",
             description="Handwriting styles, ink colors and paper presets penpad can draw.",
             annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def list_options() -> dict:
    return {
        "styles": list(STYLES),
        "inks": dict(INKS),
        "papers": {k: p.description for k, p in PAPERS.items()},
        "output_dir": None if _embed_default else output_dir(),
    }


@server.resource("penpad://syntax", name="syntax", title="penpad Markdown syntax",
                 mime_type="text/plain")
def syntax() -> str:
    return SYNTAX


@server.prompt(title="Handwritten study notes",
               description="Turn material (slides, a reading, a lecture transcript) into handwritten notes.")
def study_notes(material: str, focus: str = "", language: str = "the language of the material") -> str:
    focus_line = "Focus on: %s\n" % focus if focus else ""
    return ("Write complete study notes from the material below, as a thorough student would in their notebook: "
            "main ideas in short lines, definitions, key equations with a boxed final result, "
            "all intermediate algebraic steps opened line by line in `| ...` graphite blocks (never skip algebra), "
            "physical intuition highlighted with `==...==` after key results, pure ```svg``` diagrams where helpful, "
            "and a short 'check yourself' list at the end. "
            "Write in %s. Use penpad Markdown (see the render_notes tool), then call render_notes "
            "and fix any warnings.\n%s\n--- material ---\n%s" % (language, focus_line, material))


def main(http=False, host="127.0.0.1", port=8000):
    global _embed_default
    if http:
        _embed_default = True
        server.run("streamable-http", host=host, port=port)
    else:
        server.run()
