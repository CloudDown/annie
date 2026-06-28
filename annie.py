#!/usr/bin/env python3
"""Annie launcher — uses .venv from `uv sync` when present, then runs the CLI."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from annie.paths import venv_python  # noqa: E402

_venv_py = venv_python(ROOT)
if _venv_py is not None and Path(sys.executable).resolve() != _venv_py.resolve():
    os.execv(str(_venv_py), [str(_venv_py), str(__file__), *sys.argv[1:]])

from annie.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
