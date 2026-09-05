# Développement

Guide pour contribuer ou travailler depuis les sources du dépôt.

## Installation depuis Git

Prérequis : [uv](https://docs.astral.sh/uv/), Python **3.11+** (géré par uv), un TTY, lecteur vidéo pour les tests manuels.

```bash
git clone https://github.com/CloudDown/annie.git
cd annie
make install   # uv sync + ~/.config/annie/config.toml (si absent)
```

<details>
<summary>Installation manuelle (sans make)</summary>

```bash
uv sync
./bin/annie.py
```

</details>

`libtorrent` est installé dans le venv via le lockfile (`uv.lock`). Sur Arch, le paquet AUR utilise plutôt `python-libtorrent` système — voir [../packaging/aur/README.md](../packaging/aur/README.md).

## Commandes Make

```bash
make test           # suite unitaire offline
make test-offline   # régressions fixtures (sans réseau)
make debug-rezero   # régression catalogue Re:Zero
make smoke          # Tanya / Re:Zero / film Konosuba (offline)
make run            # lance le CLI
make clean          # supprime venv & artefacts
```

## Structure du projet

```
bin/
  annie.py            Lanceur (active .venv si présent)
  annie.cmd           Lanceur Windows (sources)
docs/
  CONTRIBUTING.md     Ce guide
annie/
  cli.py              Commandes & boucle interactive
  metadata.py         Façade AniList / MAL
  anilist.py          Client AniList GraphQL
  mal.py              Franchise MAL / Jikan
  config.py           AnnieConfig (~/.config/annie/config.toml)
  settings.py         Streaming, buffer, torrent, profils lecteur
  types.py            Types catalogue (MediaSection, MalRelease, …)
  parsing.py          Parsing titres Nyaa
  scoring.py          Scoring des releases
  catalog.py          Construction catalogue
  gather.py           Orchestration métadonnées → Nyaa
  season_coherence.py Cohérence intra-saison
  nyaa.py             Client Nyaa & cache
  stream.py           libtorrent + lecteurs
  buffer.py           Buffer / probes MKV-MP4 / lancement mpv
  player.py           Commandes mpv/vlc/ffplay
  subtitles.py        OpenSubtitles.com
  ui.py               TUI & interface terminal
  tui.py              Picker plein écran (filtre, preview)
  net.py              HTTP + rate limit
  cache.py            Cache disque JSON
  paths.py            Chemins multi-OS
  preview.py          Aperçus (legacy)
  watch_history.py    Historique de visionnage
tests/
  helpers.py          Factories & chargement fixtures
  fixtures/           Cas JSON reproductibles
  test_*.py           Suite unitaire offline
scripts/              Outils debug / validation (réseau)
packaging/
  aur/                PKGBUILD AUR
  debian/             build .deb
  windows/            install-windows.bat, install.ps1
install.bat           Raccourci Windows → packaging/windows/
```

## Tests vs scripts de validation

| Outil | Réseau | Rôle |
|-------|--------|------|
| `make test` | Non | Régression offline (fixtures) — **obligatoire avant commit**, CI |
| `make debug-rezero` | Non | Régression catalogue Re:Zero via `scripts/debug_catalog.py --offline` |
| `make smoke` | Non | Tanya / Re:Zero / film Konosuba |
| `scripts/debug_franchise.py` | Oui | Zoom **un** anime : saisons MAL vs catalogue, épisodes manquants |
| `scripts/validate_franchise.py` | Oui | Audit panel : couverture, seeders, qualité — **hors CI** |

La suite unitaire est entièrement offline (pas d’appel Nyaa/MAL/OpenSubtitles) :

```bash
make test
# ou
uv run python -m unittest discover -s tests -v
```

Les cas reproductibles vivent dans `tests/fixtures/` (`parse_titles.json`, `catalog_re_zero.json`, `subtitle_queries.json`, etc.). Ajouter un cas dans la fixture puis lancer `make test`.

Pour découvrir des bugs catalogue sur de vrais titres Nyaa/MAL (pas inventer des règles au cas par cas) :

```bash
uv run python scripts/debug_franchise.py "re zero"
uv run python scripts/validate_franchise.py --limit 20
```

Corriger uniquement les **patterns répétés** du rapport, puis figer le cas en fixture offline si possible.

## CI

Chaque push et pull request sur `master` / `cursor/initial-release` déclenche `uv sync` puis les tests unitaires via [`.github/workflows/test.yml`](../.github/workflows/test.yml).

## Paquet AUR

Pour builder le paquet depuis le dépôt local :

```bash
cd packaging/aur
makepkg -si
```
