"""penpad command line."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from . import __version__
from .document import render_markdown
from .paper import PAPERS
from .styles import INKS, STYLES

DEMO = r"""# 1) Material derivative
A particle at $(x(t), y(t), z(t))$ moves with the flow:
$$ \frac{dx}{dt} = u_1, \quad \frac{dy}{dt} = u_2, \quad \frac{dz}{dt} = u_3 $$
Chain rule on $f(x(t), y(t), z(t), t)$:
$$! \frac{Df}{Dt} = \frac{\partial f}{\partial t} + \vec{u}\cdot\nabla f $$
{red} Steady flow with $Df/Dt \neq 0$? Yes: the convective term remains.

## Energy check
- kinetic energy $E = \frac{1}{2} m v^2$
- over the cycle $\oint F \cdot dl = 0$ (conservative)
$$ \int_{0}^{T} P\,dt = \left( \frac{1}{2} m v^2 \right)_{T} - \left( \frac{1}{2} m v^2 \right)_{0} $$
> Tip: check the units before plugging numbers, $[P] = W = J/s$.
"""


def _render(a):
    if a.input == "-":
        src, default_out = sys.stdin.read(), "notes.pdf"
    else:
        with open(a.input, encoding="utf-8") as f:
            src = f.read()
        default_out = os.path.splitext(a.input)[0] + ".pdf"
    return _write(src, a.output or default_out, a)


def _write(src, out, a):
    t0 = time.time()
    overrides = {}
    if a.jitter is not None:
        overrides["jitter"] = a.jitter
    if a.slant is not None:
        overrides["slant"] = a.slant
    nb = render_markdown(src, paper=a.paper, style=a.style, seed=a.seed, footer=a.footer, **overrides)
    paths = nb.save(out, dpi=a.dpi, jobs=a.jobs)
    n = len(nb.pages)
    if not a.quiet:
        print("%s  %d page%s  %.1fs" % (", ".join(paths), n, "" if n == 1 else "s", time.time() - t0))
    return 0


def _demo(a):
    return _write(DEMO, a.output or "penpad-demo.pdf", a)


def _list(a):
    print("styles:  " + ", ".join(STYLES))
    print("inks:    " + ", ".join("%s %s" % kv for kv in INKS.items()))
    print("papers:")
    for k, p in PAPERS.items():
        print("  %-13s %s" % (k, p.description))
    return 0


def _calibrate(a):
    from .calibrate import calibrate
    calibrate(a.pdf, a.page, a.band, a.phrase, a.out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="penpad",
        description="Turn Markdown + LaTeX notes into realistic handwritten pages (PDF/PNG) for "
                    "iPad, Goodnotes, Notability, reMarkable or print.")
    ap.add_argument("--version", action="version", version="penpad " + __version__)
    sub = ap.add_subparsers(dest="cmd")

    def common(p):
        p.add_argument("-o", "--output", help="output .pdf or .png (default: input name .pdf)")
        p.add_argument("--paper", default="goodnotes", choices=sorted(PAPERS))
        p.add_argument("--style", default="cursive", choices=sorted(STYLES))
        p.add_argument("--seed", type=int, default=1, help="same seed, same handwriting")
        p.add_argument("--jitter", type=float, help="letter wobble (style default ~0.7)")
        p.add_argument("--slant", type=float, help="mean slant in degrees, positive leans right")
        p.add_argument("--footer", help="text written at the bottom of every page, before the page number")
        p.add_argument("--dpi", type=int, default=225)
        p.add_argument("-j", "--jobs", type=int, default=0, help="pages rendered in parallel (0 = all cores)")
        p.add_argument("-q", "--quiet", action="store_true")
        p.add_argument("-v", "--verbose", action="store_true")

    p = sub.add_parser("render", help="render a Markdown file (or - for stdin)")
    p.add_argument("input")
    common(p)
    p.set_defaults(func=_render)

    p = sub.add_parser("demo", help="write a sample page")
    common(p)
    p.set_defaults(func=_demo)

    p = sub.add_parser("list", help="show styles, inks and papers")
    p.set_defaults(func=_list, verbose=False)

    p = sub.add_parser("calibrate", help="measure a page you wrote and compare it with the fonts")
    p.add_argument("pdf")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--band", type=float, nargs=2, metavar=("Y0", "Y1"), help="vertical band to compare, 0-1")
    p.add_argument("--phrase", default="The quick brown fox jumps over the lazy dog, p = p(ρ), Re = 200 000")
    p.add_argument("--out", default="calibration")
    p.set_defaults(func=_calibrate, verbose=False)

    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0
    logging.basicConfig(format="penpad: %(message)s", level=logging.DEBUG if a.verbose else logging.WARNING)
    try:
        return a.func(a)
    except KeyboardInterrupt:
        return 130
    except Exception as e:   # one clean line; -v shows the traceback
        if a.verbose:
            raise
        print("penpad: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
