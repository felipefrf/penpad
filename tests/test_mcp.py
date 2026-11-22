import asyncio
import base64

import pytest

pytest.importorskip("mcp.server.mcpserver")   # mcp 2.x, Python 3.10+

from mcp import Client  # noqa: E402

from penpad import mcp_server  # noqa: E402

NOTES = "# Ondas: 1ª parte\nRelação básica:\n$$! v = \\lambda f $$\n- conjuntos $x \\in \\mathbb{R}$\n"


def call(name, args):
    async def go():
        async with Client(mcp_server.server) as c:
            return await c.call_tool(name, args)
    return asyncio.run(go())


def test_render_saves_pdf_and_reports_unsupported_latex(tmp_path, monkeypatch):
    monkeypatch.setenv("PENPAD_OUTPUT_DIR", str(tmp_path))
    r = call("render_notes", {"markdown": NOTES})
    assert not r.is_error
    text = r.content[0].text
    pdf = tmp_path / "ondas-1a-parte.pdf"
    assert "Saved %s" % pdf in text
    assert pdf.read_bytes().startswith(b"%PDF-")
    assert "\\mathbb" in text
    assert [b.type for b in r.content] == ["text", "image"]
    # a second render never overwrites the first
    r = call("render_notes", {"markdown": NOTES, "preview": False})
    assert (tmp_path / "ondas-1a-parte-2.pdf").exists()
    assert [b.type for b in r.content] == ["text"]


def test_render_can_embed_the_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("PENPAD_OUTPUT_DIR", str(tmp_path))
    r = call("render_notes", {"markdown": "# x\nok", "embed_pdf": True, "preview": False,
                              "filename": "My Notes.pdf"})
    res = r.content[-1].resource
    assert res.mime_type == "application/pdf" and str(res.uri).endswith("/my-notes.pdf")
    assert base64.b64decode(res.blob).startswith(b"%PDF-")
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("args", [{"markdown": "  "}, {"markdown": "x", "paper": "napkin"},
                                  {"markdown": "x", "style": "gothic"}])
def test_bad_input_is_a_readable_tool_error(args):
    r = call("render_notes", args)
    assert r.is_error
    assert "render_notes:" in r.content[0].text


def test_list_options():
    r = call("list_options", {})
    assert "goodnotes" in r.content[0].text and "cursive" in r.content[0].text
