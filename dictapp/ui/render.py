"""HTML rendering of lookup results for QTextBrowser / QLabel.

Links use custom schemes handled by the widgets:
  play:uk / play:us / play:zh   pronounce the headword
  lookup:<text>                 look up another word
"""
from __future__ import annotations

from html import escape
from urllib.parse import quote

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from ..core.models import LookupResult


def colors() -> dict[str, str]:
    pal = QApplication.palette()
    text = pal.color(QPalette.Text)
    dark = text.lightness() > 128
    return {
        "text": text.name(),
        "muted": "#9aa0a6" if dark else "#5f6368",
        "accent": "#8ab4f8" if dark else "#1a64c8",
        "pos": "#f28b82" if dark else "#b3261e",
        "tag_bg": "#3c4043" if dark else "#e8eaed",
        "zh": "#81c995" if dark else "#137333",
    }


def _lookup_link(word: str, label: str | None = None) -> str:
    c = colors()
    return (f'<a href="lookup:{quote(word)}" style="color:{c["accent"]};text-decoration:none">'
            f'{escape(label or word)}</a>')


def phonetics_html(res: LookupResult, compact: bool = False) -> str:
    c = colors()
    parts = []
    if res.language == "en" and res.kind == "word":
        for acc, label in (("uk", "UK"), ("us", "US")):
            ipa = getattr(res, f"phonetic_{acc}")
            ipa_html = f" /{escape(ipa)}/" if ipa else ""
            parts.append(f'<a href="play:{acc}" style="color:{c["accent"]};text-decoration:none">'
                         f'🔊 {label}</a><span style="color:{c["muted"]}">{ipa_html}</span>')
    elif res.language == "zh" and res.kind == "word" and res.found:
        parts.append(f'<a href="play:zh" style="color:{c["accent"]};text-decoration:none">🔊</a> '
                     f'<span style="color:{c["zh"]}">{escape(res.pinyin)}</span>')
    return ("&nbsp;&nbsp;&nbsp;" if compact else "&nbsp;&nbsp;&nbsp;&nbsp;").join(parts)


def header_html(res: LookupResult) -> str:
    c = colors()
    head = escape(res.headword or res.query)
    out = [f'<span style="font-size:20pt;font-weight:600">{head}</span>']
    if res.kind == "word" and res.zh_entries and res.zh_entries[0].trad != res.zh_entries[0].simp:
        out.append(f'<span style="color:{c["muted"]};font-size:13pt"> ({escape(res.zh_entries[0].trad)})</span>')
    for tag in res.tags:
        out.append(f' <span style="background:{c["tag_bg"]};font-size:8pt">&nbsp;{escape(tag)}&nbsp;</span>')
    ph = phonetics_html(res)
    if ph:
        out.append(f"<br>{ph}")
    if res.inflection:
        out.append(f'<br><span style="color:{c["muted"]}">{escape(res.inflection.split(" of ")[0])} of </span>'
                   + _lookup_link(res.lemma))
    return "".join(out)


def _message(text: str) -> str:
    return f'<p style="color:{colors()["muted"]}">{escape(text)}</p>'


def not_found_html(res: LookupResult) -> str:
    out = [_message(f'No dictionary entry for “{res.query}”.')]
    if res.suggestions:
        out.append("<p>Did you mean: " + ", ".join(_lookup_link(s) for s in res.suggestions) + "?</p>")
    return "".join(out)


def english_html(res: LookupResult, pending: bool = False) -> str:
    c = colors()
    if not res.query:
        return ""
    if res.kind == "sentence":
        if res.language == "en":
            return f"<p style='font-size:13pt'>{escape(res.query)}</p>"
        return translation_html(res, pending)
    if res.language == "zh":
        if not res.found:
            return not_found_html(res)
        out = []
        for e in res.zh_entries:
            out.append(f'<p><b style="color:{c["zh"]}">{escape(e.pinyin)}</b>'
                       f'<span style="color:{c["muted"]}"> {escape(e.simp)}'
                       f'{" / " + escape(e.trad) if e.trad != e.simp else ""}</span><ol>')
            for d in e.definitions:
                if d.startswith("CL:"):
                    out.append(f'<li style="color:{c["muted"]}">Measure word: {escape(d[3:])}</li>')
                else:
                    out.append(f"<li>{escape(d)}</li>")
            out.append("</ol></p>")
        return "".join(out)
    if not res.senses_en:
        if res.found:
            return _message("No English definitions offline — see the Chinese tab."
                            + (" Loading online definitions…" if pending else ""))
        return not_found_html(res) + (_message("Checking online…") if pending else "")
    out = []
    for s in res.senses_en:
        out.append(f'<p style="margin-bottom:2px"><i style="color:{c["pos"]};font-size:12pt">'
                   f'{escape(s.pos or "definition")}</i></p><ol style="margin-top:0">')
        out += [f'<li style="margin-bottom:3px">{escape(d)}</li>' for d in s.definitions]
        out.append("</ol>")
    if res.forms:
        forms = "; ".join(f"{k}: {_lookup_link(v)}" for k, v in res.forms.items())
        out.append(f'<p style="color:{c["muted"]}">Forms — {forms}</p>')
    return "".join(out)


