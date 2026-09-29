"""HTML rendering of lookup results for QTextBrowser / QLabel.

Links use custom schemes handled by the widgets:
  play:uk / play:us / play:zh / play:tl   pronounce the headword
  lookup:<text>                           look up another word (auto-detect language)
  lookupas:<en|tl>:<text>                 look up, forcing English or Tagalog
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
        "tl": "#fcad70" if dark else "#b55d00",
    }


LANG_NAMES = {"en": "English", "zh": "Chinese", "tl": "Tagalog"}


def _lookup_link(word: str, label: str | None = None) -> str:
    c = colors()
    return (f'<a href="lookup:{quote(word)}" style="color:{c["accent"]};text-decoration:none">'
            f'{escape(label or word)}</a>')


def _lookup_as(lang: str, word: str, label: str | None = None) -> str:
    c = colors()
    return (f'<a href="lookupas:{lang}:{quote(word)}" style="color:{c["accent"]};text-decoration:none">'
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
    elif res.language == "tl" and res.kind == "word" and res.found:
        ipa = f" /{escape(res.ipa)}/" if res.ipa else ""
        parts.append(f'<a href="play:tl" style="color:{c["accent"]};text-decoration:none">🔊</a>'
                     f'<span style="color:{c["muted"]}">{ipa}</span>')
    return ("&nbsp;&nbsp;&nbsp;" if compact else "&nbsp;&nbsp;&nbsp;&nbsp;").join(parts)


def header_parts(res: LookupResult, accent: str = "us") -> tuple[str, str, str]:
    """(title, pronunciation line, extra notes) for the main window header.

    The pronunciation line sits next to the Save button, so sentences get a
    "Listen" link there too. `accent` is the default English accent for sentences.
    """
    c = colors()
    head = escape(res.headword or res.query)
    title = [f'<span style="font-size:20pt;font-weight:600">{head}</span>']
    if res.language == "tl":
        title.append(f' <span style="color:{c["tl"]};font-size:9pt">&nbsp;Tagalog</span>')
    if res.kind == "word" and res.zh_entries and res.zh_entries[0].trad != res.zh_entries[0].simp:
        title.append(f'<span style="color:{c["muted"]};font-size:13pt"> ({escape(res.zh_entries[0].trad)})</span>')
    for tag in res.tags:
        title.append(f' <span style="background:{c["tag_bg"]};font-size:8pt">&nbsp;{escape(tag)}&nbsp;</span>')

    phon = phonetics_html(res)
    if not phon and res.query and res.language in ("en", "zh", "tl"):
        acc = res.language if res.language in ("zh", "tl") else accent
        phon = f'<a href="play:{acc}" style="color:{c["accent"]};text-decoration:none">🔊 Listen</a>'

    extra = []
    if res.inflection:
        link = _lookup_as(res.language, res.lemma) if res.language == "tl" else _lookup_link(res.lemma)
        extra.append(f'<span style="color:{c["muted"]}">{escape(res.inflection.split(" of ")[0])} of </span>'
                     + link)
    hint = alt_hint_html(res).removeprefix("<br>")
    if hint:
        extra.append(hint)
    return "".join(title), phon, "<br>".join(extra)


def alt_hint_html(res: LookupResult) -> str:
    """'Also a Tagalog word: …' when the spelling exists in the other Latin-script language."""
    if not res.alt_lang:
        return ""
    c = colors()
    summary = res.alt_summary if len(res.alt_summary) < 70 else res.alt_summary[:70] + "…"
    name = LANG_NAMES[res.alt_lang]
    article = "an" if name[0] in "AEIOU" else "a"
    return (f'<br><span style="color:{c["muted"]};font-size:9pt">Also {article} {name} word '
            f'({escape(summary)}) — </span>'
            + _lookup_as(res.alt_lang, res.query, f"look up as {LANG_NAMES[res.alt_lang]}"))


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
        return translation_html(res, pending) + (gloss_html(res) if res.language == "tl" else "")
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
        if res.language == "tl":
            return not_found_html(res) + translation_html(res, pending)
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
    if res.language == "tl":
        return _message("Chinese isn't available for Tagalog — see the English and Tagalog tabs.")
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


def tagalog_html(res: LookupResult, pending: bool = False) -> str:
    c = colors()
    if not res.query:
        return ""
    if res.language == "zh":
        return _message("Tagalog is available for English and Tagalog input.")
    if res.language == "tl":
        if res.kind == "sentence" or not res.found:
            return (f"<p style='font-size:13pt'>{escape(res.query)}</p>"
                    + translation_html(res, pending) + gloss_html(res))
        out = []
        for e in res.tl_entries:
            ipa = f' <span style="color:{c["muted"]}">/{escape(e.ipa)}/</span>' if e.ipa else ""
            out.append(f'<p style="margin-bottom:2px"><span style="font-size:14pt;color:{c["tl"]}">'
                       f'{escape(e.canonical)}</span> <i style="color:{c["pos"]}">{escape(e.pos)}</i>{ipa}</p>'
                       '<ol style="margin-top:0">')
            for gloss, tags in e.senses:
                tag_html = (f' <span style="color:{c["muted"]};font-size:9pt">({escape(", ".join(tags))})</span>'
                            if tags else "")
                if e.lemma and gloss == e.lemma_note:
                    body = escape(gloss.rsplit(" ", 1)[0]) + " " + _lookup_as("tl", e.lemma)
                else:
                    body = escape(gloss)
                out.append(f'<li style="margin-bottom:3px">{body}{tag_html}</li>')
            out.append("</ol>")
            for tl, en in e.examples:
                out.append(f'<p style="margin:0 0 6px 24px"><i>{escape(tl)}</i><br>'
                           f'<span style="color:{c["muted"]}">{escape(en)}</span></p>')
        return "".join(out)
    # English input
    if res.kind == "word" and res.meanings_tl:
        rows = ["<table cellspacing='0' cellpadding='3'>"]
        for word, gloss in res.meanings_tl:
            rows.append(f'<tr><td style="font-size:13pt;padding-right:12px">{_lookup_as("tl", word)}</td>'
                        f'<td style="color:{c["muted"]}">{escape(gloss)}</td></tr>')
        rows.append("</table>")
        return "".join(rows)
    if res.translation_tl:
        return f'<p style="font-size:13pt;color:{c["tl"]}">{escape(res.translation_tl)}</p>'
    if res.translation_tl_error:
        return _message(res.translation_tl_error)
    if res.kind == "word" and res.found and not pending:
        return _message("No Tagalog equivalent in the offline dictionary.")
    return _message("Translating…" if pending else "No Tagalog translation.")


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
        if not (py or meaning):
            continue
        link = _lookup_as("tl", tok) if res.language == "tl" else _lookup_link(tok)
        rows.append(f"<tr><td>{link}</td>"
                    f'<td style="color:{c["zh"]};padding:0 8px">{escape(py)}</td>'
                    f"<td>{escape(meaning)}</td></tr>")
    rows.append("</table>")
    return "".join(rows)


def _say_link(text: str, lang: str) -> str:
    c = colors()
    return (f'<a href="say:{lang}:{quote(text)}" style="color:{c["accent"]};text-decoration:none"'
            f' title="Listen">🔊</a>&nbsp;')


def _example_line(text_html: str, raw: str, lang: str, guide, color: str = "") -> str:
    """One sentence with its 🔊 button and pronunciation guide underneath."""
    c = colors()
    style = f' style="color:{color}"' if color else ""
    line = f"{_say_link(raw, lang)}<span{style}>{text_html}</span>"
    g = guide(raw, lang) if guide else ""
    if g:
        line += f'<br><span style="color:{c["muted"]};font-size:9pt">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{escape(g)}</span>'
    return line


def examples_html(res: LookupResult, show_zh: bool, pending: bool = False,
                  guide=None) -> str:
    """Example sentences, each with a 🔊 button and a pronunciation guide.

    `guide(text, lang)` returns IPA (en/tl) or pinyin (zh) for a sentence.
    `show_zh` toggles the translations (Chinese for English words, English otherwise).
    """
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
    word = escape((res.query if res.language == "tl" else res.headword or res.query).lower())
    for ex in res.examples:
        if res.language == "tl":
            main = _example_line(_bold(escape(ex.tl or ""), word), ex.tl or "", "tl", guide)
            trans = _example_line(escape(ex.en), ex.en, "en", guide, c["muted"]) if show_zh else ""
        elif res.language == "zh":
            # Chinese headword: the Chinese sentence is primary, English is the translation.
            main = _example_line(_bold(escape(ex.zh or ""), word), ex.zh or "", "zh", guide) if ex.zh else ""
            trans = _example_line(escape(ex.en), ex.en, "en", guide, c["muted"]) if show_zh or not ex.zh else ""
        else:
            main = _example_line(_bold(escape(ex.en), word), ex.en, "en", guide)
            trans = ""
            if show_zh:
                trans = (_example_line(escape(ex.zh), ex.zh, "zh", guide, c["zh"]) if ex.zh else
                         f'<span style="color:{c["muted"]}">(translating…)</span>')
        body = "<br>".join(x for x in (main, trans) if x)
        out.append(f'<p style="margin:0 0 12px 0">{body}</p>')
    return "".join(out)


def _bold(html_text: str, word: str) -> str:
    lower, i, parts = html_text.lower(), 0, []
    while word and (j := lower.find(word, i)) >= 0:
        parts += [html_text[i:j], f"<b>{html_text[j:j + len(word)]}</b>"]
        i = j + len(word)
    return "".join(parts) + html_text[i:]


def popup_html(res: LookupResult, pending: bool, show_tl: bool = True) -> str:
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
        tl = res.short_tl(4) if res.language == "en" and show_tl else []
        if tl:
            out.append(f"<p style='margin:4px 0 0 0'><span style='color:{c['muted']}'>Tagalog:</span> "
                       + ", ".join(_lookup_as("tl", w) for w in tl) + "</p>")
        if res.alt_lang:
            out.append(alt_hint_html(res).removeprefix("<br>"))
        if not zh and not en:
            out.append(_message("Loading…" if pending else "No definition available."))
    elif res.kind == "word" and res.language == "en" and not res.translation:
        out.append(f'<span style="font-size:14pt;font-weight:600">{escape(res.query)}</span>')
        out.append(not_found_html(res))
        if pending:
            out.append(_message("Checking online…"))
    else:
        q = res.query if len(res.query) < 220 else res.query[:220] + "…"
        badge = (f' <span style="color:{c["tl"]};font-size:8pt">Tagalog</span>'
                 if res.language == "tl" else "")
        out.append(f'<span style="color:{c["muted"]}">{escape(q)}</span>{badge}')
        out.append(translation_html(res, pending))
        if not res.translation and res.gloss:
            out.append(gloss_html(res))
    return "".join(out)
