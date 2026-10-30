"""Translate a LaTeX math subset into penpad's compact markup.

penpad markup:  x_1  x^{2}  \\f{num}{den}  \\i{low}{high}  \\r{radicand}  \\p{...}  \\q{...}
plus combining accents (u⃗ x̂ ẋ ẍ x̄). Anything penpad cannot draw is passed through as text.
"""

from __future__ import annotations

SYMBOLS = {
    # greek
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "rho": "ρ", "varrho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # operators and relations
    "partial": "∂", "nabla": "∇", "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "infty": "∞", "to": "→", "rightarrow": "→", "longrightarrow": "→", "Rightarrow": "⇒",
    "implies": "⇒", "leftarrow": "←", "iff": "⇔", "neq": "≠", "ne": "≠", "leq": "≤", "le": "≤",
    "geq": "≥", "ge": "≥", "approx": "≈", "sim": "~", "simeq": "≃", "equiv": "≡", "propto": "∝",
    "sum": "∑", "prod": "∏", "int": "∫", "oint": "∮", "circ": "°", "degree": "°", "prime": "'",
    "in": "∈", "forall": "∀", "exists": "∃", "cup": "∪", "cap": "∩", "subset": "⊂",
    "ldots": "...", "dots": "...", "cdots": "···", "|": "‖", "Vert": "‖", "parallel": "‖", "perp": "⊥", "langle": "⟨", "rangle": "⟩",
    "{": "\\{", "}": "\\}", "_": "\\_", "%": "%", "&": "&", "#": "#", "$": "$",
    # spacing
    ",": "\u2009", ";": "\u2005", ":": "\u2005", "!": "", "quad": "\u2003", "qquad": "\u2003\u2003", " ": " ",
}
NAMED = {"sin", "cos", "tan", "cot", "sec", "csc", "log", "ln", "exp", "lim", "max", "min",
         "det", "arg", "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan", "sup", "inf"}
ACCENTS = {"vec": "⃗", "hat": "̂", "widehat": "̂", "dot": "̇", "ddot": "̈",
           "bar": "̄", "overline": "̄", "tilde": "̃"}
STYLE_WRAPPERS = {"text", "mathrm", "mathbf", "mathit", "mathsf", "boldsymbol", "operatorname",
                  "mathcal", "textbf", "textit", "mbox", "displaystyle"}
TEXT_WRAPPERS = {"text", "mbox", "textbf", "textit", "textrm"}   # spaces inside are kept
DELIMS = {"(": ")", "[": "]", "\\{": "\\}", "|": "|", ".": "."}


def _tokens(s):
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            j = i + 1
            if s[j].isalpha():
                while j < len(s) and s[j].isalpha():
                    j += 1
                yield "\\" + s[i + 1:j]
                i = j
            else:
                yield "\\" + s[j]
                i = j + 1
        else:
            yield c
            i += 1


BINARY = set("=<>+−±×≠≈≤≥→⇒⇔≡∝∈~") | {"-"}
OPENERS = set("([{,=") | BINARY


class _Parser:
    def __init__(self, s):
        self.t = list(_tokens(s))
        self.i = 0
        self.script = 0   # inside a subscript/superscript: operators stay tight

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def seq(self, stop=None):
        out = []
        while self.peek() is not None and self.peek() != stop and not (stop == "\\right" and self.peek() == "\\right"):
            if self.peek() == "}" and stop is None:   # stray brace
                self.next()
                continue
            a = self.atom()
            if a in BINARY and not self.script:
                prev = "".join(out).rstrip()
                unary = not prev or prev[-1] in OPENERS
                a = a if unary else " %s " % a
            elif a == "," and not self.script:
                a = ",\u2005"
            out.append(a)
        if stop is not None and self.peek() == stop:
            self.next()
        return "".join(out)

    def raw(self):
        """Literal text of a {group}, spaces included."""
        self.next()
        out, depth = [], 1
        while self.peek() is not None:
            tok = self.next()
            depth += {"{": 1, "}": -1}.get(tok, 0)
            if depth == 0:
                break
            out.append(SYMBOLS.get(tok[1:], tok[1:]) if tok.startswith("\\") else tok)
        return escape_text("".join(out))

    def arg(self):
        """One argument: {group}, a command, or a single character."""
        while self.peek() == " ":
            self.next()
        tok = self.peek()
        if tok == "{":
            self.next()
            return self.seq("}")
        if tok is None:
            return ""
        return self.atom()

    def atom(self):
        tok = self.next()
        if tok == " ":
            return ""
        if tok == "{":
            return self.seq("}")
        if tok in ("^", "_"):
            self.script += 1
            a = self.arg()
            self.script -= 1
            return "%s{%s}" % (tok, a)
        if tok == "\\":
            return ""
        if not tok.startswith("\\"):
            return tok
        name = tok[1:]
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            num = self.arg()
            return "\\f{%s}{%s}" % (num, self.arg())
        if name in ("int", "oint", "iint"):
            low = high = ""
            while self.peek() in ("_", "^"):
                which = self.next()
                if which == "_":
                    low = self.arg()
                else:
                    high = self.arg()
            if name == "oint" or not (low or high):
                sym = "∮" if name == "oint" else "∫"
                return sym + ("_{%s}" % low if low else "") + ("^{%s}" % high if high else "")
            return "\\i{%s}{%s}" % (low, high)
        if name == "sqrt":
            if self.peek() == "[":   # nth root: write the index as a small superscript
                self.next()
                idx = self.seq("]")
                return "^{%s}\\r{%s}" % (idx, self.arg())
            return "\\r{%s}" % self.arg()
        if name in ACCENTS:
            a = self.arg()
            return a + ACCENTS[name]   # on the last character
        if name in TEXT_WRAPPERS and self.peek() == "{":
            return self.raw()
        if name in STYLE_WRAPPERS:
            return self.arg() if self.peek() == "{" or name != "displaystyle" else ""
        if name == "left":
            opener = self.next() or "("
            inner = self.seq("\\right")
            closer = self.next() or ")"
            if opener == "(":
                return "\\p{%s}" % inner
            if opener == "[":
                return "\\q{%s}" % inner
            o = "" if opener == "." else SYMBOLS.get(opener[1:], opener) if opener.startswith("\\") else opener
            c = "" if closer == "." else SYMBOLS.get(closer[1:], closer) if closer.startswith("\\") else closer
            return o + inner + c
        if name in ("right", "big", "Big", "bigg", "Bigg", "limits", "nolimits"):
            return ""
        if name in NAMED:
            return "\u2009" + name + "\u2009"
        if name in SYMBOLS:
            return SYMBOLS[name]
        return name   # unknown command: keep its name readable


def latex_to_markup(s: str) -> str:
    """'\\frac{\\partial u}{\\partial t} = \\nu \\nabla^2 u' -> '\\f{∂u}{∂t} = ν∇^{2}u'."""
    return " ".join(w for w in _Parser(s.strip()).seq().split(" ") if w)


def escape_text(s: str) -> str:
    """Literal text for the markup parser (no subscripts or commands)."""
    return "".join("\\" + c if c in "\\_^{}" else c for c in s)