def chinese_html(res: LookupResult, pending: bool = False) -> str:
    c = colors()
    if not res.query:
        return ""
    if res.kind == "sentence":
        return translation_html(res, pending) + gloss_html(res)
    if res.language == "zh":
        if not res.found:
            return not_found_html(res)
        out = []
        for e in res.zh_entries:
            defs = "; ".join(d for d in e.definitions if not d.startswith("CL:"))
            out.append(f'<p><span style="font-size:14pt">{escape(e.simp)}</span> '
                       f'<b style="color:{c["zh"]}">{escape(e.pinyin)}</b><br>{escape(defs)}</p>')
        return "".join(out)
    if not res.meanings_zh:
        if res.translation:
            return translation_html(res, pending)
        return not_found_html(res) if not res.found else _message("No Chinese meaning available.")
    out = ["<table cellspacing='0' cellpadding='3'>"]
    for pos, txt in res.meanings_zh:
        out.append(f'<tr><td style="color:{c["pos"]};padding-right:8px" valign="top">{escape(pos)}</td>'
                   f'<td style="font-size:12pt">{escape(txt)}</td></tr>')
    out.append("</table>")
    return "".join(out)


def translation_html(res: LookupResult, pending: bool = False) -> str:
    c = colors()
    if res.translation:
        engine = (f'<br><span style="color:{c["muted"]};font-size:8pt">'
                  f'{escape(res.translation_engine)}</span>') if res.translation_engine else ""
        return f'<p style="font-size:13pt">{escape(res.translation)}{engine}</p>'
    if res.translation_error:
        return _message(res.translation_error)
    return _message("Translating…" if pending else "No translation.")


def gloss_html(res: LookupResult) -> str:
    if not res.gloss:
        return ""
    c = colors()
    rows = [f'<p style="color:{c["muted"]};margin-bottom:2px">Word by word (offline):</p>'
            "<table cellspacing='0' cellpadding='2'>"]
    for tok, py, meaning in res.gloss:
        if not py:
            continue
        rows.append(f"<tr><td>{_lookup_link(tok)}</td>"
                    f'<td style="color:{c["zh"]};padding:0 8px">{escape(py)}</td>'
                    f"<td>{escape(meaning)}</td></tr>")
    rows.append("</table>")
    return "".join(rows)


def examples_html(res: LookupResult, show_zh: bool, pending: bool = False) -> str:
    c = colors()
    if not res.examples:
        if res.kind != "word":
            return _message("Examples are shown for words and phrases.")
        if pending:
            return _message("Loading examples…")
        if not res.online_loaded and res.online_error:
            return _message(f"No examples available offline. ({res.online_error})")
        return _message("No example sentences found.")
    out = []
    word = (res.headword or res.query).lower()
    for ex in res.examples:
        en = escape(ex.en)
        if res.language == "en" and word:
            # Bold the headword where it occurs (simple, case-insensitive).
            lower, i, parts = en.lower(), 0, []
            w = escape(word)
            while (j := lower.find(w, i)) >= 0:
                parts += [en[i:j], f"<b>{en[j:j + len(w)]}</b>"]
                i = j + len(w)
            en = "".join(parts) + en[i:]
        zh = ""
        if show_zh or res.language == "zh":
            if ex.zh:
                zh = f'<br><span style="color:{c["zh"]}">{escape(ex.zh)}</span>'
            elif show_zh:
                zh = f'<br><span style="color:{c["muted"]}">(translating…)</span>'
        out.append(f'<li style="margin-bottom:8px">{en}{zh}</li>')
    return "<ul>" + "".join(out) + "</ul>"


def popup_html(res: LookupResult, pending: bool) -> str:
    """Compact body for the quick-lookup popup."""
    c = colors()
    out = []
    if res.kind == "word" and (res.found or res.senses_en):
        head = escape(res.headword or res.query)
        out.append(f'<span style="font-size:15pt;font-weight:600">{head}</span>')
        if res.inflection:
            out.append(f'<span style="color:{c["muted"]}"> · {escape(res.inflection)}</span>')
        ph = phonetics_html(res, compact=True)
        if ph:
            out.append(f"<br>{ph}")
        zh = res.short_zh(3) if res.language == "en" else []
        en = res.short_en(3)
        if zh:
            out.append("<p style='margin:6px 0 2px 0'>" +
                       "<br>".join(f'<span style="color:{c["zh"]}">{escape(z)}</span>' for z in zh) + "</p>")
        if en:
            out.append("<p style='margin:4px 0 0 0'>" + "<br>".join(escape(e) for e in en) + "</p>")
        if not zh and not en:
            out.append(_message("Loading…" if pending else "No definition available."))
    elif res.kind == "word" and res.language == "en" and not res.translation:
        out.append(f'<span style="font-size:14pt;font-weight:600">{escape(res.query)}</span>')
        out.append(not_found_html(res))
        if pending:
            out.append(_message("Checking online…"))
    else:
        q = res.query if len(res.query) < 220 else res.query[:220] + "…"
        out.append(f'<span style="color:{c["muted"]}">{escape(q)}</span>')
        out.append(translation_html(res, pending))
        if not res.translation and res.gloss:
            out.append(gloss_html(res))
    return "".join(out)
