"""Explicit project entry for embedded Windows Python, independent of cwd/PATH."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.diagnostics.x431_reference import main

if __name__ == '__main__':
    raise SystemExit(main())
