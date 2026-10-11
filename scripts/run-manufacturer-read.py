"""Explicit standalone bootstrap for typed manufacturer reads; --check-only opens no hardware."""
from pathlib import Path
import sys

root = str(Path(__file__).resolve().parents[1])
if root not in sys.path:
    sys.path.insert(0, root)

from scripts.diagnostics.manufacturer_read import main

if __name__ == '__main__':
    raise SystemExit(main())
