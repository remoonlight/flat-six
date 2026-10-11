"""Standalone static ELF/loader audit; does not load the reference libraries."""
from pathlib import Path
import sys

root = str(Path(__file__).resolve().parents[1])
if root not in sys.path:
    sys.path.insert(0, root)

from scripts.diagnostics.x431_native_binding import main

if __name__ == '__main__':
    raise SystemExit(main())
