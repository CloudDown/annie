#!/usr/bin/env python3
"""Télécharge le top N MAL (cache incrémental) puis lance la validation."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, print  # noqa: E402

from annie.mal import fetch_top_anime


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=1000)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "scripts" / "results" / "validate_top1000.json",
    )
    args = parser.parse_args(argv)

    target = args.top
    print(f"Téléchargement top {target} MAL (reprise cache si partiel)…")
    while True:
        entries = fetch_top_anime(target)
        print(f"  {len(entries)}/{target} en cache")
        if len(entries) >= target:
            break
        print("  pause 30s puis reprise…")
        time.sleep(30)

    validate = ROOT / "scripts" / "validate_franchise.py"
    cmd = [
        sys.executable,
        str(validate),
        "--top",
        str(target),
        "--workers",
        str(args.workers),
        "--output",
        str(args.output),
        "--resume",
    ]
    print(f"Lancement validation → {args.output}")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
