# Annie

CLI to search [Nyaa.si](https://nyaa.si), sort anime torrents, pick releases with **fzf**, and stream via **libtorrent** + **mpv** / **vlc** / **ffplay**.

## Requirements

- Python 3.11+
- [fzf](https://github.com/junegunn/fzf) (interactive mode)
- mpv, vlc, or ffplay
- libtorrent Python bindings (installed automatically)

## Setup

```bash
make install
# or
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Usage

```bash
./annie.py                  # interactive
annie search "frieren" -l 5
annie watch "frieren" -s 2 -e 6
frieren s2e10               # shortcut in interactive mode
```

Config (optional): `~/.config/annie/config.toml`

See also `AGENTS.md` for architecture and agent conventions.

```toml
player = "mpv"
skip_recap_movies = false
```

## Project layout

```
annie.py          launcher (auto-uses .venv)
annie/
  cli.py          commands + interactive loop
  media.py        parse, rank, catalog
  nyaa.py         Nyaa client
  stream.py       libtorrent + players
  ui.py           fzf + terminal UI
```
