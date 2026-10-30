import re
import string

import pytest

from penpad import PAPERS, STYLES, Hand, Notebook, latex_to_markup, render_markdown
from penpad import fonts
from penpad.cli import main
from penpad.styles import get_style, ink

CHARSET = string.ascii_letters + string.digits + ".,;:!?()[]+-=/*'\"<>%&" + "αβγδεθλμνπρστφψωΔΣΩ∂∞±×"


def svg_of(text, seed=1, style="cursive"):
    return Hand(get_style(style), seed).write(0, 0, text, "#000000", 24)[0]


def test_same_seed_same_pixels():
    def page():
        nb = Notebook(seed=3)
        nb.text("pixel for pixel $x^2$")
        return nb.render(dpi=60)[0].tobytes()
    assert page() == page()


def test_same_seed_same_handwriting():
    assert svg_of("Reproducible 123", seed=4) == svg_of("Reproducible 123", seed=4)
    assert svg_of("Reproducible 123", seed=4) != svg_of("Reproducible 123", seed=5)


@pytest.mark.parametrize("style", sorted(STYLES))
def test_every_supported_character_has_a_glyph(style):
    hand = Hand(get_style(style), 1)
    for ch in CHARSET:
        assert hand._pick_font(ch, 0) is not None, "no font draws %r" % ch


def test_bundled_fonts_need_no_system_fonts():
    for name in fonts.FONTS:
        assert fonts.has(name, "a") or fonts.has(name, "α"), name


def test_repeated_letters_are_never_identical():
    svg = svg_of("aaaaaaaa")
    paths = re.findall(r'<path d="([^"]+)"', svg)
    assert len(paths) == 8
    assert len(set(paths)) == 8


def test_allographs_never_repeat_back_to_back():
    hand = Hand(get_style("cursive"), 3)
    picks = []
    for _ in range(200):
        hand._variant("Cedarville", "e")
        picks.append(hand._last[("Cedarville", "e")])
    assert set(picks) == set(range(get_style("cursive")["allographs"]))
    assert all(a != b for a, b in zip(picks, picks[1:]))


def test_variation_stays_legible():
    hand = Hand(get_style("cursive", jitter=3.0, looseness=3.0), 9)
    svg = hand.write(0, 0, "legibility " * 40, "#000000", 24)[0]
    for rot, skew, sx in re.findall(r"rotate\(([-\d.]+)\) skewX\(([-\d.]+)\) scale\(([-\d.]+)", svg):
        assert abs(float(rot)) <= 9.0 + 1e-6
        assert abs(float(skew)) <= 14.0 + 1e-6


def test_slant_is_correlated_between_neighbours():
    hand = Hand(get_style("cursive"), 2)
    svg = hand.write(0, 0, "x" * 400, "#000000", 24)[0]
    skews = [float(s) for s in re.findall(r"skewX\(([-\d.]+)\)", svg)]
    mean = sum(skews) / len(skews)
    d = [s - mean for s in skews]
    lag1 = sum(a * b for a, b in zip(d, d[1:])) / sum(a * a for a in d)
    assert lag1 > 0.2   # a real hand drifts; independent jitter would give ~0


@pytest.mark.parametrize("src,expected", [
    (r"\frac{a}{b}", r"\f{a}{b}"),
    (r"x_{n-1} = -a + b", "x_{n-1} = -a + b"),
    (r"\int_0^1 x\,dx", "\\i{0}{1}x\u2009dx"),
    (r"\left( \frac{1}{2} \right)", r"\p{\f{1}{2}}"),
    (r"\vec{u}\cdot\nabla f", "u\u20d7·∇f"),
    (r"\sqrt{x^2 + 1}", r"\r{x^{2} + 1}"),
    (r"\alpha \leq \beta", "α ≤ β"),
])
def test_latex_translation(src, expected):
    assert latex_to_markup(src) == expected


def test_markdown_document_lays_out_and_breaks_pages():
    src = "# Title\n" + "\n".join("Line %d with $x_%d^2$" % (i, i) for i in range(80)) + "\n---\nlast page"
    nb = render_markdown(src, seed=2)
    assert len(nb.pages) >= 3
    assert nb.pages[-1]["inked"]


def test_wide_equation_shrinks_to_fit(caplog):
    nb = Notebook()
    nb.math(r"a + b + c + d + e + f + g + h + i + j + k + l + m + n + o")
    xs = [float(x) for x in re.findall(r"translate\(([-\d.]+) ", "".join(s for _, s in nb.page["inked"]))]
    assert max(xs) < nb.W
    assert not caplog.records


def test_literal_text_is_not_parsed_as_math():
    nb = render_markdown(r"snake_case and x^2 stay literal, $x^2$ does not", seed=1)
    assert nb.pages[0]["inked"]


@pytest.mark.parametrize("paper", sorted(PAPERS))
def test_every_paper_renders(paper):
    nb = Notebook(paper=paper, seed=1)
    nb.title("Paper")
    nb.text("Short line.")
    img = nb.render(dpi=60)[0]
    p = PAPERS[paper]
    assert abs(img.width / img.height - p.width_pt / p.height_pt) < 0.01


def test_inks():
    assert ink("blue").startswith("#")
    assert ink("#123456") == "#123456"
    with pytest.raises(ValueError):
        ink("chartreuse")


def test_cli_writes_pdf_and_prints_one_clean_line(tmp_path, capsys):
    src = tmp_path / "notes.md"
    src.write_text("# Hello\nworld $E = mc^2$\n", encoding="utf-8")
    assert main(["render", str(src), "--dpi", "80", "-j", "1"]) == 0
    out = capsys.readouterr()
    assert (tmp_path / "notes.pdf").exists()
    assert out.err == ""
    assert len(out.out.strip().splitlines()) == 1


def test_cli_reports_errors_without_traceback(tmp_path, capsys):
    assert main(["render", str(tmp_path / "missing.md")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("penpad:") and "Traceback" not in err


def test_pdf_page_size_matches_paper(tmp_path):
    pdfium = pytest.importorskip("pypdfium2")
    nb = Notebook(paper="a4-lines")
    nb.text("A4 page")
    path = str(tmp_path / "a4.pdf")
    nb.save(path, dpi=72)
    w, h = pdfium.PdfDocument(path)[0].get_size()
    assert abs(w - 595.28) < 2 and abs(h - 841.89) < 2


@pytest.mark.parametrize("style", sorted(STYLES))
def test_every_character_leaves_ink(style):
    """Dots, colons and i-dots are TrueType contours without a moveTo; they must still render."""
    from penpad import raster
    hand = Hand(get_style(style), 1)
    for ch in CHARSET + ":;.!?":
        svg, _ = hand.write(20, 60, ch, "#000000", 40)
        alpha = raster.rasterize(100, 100, svg, 100).split()[3]
        assert alpha.getextrema()[1] > 0, "%r renders blank in %s" % (ch, style)


def test_parallel_render_matches_serial():
    nb = render_markdown("one\n---\ntwo\n---\nthree", seed=5)
    serial = [im.tobytes() for im in nb.render(dpi=50, jobs=1)]
    parallel = [im.tobytes() for im in nb.render(dpi=50, jobs=3)]
    assert len(serial) == 3 and serial == parallel
