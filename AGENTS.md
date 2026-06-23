# AGENTS.md

Instructions for AI coding agents working on **Annie**.

## Project

Annie is a Python CLI to search Nyaa.si, sort anime torrents, pick releases with **fzf** (2 steps: section → episode), and stream via **libtorrent** + **mpv** / **vlc** / **ffplay**.

Target UX: minimal friction, small codebase, English UI.

## Stack

- Python 3.11+
- libtorrent (Python bindings)
- stdlib HTTP for Nyaa (`urllib`, no requests)
- fzf subprocess for interactive selection
- mpv / vlc / ffplay subprocess for playback
- Packaging: `pyproject.toml` + editable install

## Architecture

Keep the flat layout — do **not** split into micro-packages unless the CLI grows a lot.

```
annie.py              launcher (auto-switches to .venv)
annie/
  cli.py              argparse, interactive loop, orchestration
  media.py            models, config, title parse/rank, catalog build
  nyaa.py             Nyaa.si client
  stream.py           libtorrent + players
  ui.py               colors, banner, fzf, console
  preview.py          fzf preview helper (`python -m annie.preview`)
  __main__.py         `python -m annie`
pyproject.toml        source of truth for deps + entry point `annie`
```

## Commands

```bash
make install          # venv + pip install -e .
./annie.py            # interactive (preferred during dev)
annie search "query"  # after install
python -m annie       # same entry point
```

Config (optional): `~/.config/annie/config.toml`  
Cache: `~/.cache/annie/` (previews, stream files)

## Conventions

- **UI language**: English (prompts, errors, section labels, help)
- **Scope**: smallest correct diff; match existing style in the file you edit
- **No history feature**: no watch history, `--continue`, or search history files
- **fzf flow**: keep **two screens** (section, then episode) — do not merge into one
- **Search input**: simple terminal `input()`, not fzf
- **Seasons**: episode counts come from Nyaa singles + **batch range parsing** (`01~13`, `S01E01-E13`, etc.) — not from an external API unless explicitly requested
- **Movies**: dedupe by canonical movie id (`movie-1`, `movie-2`, `movie-3` + title aliases), keep best release per film
- **Manga**: filter scanlation / cbz / digital volumes in `media.py`
- **Commits**: only when the user asks

## Boundaries

- Stay on **Python** unless the user explicitly asks for a rewrite
- Do not add heavy deps (ORM, web framework, Jikan/MAL client) without discussion
- Do not reintroduce removed modules (`cli/`, `core/`, `titles/`, `ranking/` as separate trees)
- Do not add tests unless they cover real parsing/catalog behavior worth guarding

## Gotchas

- **fzf preview**: use `PYTHONPATH=<root> python -m annie.preview {key}` — never `python -c "preview_key('…')"` (quote breakage)
- **Batch playback**: batch items keep `MediaKind.BATCH` with `episode` set; `play_item` uses `episode_file_query()` for file selection inside the torrent
- **Large files (>2 GiB)**: wait for full download before mpv; smaller files use min buffer only
- **`media.py` is large** by design (parse + rank + catalog) — OK to extend there rather than new packages
- **`requirements.txt`** is legacy; **`pyproject.toml`** is canonical for dependencies

## Verification

After changes:

```bash
make install
.venv/bin/annie --help
.venv/bin/annie search "frieren" -l 3
.venv/bin/python3 -c "from annie.media import build_catalog; from annie.nyaa import search; print(len(build_catalog(search('frieren'), 'frieren')))"
```

Interactive fzf paths need a TTY — not testable headless without mocking.
