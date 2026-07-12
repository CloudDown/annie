# Scripts de debug Annie

Outils pour diagnostiquer parsing, catalogue et matching **sans lancer mpv**.

## Qui fait quoi

| Outil | Réseau | Quand l’utiliser |
|-------|--------|------------------|
| `make test` | Non | Régression offline avant commit / CI |
| `debug_franchise.py` | Oui | Comprendre **un** anime (saisons, épisodes manquants, offsets) |
| `validate_franchise.py` | Oui | Audit qualité panel (couverture + seeders) — hors CI |
| Autres `debug_*.py` | Variable | Parsing, matching fichier, sous-titres, fixtures |

Les tests unitaires figent des cas connus. Les scripts live servent à **découvrir** les vrais échecs Nyaa/MAL, puis à n’ajouter que des correctifs généraux (pas d’exception par titre).

## Tests rapides (offline, recommandé)

```bash
make test                    # toute la suite unitaire
python scripts/debug_match.py --fixture
python scripts/debug_subtitles.py --fixture
python scripts/debug_catalog.py --offline
python scripts/debug_parse.py --fixture parse_titles
```

## Scripts

| Script | Réseau | Description |
|--------|--------|-------------|
| `debug_parse.py` | Non | Affiche `parse_title` + batch range pour un titre |
| `debug_match.py` | Non | Teste `match_episode_filename` (batch SubsPlease, etc.) |
| `debug_subtitles.py` | Optionnel | Variantes de titre + probe OpenSubtitles par épisode |
| `debug_catalog.py` | Optionnel | Catalogue offline via `tests/fixtures/catalog_re_zero.json` |
| `debug_franchise.py` | Oui | Rapport détaillé MAL ↔ catalogue pour **un** anime |
| `validate_franchise.py` | Oui | Couverture MAL + **seeders/qualité** par épisode (100 anime par défaut) |
| `survey_nyaa_titles.py` | Oui | Collecte masse titres Nyaa + rapport patterns parsing (JSON + MD) |

## Exemples

```bash
# Pourquoi S01E08 batch ne matche pas le fichier ?
python scripts/debug_match.py \
  "[SubsPlease] Re Zero - 08 (1080p) [ABCD1234].mkv" -s 1 -e 8

# Régression Re:Zero sans Nyaa
python scripts/debug_catalog.py --offline

# Sous-titres : variantes offline puis probe API
python scripts/debug_subtitles.py --fixture
python scripts/debug_subtitles.py "re zero" -s 1 -e 8 -l fr \
  --nyaa "[SubsPlease] Re Zero kara Hajimeru Isekai Seikatsu - 08 (1080p)"
python scripts/debug_subtitles.py --fixture --live

# Diagnostic complet (réseau)
python scripts/debug_franchise.py "re zero"
python scripts/debug_franchise.py "re zero" --json

# Validation 10 anime
python scripts/validate_franchise.py --limit 10

# Survey patterns Nyaa (top MAL + rapport pour améliorer le parsing)
uv run python scripts/survey_nyaa_titles.py --top 30 --pages 2
uv run python scripts/survey_nyaa_titles.py --from-validate-queries --limit 15 --pages 1
```

## Fixtures (`tests/fixtures/`)

- `parse_titles.json` — titres Nyaa + résultat attendu du parsing
- `match_filenames.json` — noms de fichiers torrent + épisode cible
- `catalog_re_zero.json` — entrées simulées + attentes par saison (régression Re:Zero)
- `catalog_quality_re_zero.json` — S1E8 : batch seedé vs Director's Cut (seeders + qualité)
- `subtitle_queries.json` — titres Nyaa + attentes variantes / hits OpenSubtitles

Ajouter un cas dans la fixture puis lancer `make test` ou le script `--fixture` associé.

`validate_franchise.py` signale aussi les épisodes à faible seed (&lt;10), basse qualité (&lt;720p) ou variantes (Director's Cut).
