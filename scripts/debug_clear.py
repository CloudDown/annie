#!/usr/bin/env python3
"""Vérifie clear_terminal / begin_playback_ui — l'ASCII au-dessus doit disparaître."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import print  # noqa: E402

from annie.ui import (  # noqa: E402
    BANNER_ART,
    C,
    begin_playback_ui,
    end_playback_ui,
    stylize,
)


def main() -> int:
    print()
    for line in BANNER_ART[:4]:
        print(stylize(line, C.PALE_PINK))
    print("(banner ci-dessus — clear dans 1s…)")
    time.sleep(1.0)
    begin_playback_ui()
    print(stylize("◆ Après clear — seul ce bloc doit rester", C.YELLOW, C.BOLD))
    print("sous-titres  demo.srt")
    print("lecture  demo.mkv  mpv")
    print("(alt-screen 2s, puis restauration…)")
    time.sleep(2.0)
    end_playback_ui()
    print("retour écran principal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
