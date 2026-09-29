# EnZhDict — English ⇄ Chinese ⇄ Tagalog desktop dictionary

A Windows dictionary and study app for English and Chinese learners.

- **Main window**: search in English, Chinese or Tagalog. The language is detected automatically, and an **Auto / EN / 中文 / TL** selector overrides it. Results appear in five tabs: **English** (definitions by part of speech, UK/US IPA, 🔊 buttons), **中文 Chinese**, **Tagalog**, **Examples** (with an optional translation toggle), and **Saved** (the table and flashcards).
- **Tagalog ⇄ English**: Tagalog words show English meanings, IPA, audio, verb aspects linked to their root (*kinain* → *kainin*), and bilingual examples. English words list their Tagalog equivalents in the Tagalog tab. Tagalog sentences get a translation plus an offline word-by-word gloss. Chinese isn't paired with Tagalog.
- **Quick lookup anywhere**: select text in any app, then press **Ctrl twice**. A small popup appears next to the cursor with IPA, audio and short English and Chinese definitions, or a translation for sentences.
- **Saved list and flashcards**: save words and sentences, add notes, and review them with FSRS spaced repetition. You can import and export the list as CSV.
- **Offline first**: ECDICT (770k English entries), CC-CEDICT (125k Chinese entries) and Wiktionary Tagalog (40k entries) are stored in a local SQLite file. When you're online, the app adds UK/US audio, extra definitions, bilingual example sentences and sentence translation, and caches them for later.

## Setup (development)

Requires **64-bit** Python 3.11+ (PySide6 has no 32-bit wheels).

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements-dev.txt
```

### Download and build the dictionary data

```powershell
python scripts\build_dict.py
```

This downloads the three sources into `resources\raw\` and builds `resources\dict.db` (~110 MB, about 20 s):

| Source | File | License |
|---|---|---|
| [ECDICT](https://github.com/skywind3000/ECDICT) | `ecdict.csv` (~66 MB) | MIT |
| [CC-CEDICT](https://www.mdbg.net/chinese/dictionary?page=cedict) | `cedict_1_0_ts_utf-8_mdbg.txt.gz` (~4 MB) | CC BY-SA 4.0 |
| [Wiktionary Tagalog via Kaikki](https://kaikki.org/dictionary/Tagalog/) | `kaikki-tagalog.jsonl` (~16 MB) | CC BY-SA 4.0 |

If you already have a `dict.db` without Tagalog, add it without rebuilding everything:
`python scripts\build_dict.py --add-tagalog`.

If your network blocks the downloads, fetch the files manually and run
`python scripts\build_dict.py --ecdict path\to\ecdict.csv --cedict path\to\cedict.txt.gz --tagalog path\to\kaikki-tagalog.jsonl`.

### Run

```powershell
python main.py          # main window + tray icon
python main.py --tray   # start hidden in the tray
python -m pytest        # unit tests
```

Only one instance runs at a time. Launching the app again brings the existing window to the front. Closing the main window hides it; use the tray menu's **Quit** to exit.

## Build the .exe (PyInstaller, one-folder)

```powershell
python scripts\make_icon.py        # optional: regenerates resources\icon.ico
pyinstaller --noconfirm EnZhDict.spec
```

The result is `dist\EnZhDict\EnZhDict.exe`, a folder of about 240 MB that includes `dict.db`. Zip the whole folder to distribute it. To smoke-test a build, run `EnZhDict.exe --selftest` and check `%APPDATA%\EnZhDict\enzhdict.log`.

## Where data lives

`%APPDATA%\EnZhDict\`:
- `user.db`: saved entries, FSRS review state, and the online cache
- `settings.json`: settings
- `audio\`: cached pronunciations
- `enzhdict.log`: log file

## Online services and fallbacks

| Feature | Primary | Fallback |
|---|---|---|
| English definitions, UK/US IPA, audio | Free Dictionary API (dictionaryapi.dev) | ECDICT offline data |
| Example sentences (with Chinese, or English for Tagalog) | Tatoeba API | Wiktionary examples for Tagalog; otherwise the message "No examples available offline" |
| Pronunciation | Recorded audio from the Free Dictionary API | edge-tts (en-GB / en-US / zh-CN / fil-PH neural voices), then the offline Windows system voice |
| Sentence translation (EN→中文, 中文→EN, TL→EN, and EN→TL in the Tagalog tab) | Google (deep-translator) | MyMemory (deep-translator), then Argos (offline, optional), then an offline word-by-word gloss for Chinese and Tagalog |

A service that fails (for example, with a rate limit) is skipped for 10 minutes so lookups stay fast. Every result is cached, so a repeat lookup works offline.

### Optional: fully offline sentence translation (Argos)

```powershell
pip install argostranslate
argospm update
argospm install translate-en_zh
argospm install translate-zh_en
```

Argos pulls in large ML dependencies, so it's left out of the default install and the .exe build.

## Project structure

```
main.py                    entry point
dictapp/
  config.py                paths + Settings (settings.json)
  app.py                   tray, hotkey → popup wiring, single instance, --selftest
  core/                    no Qt, unit-tested
    langdetect.py          language detection, word vs sentence, query cleanup
    lookup.py              offline lookup (ECDICT/CC-CEDICT/Tagalog), English-vs-Tagalog detection,
                           lemma/stem fallback, zh segmentation, English→Tagalog reverse lookup
    online.py              Free Dictionary + Tatoeba, with caching
    translate.py           translation fallback chain
    audio.py               recorded audio download / edge-tts synthesis, cached
    srs.py                 FSRS scheduling over the reviews table
    pinyin.py, models.py
  data/userdb.py           SQLite: entries, reviews, cache; CSV import/export; schema migrations
  data/tagalog_import.py   Wiktionary (Kaikki) Tagalog parser + English→Tagalog index builder
  platform_support/        all OS-specific code
    hotkey.py              double-tap-Ctrl detector (pynput)
    windows.py             Ctrl+C capture with full clipboard restore, Run-key startup, WS_EX_NOACTIVATE
    generic.py             stubs for macOS/Linux (to do)
  ui/                      PySide6 widgets: main window, popup, saved tab + review, settings, rendering
