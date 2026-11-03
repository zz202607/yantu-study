"""Run the server under pythonw (no console) with output in .cache/server.log.

pythonw sets sys.stdout/sys.stderr to None, and the server prints its LAN
address at startup, so the Startup shortcut must point here rather than at
app/server.py directly.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"


def main():
    CACHE.mkdir(exist_ok=True)
    with open(CACHE / "server.log", "a", encoding="utf-8", buffering=1) as log:
        sys.stdout = log
        sys.stderr = log
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        runpy.run_path(str(ROOT / "app" / "server.py"), run_name="__main__")


if __name__ == "__main__":
    main()
