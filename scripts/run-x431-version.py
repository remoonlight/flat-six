"""Explicit offline version-audit entry for bundled Windows Python."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.diagnostics.x431_variant_selection import main

if __name__ == '__main__':
    raise SystemExit(main())