scripts/build_dict.py      download sources and build resources/dict.db
tests/                     pytest suite
```

## Notes and limitations

- **English vs Tagalog detection**: both use the Latin alphabet. Each word is scored against both dictionaries, with common Tagalog function words (*ang, ng, mga, po…*) and stress accents (*baháy*) counting as strong Tagalog evidence. Words spelled the same in both languages (*said, may*) default to English, with an "Also a Tagalog word — look up as Tagalog" link. The popup's **as Tagalog / as English** button and the main window's language selector override detection.
- **Tagalog coverage**: Wiktionary has no word frequencies, so English→Tagalog results are ranked by sense order and entry richness. The top result is usually the common word (house → *bahay*, eat → *kumain*), but the list can include rare or regional words. Offline Windows voices rarely include Filipino, so offline Tagalog audio usually isn't available.

- **Selected text** is captured by sending Ctrl+C to the foreground app, then restoring every clipboard format afterwards. Apps that don't support Ctrl+C for copying (some games, or a console with no selection, where Ctrl+C interrupts) won't work. Clipboard managers may briefly record the copied text.
- The popup never takes keyboard focus. **Esc**, clicking elsewhere, or moving the mouse away closes it.
- ECDICT provides one (mostly British) IPA per word. The US IPA and recorded audio come from the online Free Dictionary API.
- The Free Dictionary API is sometimes slow (15 s+). The popup shows offline results immediately and fills in online data when it arrives.
- Due cards: "due today" means due any time before the end of your local calendar day. After a "Good" rating, new cards take a 10-minute learning step and come back in the same session.
