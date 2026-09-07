"""Bootstrap commun pour les scripts Annie (hors package installé)."""

from __future__ import annotations

import sys
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# flush=True par défaut pour les scripts interactifs
print = partial(print, flush=True)  # type: ignore[misc, assignment]

FIXTURES = ROOT / "tests" / "fixtures"
