# Scripts de debug Annie

Outils pour diagnostiquer parsing, catalogue et matching **sans lancer mpv**.

## Tests rapides (offline, recommandé)

```bash
make test                    # toute la suite unitaire
python scripts/debug_match.py --fixture
python scripts/debug_catalog.py --offline
python scripts/debug_parse.py --fixture parse_titles
```

## Scripts

| Script | Réseau | Description |
|--------|--------|-------------|
| `debug_parse.py` | Non | Affiche `parse_title` + batch range pour un titre |
| `debug_match.py` | Non | Teste `match_episode_filename` (batch SubsPlease, etc.) |
| `debug_catalog.py` | Optionnel | Catalogue offline via `tests/fixtures/catalog_re_zero.json` |
| `debug_franchise.py` | Oui | Rapport détaillé MAL ↔ catalogue pour **un** anime |
| `validate_franchise.py` | Oui | Couverture MAL + **seeders/qualité** par épisode (100 anime par défaut) |

## Exemples

```bash
# Pourquoi S01E08 batch ne matche pas le fichier ?
python scripts/debug_match.py \
  "[SubsPlease] Re Zero - 08 (1080p) [ABCD1234].mkv" -s 1 -e 8

# Régression Re:Zero sans Nyaa
python scripts/debug_catalog.py --offline

# Diagnostic complet (réseau)
python scripts/debug_franchise.py "re zero"
python scripts/debug_franchise.py "re zero" --json

# Validation 10 anime
python scripts/validate_franchise.py --limit 10
```

## Fixtures (`tests/fixtures/`)

- `parse_titles.json` — titres Nyaa + résultat attendu du parsing
- `match_filenames.json` — noms de fichiers torrent + épisode cible
- `catalog_re_zero.json` — entrées simulées + attentes par saison (régression Re:Zero)
- `catalog_quality_re_zero.json` — S1E8 : batch seedé vs Director's Cut (seeders + qualité)

Ajouter un cas dans la fixture puis lancer `make test` ou le script `--fixture` associé.

`validate_franchise.py` signale aussi les épisodes à faible seed (&lt;10), basse qualité (&lt;720p) ou variantes (Director's Cut).
