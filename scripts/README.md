# Annie debug scripts

Tools to diagnose parsing, catalog, and matching **without launching mpv**.

| Tool | Network | When to use |
|------|---------|-------------|
| `make test` | No | Offline regression before commit / CI |
| `debug_franchise.py` | Yes | One anime: MAL seasons vs catalog, missing episodes |
| `validate_franchise.py` | Yes | Panel audit (coverage + seeders) — not CI |
| `debug_catalog.py` | Optional | Offline Re:Zero fixture or live `gather_catalog` |
| `debug_parse.py` / `debug_match.py` | No | Title parse / torrent filename match |
| `debug_subtitles.py` | Optional | OpenSubtitles title variants |
| `capture_readme_shots.sh` | No | Real foot+grim screenshots (your theme) → `docs/screenshots/` |
| `show_readme_shot.py` | No | One-shot screen used by the capture script |
| `render_readme_shots.py` | No | Fallback PNG renderer (no Wayland) |

```bash
make test
python scripts/debug_catalog.py --offline
python scripts/debug_franchise.py "re zero"
python scripts/validate_franchise.py --limit 10
```

Fixtures live in `tests/fixtures/`. Add a case there, then run `make test`.
