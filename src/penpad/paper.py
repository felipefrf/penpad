"""Paper presets. Pages are laid out on a 900 px wide canvas and exported at real size."""

from __future__ import annotations

from dataclasses import dataclass, replace

CANVAS_WIDTH = 900
MM = 72 / 25.4


@dataclass(frozen=True)
class Paper:
    width_pt: float
    height_pt: float
    ruling: str = "grid"          # grid, lines, dots or blank
    step_pt: float = 16.0         # distance between rules
    pitch_pt: float = 0.0         # distance between baselines (0 = one rule)
    color: str = "#d1d3d4"
    rule_pt: float = 0.65
    background: str = "#ffffff"
    margin_pt: float = 17.0
    margin_line: str = ""         # color of a vertical margin rule, "" for none
    description: str = ""

    @property
    def px_per_pt(self) -> float:
        return CANVAS_WIDTH / self.width_pt

    @property
    def size_px(self):
        return CANVAS_WIDTH, round(self.height_pt * self.px_per_pt)

    @property
    def step(self) -> float:
        return self.step_pt * self.px_per_pt

    @property
    def pitch(self) -> float:
        return (self.pitch_pt or self.step_pt) * self.px_per_pt

    @property
    def margin(self) -> float:
        return self.margin_pt * self.px_per_pt

    @property
    def text_size(self) -> float:
        """Letter size that fills a line the way people write on this ruling."""
        return 0.76 * self.pitch

    @property
    def pen_scale(self) -> float:
        """Stroke widths are tuned for the Goodnotes page; keep them physical elsewhere."""
        return 455.04 / self.width_pt

    def svg(self) -> str:
        w, h = self.size_px
        out = ['<rect width="%d" height="%d" fill="%s"/>' % (w, h, self.background)]
        s, lw = self.step, self.rule_pt * self.px_per_pt
        if self.ruling == "grid":
            d = ["M%.2f 0 V%d" % (i * s, h) for i in range(int(w / s) + 1)]
            d += ["M0 %.2f H%d" % (i * s, w) for i in range(int(h / s) + 1)]
            out.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f"/>' % (" ".join(d), self.color, lw))
        elif self.ruling == "lines":
            top = 3 * s
            d = ["M0 %.2f H%d" % (top + i * s, w) for i in range(int((h - top) / s))]
            out.append('<path d="%s" fill="none" stroke="%s" stroke-width="%.2f"/>' % (" ".join(d), self.color, lw))
        elif self.ruling == "dots":
            dots = ['<circle cx="%.1f" cy="%.1f" r="%.2f"/>' % (i * s, j * s, lw * 1.3)
                    for i in range(1, int(w / s) + 1) for j in range(1, int(h / s) + 1)]
            out.append('<g fill="%s">%s</g>' % (self.color, "".join(dots)))
        if self.margin_line:
            x = self.margin * 0.8
            out.append('<path d="M%.2f 0 V%d" stroke="%s" stroke-width="%.2f"/>' % (x, h, self.margin_line, lw * 1.2))
        return "".join(out)


A4 = (595.28, 841.89)
LETTER = (612.0, 792.0)

PAPERS = {
    "goodnotes": Paper(455.04, 588.45, "grid", 16, description="Goodnotes default page, 16 pt grid"),
    "a4-grid": Paper(*A4, "grid", 5 * MM, 10 * MM, "#cfd8e3", 0.5, margin_pt=15 * MM,
                     description="A4, 5 mm squares, one line every two squares"),
    "a4-lines": Paper(*A4, "lines", 8 * MM, color="#b9cde3", rule_pt=0.6, margin_pt=20 * MM,
                      margin_line="#f1a7a7", description="A4 ruled, 8 mm lines, margin"),
    "a4-dots": Paper(*A4, "dots", 5 * MM, 10 * MM, "#b8bec6", 0.6, margin_pt=15 * MM,
                     description="A4 dot grid, 5 mm"),
    "a4-blank": Paper(*A4, "blank", 8 * MM, margin_pt=20 * MM, description="A4 plain"),
    "letter-lines": Paper(*LETTER, "lines", 9 / 32 * 72, color="#b9cde3", rule_pt=0.6, margin_pt=1.25 * 72,
                          margin_line="#f1a7a7", description="US Letter, college ruled (9/32 in)"),
    "letter-grid": Paper(*LETTER, "grid", 0.25 * 72, 0.25 * 72, "#cfd8e3", 0.5, margin_pt=0.6 * 72,
                         description="US Letter, quarter-inch graph paper"),
    "remarkable": Paper(447.4, 596.5, "lines", 8 * MM, color="#c8c8c8", rule_pt=0.6, margin_pt=12 * MM,
                        description="reMarkable 2 page, lined"),
}


def get_paper(paper=None, **overrides) -> Paper:
    if paper is None:
        p = PAPERS["goodnotes"]
    elif isinstance(paper, Paper):
        p = paper
    else:
        if paper not in PAPERS:
            raise ValueError("unknown paper %r, choose from %s" % (paper, ", ".join(PAPERS)))
        p = PAPERS[paper]
    return replace(p, **overrides) if overrides else p
