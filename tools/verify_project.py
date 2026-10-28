"""Offline compilation and synthetic integration checks; never submits API jobs."""

import compileall
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
if not compileall.compile_dir(root / "tools", quiet=1):
    raise SystemExit(1)
raise SystemExit(
    subprocess.call(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=root
    )
)
