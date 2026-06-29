# Développement

Guide pour contribuer ou travailler depuis les sources du dépôt.

## Installation depuis Git

Prérequis : [uv](https://docs.astral.sh/uv/), Python **3.11+** (géré par uv), [fzf](https://github.com/junegunn/fzf), lecteur vidéo pour les tests manuels.

```bash
git clone https://github.com/CloudDown/annie.git
cd annie
make install   # uv sync + ~/.config/annie/config.toml (si absent)
```

<details>
<summary>Installation manuelle (sans make)</summary>

```bash
uv sync
./annie.py
```

</details>

`libtorrent` est installé dans le venv via le lockfile (`uv.lock`). Sur Arch, le paquet AUR utilise plutôt `python-libtorrent` système — voir [packaging/aur/README.md](packaging/aur/README.md).

## Commandes Make

```bash
make test           # suite unitaire offline
make test-offline   # scripts debug + fixtures (sans réseau)
make debug-rezero   # régression catalogue Re:Zero
make validate       # validation réseau (10 anime)
make validate-subs  # validation sous-titres OpenSubtitles (réseau)
make run            # lance le CLI
make clean          # supprime venv & artefacts
```

## Structure du projet

```
annie.py              Lanceur (active .venv si présent)
annie/
  cli.py              Commandes & boucle interactive
  mal.py              Franchise MAL / Jikan
  config.py           AnnieConfig (~/.config/annie/config.toml)
  types.py            Types catalogue (MediaSection, MalRelease, …)
  parsing.py          Parsing titres Nyaa
  scoring.py          Scoring des releases
  catalog.py          Construction catalogue aligné MAL
  media.py            Façade de compatibilité (ré-exporte les modules ci-dessus)
  settings.py         Options streaming (sections [streaming], [buffer], … dans config.toml)
  nyaa.py             Client Nyaa & cache
  stream.py           libtorrent + lecteurs
  subtitles.py        OpenSubtitles.com (recherche + téléchargement)
  ui.py               fzf & interface terminal
  cache.py            Cache disque JSON
  preview.py          Aperçus terminal
tests/
  helpers.py          Factories & chargement fixtures
  fixtures/           Cas JSON reproductibles (Re:Zero, filenames, …)
  test_parsing.py     Parsing, filtre saison, normalisation
  test_catalog.py     Catalogue offline (régression Re:Zero)
  test_scoring.py     Scoring / pick_best
  test_stream.py      Matching fichiers batch
  test_subtitles.py   Parsing API OpenSubtitles
  test_fixtures.py    Pilotage par fixtures parse_titles.json
scripts/
  README.md           Guide des outils de debug
  debug_parse.py      Parser un titre Nyaa
  debug_match.py      Tester match épisode ↔ fichier
  debug_catalog.py    Catalogue offline ou live
  debug_franchise.py  Diagnostic détaillé (1 anime)
  validate_franchise.py  Validation MAL vs Nyaa (100 anime)
packaging/
  aur/                PKGBUILD AUR (makepkg -si)
```

## Tests

La suite unitaire est entièrement offline (pas d’appel Nyaa/MAL/OpenSubtitles) :

```bash
make test
# ou
uv run python -m unittest discover -s tests -v
```

Les cas reproductibles vivent dans `tests/fixtures/` (`parse_titles.json`, `catalog_re_zero.json`, `subtitle_queries.json`, etc.). Ajouter un cas dans la fixture puis lancer `make test`.

## Scripts de debug

Voir [scripts/README.md](scripts/README.md) pour le détail des outils (`debug_parse.py`, `debug_match.py`, `debug_subtitles.py`, `validate_franchise.py`, …).

## CI

Chaque push et pull request sur `master` / `cursor/initial-release` déclenche `uv sync` puis les tests unitaires via [`.github/workflows/test.yml`](.github/workflows/test.yml).

## Paquet AUR

Pour builder le paquet depuis le dépôt local :

```bash
cd packaging/aur
makepkg -si
```
