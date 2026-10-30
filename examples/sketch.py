"""A page mixing text, math and a hand-drawn figure through the Python API."""

import sys

from penpad import Notebook

nb = Notebook(paper="goodnotes", style="cursive", seed=21)
nb.title("Free-body diagram")
nb.text("Block on an incline, no friction:")


def incline(nb, x0, y0):
    nb.line(x0 + 40, y0 + 230, x0 + 520, y0 + 230)
    nb.line(x0 + 40, y0 + 230, x0 + 520, y0 + 30)
    nb.line(x0 + 520, y0 + 30, x0 + 520, y0 + 230)
    cx, cy = x0 + 300, y0 + 108
    nb.curve([(cx - 40, cy + 2), (cx + 2, cy - 17), (cx + 18, cy + 22), (cx - 24, cy + 40)], closed=True)
    nb.arrow(cx, cy + 12, cx, cy + 110, color="red")
    nb.write(cx + 8, cy + 104, "mg", color="red")
    nb.arrow(cx, cy + 12, cx - 40, cy - 80, color="blue")
    nb.write(cx - 70, cy - 82, "N", color="blue")
    nb.write(x0 + 110, y0 + 222, "θ", size=20)


nb.figure(260, incline)
nb.math(r"\sum F_{\parallel} = mg\sin\theta = ma \Rightarrow a = g\sin\theta", box=True)
nb.save(sys.argv[1] if len(sys.argv) > 1 else "sketch.pdf")
