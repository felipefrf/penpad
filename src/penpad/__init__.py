"""penpad: realistic handwritten pages from text, Markdown and LaTeX math.

    from penpad import Notebook
    nb = Notebook(paper="goodnotes", style="cursive", seed=7)
    nb.title("Kinematics")
    nb.text("Velocity is the time derivative of position:")
    nb.math(r"\\vec{v} = \\frac{d\\vec{r}}{dt}", box=True)
    nb.save("kinematics.pdf")
"""

__version__ = "1.0.0"

from .document import render_markdown  # noqa: E402
from .fonts import register_font  # noqa: E402
from .hand import Hand  # noqa: E402
from .latex import latex_to_markup  # noqa: E402
from .notebook import Notebook  # noqa: E402
from .paper import PAPERS, Paper  # noqa: E402
from .styles import INKS, STYLES  # noqa: E402

__all__ = ["Notebook", "Hand", "Paper", "PAPERS", "STYLES", "INKS", "render_markdown",
           "latex_to_markup", "register_font", "__version__"]
