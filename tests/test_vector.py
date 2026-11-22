"""Tests for pure-vector PDF output, PDF layers, metadata, highlights, algebraic steps and SVG diagrams."""

import re
import zlib

import pypdfium2 as pdfium
import pytest

from penpad import Notebook, PAPERS, render_markdown
from penpad.cli import main

SAMPLE_MD = r"""# EST-57 - Lista de Exercícios 3

## 1) Divergência da seção típica
Aerodinâmica estacionária linear, pequenas rotações, flap travado em $\beta_e = 0$ e $\partial c_{mac}/\partial \alpha = 0$.

(a) Incremento de sustentação no CA ($e = x_{ea} - x_{ac}$):
$$ \delta L = \bar{q} S c_{l\alpha} \delta\alpha_e \Rightarrow K_\alpha \delta\alpha_e = e \delta L $$

| Passando o momento aerodinâmico para a esquerda e colocando $\delta\alpha_e$ em evidência:
| $(K_\alpha - \bar{q} S e c_{l\alpha})\delta\alpha_e = 0 \Rightarrow K_{\alpha,\text{ef}} = K_\alpha - \bar{q} S e c_{l\alpha}$

==Sentido físico:== A sustentação atua como uma mola torcional negativa ($-\bar{q} S e c_{l\alpha}$) que reduz a rigidez efetiva conforme $\bar{q}$ cresce.

```svg
<svg viewBox="0 0 300 90">
  <line x1="20" y1="45" x2="280" y2="45" stroke="#1a2b4c" stroke-width="2" stroke-linecap="round"/>
  <circle cx="90" cy="45" r="5" fill="#f33f3f"/>
  <path d="M 90 45 L 90 12" fill="none" stroke="#f33f3f" stroke-width="2" stroke-linecap="round"/>
  <rect x="170" y="37" width="16" height="16" rx="3" fill="none" stroke="#1a2b4c" stroke-width="2"/>
</svg>
```
"""


def test_vector_pdf_has_zero_bitmaps_exact_size_clean_metadata_and_flat_geometry(tmp_path):
    nb = render_markdown(SAMPLE_MD, paper="a4-lines", style="casual", seed=7)
    pdf_path = tmp_path / "lista03_vetorial.pdf"
    nb.save(str(pdf_path))

    raw = pdf_path.read_bytes()
    assert raw.startswith(b"%PDF-1.4")
    assert b"/Subtype /Image" not in raw
    # No tool fingerprinting in metadata or layer structure by default
    assert b"penpad" not in raw.lower()
    assert b"/Creator" not in raw
    assert b"/Producer" not in raw
    assert b"/OCProperties" not in raw
    for layer in (b"(Paper)", b"(Highlights)", b"(Diagrams)", b"(Handwriting)"):
        assert layer not in raw
    assert b"/Title " in raw

    # Inspect decompressed page content stream: baked page coordinates, no cm, no fill+stroke (B)
    streams = re.findall(rb"stream\n(.*?)\nendstream", raw, re.S)
    assert streams
    dec = zlib.decompress(streams[0]).decode("ascii")
    lines = dec.splitlines()
    assert not any(ln.endswith(" cm") for ln in lines), "expected baked page-space coordinates without cm"
    assert not any(ln in ("B", "B*") for ln in lines), "expected pure filled strokes (f), never fill+stroke (B)"
    assert any(ln == "f" for ln in lines)

    # Verify with pdfium: exact A4 dimensions and rendered content is non-blank
    doc = pdfium.PdfDocument(str(pdf_path))
    assert len(doc) == len(nb.pages)
    w, h = doc[0].get_size()
    assert abs(w - 595.28) < 0.05
    assert abs(h - 841.89) < 0.05

    img = doc[0].render(scale=1.5).to_pil().convert("RGB")
    extrema = img.getextrema()
    # Must contain dark ink pixels (not an all-white page)
    assert min(lo for lo, _ in extrema) < 80


def test_vector_pdf_opt_in_layers(tmp_path):
    nb = render_markdown("# Hello\nworld\n", paper="a4-lines", seed=1)
    raw = nb.render_pdf_bytes(vector_pdf=True, layers=True)
    assert raw.startswith(b"%PDF-1.5")
    assert b"/OCProperties" in raw
    for layer in (b"(Paper)", b"(Highlights)", b"(Diagrams)", b"(Handwriting)"):
        assert layer in raw


def test_vector_pdf_is_compact(tmp_path):
    nb = render_markdown(SAMPLE_MD, paper="a4-lines", seed=3)
    vec_path = tmp_path / "vec.pdf"
    nb.save(str(vec_path), vector_pdf=True)
    # Pure vector page should stay well under 350 KB
    assert vec_path.stat().st_size < 350_000


@pytest.mark.parametrize("paper", sorted(PAPERS))
def test_vector_pdf_matches_every_paper_preset(paper, tmp_path):
    nb = Notebook(paper=paper, seed=2)
    nb.title("Test %s" % paper)
    nb.text("Hello ==world== with $x^2 + y^2$")
    nb.step("Intermediate step: $x = 1$")
    p = tmp_path / ("%s.pdf" % paper)
    nb.save(str(p))

    doc = pdfium.PdfDocument(str(p))
    w, h = doc[0].get_size()
    spec = PAPERS[paper]
    assert abs(w - spec.width_pt) < 0.05
    assert abs(h - spec.height_pt) < 0.05


def test_highlights_steps_and_svg_diagrams_populate_layers():
    nb = render_markdown(SAMPLE_MD, paper="a4-lines", seed=1)
    p0 = nb.pages[0]
    assert p0["highlights"], "expected ==...== to emit vector highlight strokes"
    assert p0["strokes"], "expected | steps bar and ```svg``` diagram to emit diagram strokes"
    assert p0["inked"], "expected handwriting paths"

    # Raster composition should also succeed with highlights and inline SVG
    img = nb.render_page(0, dpi=72)
    assert img.width > 100 and img.height > 100


def test_svg_export_single_and_multipage(tmp_path):
    nb1 = render_markdown("# One\n==highlight==\n", seed=1)
    out1 = nb1.save(str(tmp_path / "one.svg"))
    assert len(out1) == 1
    svg1 = (tmp_path / "one.svg").read_text(encoding="utf-8")
    assert '<g id="paper">' in svg1
    assert '<g id="highlights">' in svg1
    assert '<g id="handwriting">' in svg1

    nb2 = render_markdown("# P1\nhello\n---\n# P2\nworld\n", seed=1)
    out2 = nb2.save(str(tmp_path / "multi.svg"))
    assert len(out2) == 2
    assert (tmp_path / "multi-01.svg").exists()
    assert (tmp_path / "multi-02.svg").exists()


def test_cli_vector_svg_and_raster_flags(tmp_path, capsys):
    src = tmp_path / "notes.md"
    src.write_text(SAMPLE_MD, encoding="utf-8")

    # Default vector PDF
    vec_pdf = tmp_path / "vec.pdf"
    assert main(["render", str(src), "--paper", "a4-lines", "-o", str(vec_pdf)]) == 0
    assert b"/Subtype /Image" not in vec_pdf.read_bytes()

    # Raster PDF fallback
    ras_pdf = tmp_path / "ras.pdf"
    assert main(["render", str(src), "--paper", "a4-lines", "--raster", "--dpi", "72", "-o", str(ras_pdf)]) == 0
    assert b"/Subtype /Image" in ras_pdf.read_bytes()

    # Direct SVG export
    out_svg = tmp_path / "notes.svg"
    assert main(["render", str(src), "--paper", "a4-lines", "-o", str(out_svg)]) == 0
    assert out_svg.exists()
