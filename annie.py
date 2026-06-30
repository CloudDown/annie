#!/usr/bin/env python3
"""Annie launcher — uses .venv from `uv sync` when present, then runs the CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from annie.paths import venv_python  # noqa: E402


def _clean_venv_env(env: dict[str, str]) -> dict[str, str]:
    cleaned = dict(env)
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONEXECUTABLE"):
        cleaned.pop(key, None)
    return cleaned


def _reexec_with_venv() -> None:
    venv_py = venv_python(ROOT)
    if venv_py is None:
        return
    if Path(sys.executable).resolve() == venv_py.resolve():
        return
    argv = [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]]
    env = _clean_venv_env(os.environ)
    if sys.platform == "win32":
        raise SystemExit(subprocess.call(argv, env=env))
    os.execv(str(venv_py), argv)


_reexec_with_venv()

from annie.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
