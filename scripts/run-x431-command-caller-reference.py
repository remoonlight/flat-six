"""Standalone explicit offline FILE caller / separate STD CPU reference."""
from pathlib import Path
import sys

root = str(Path(__file__).resolve().parents[1])
if root not in sys.path:
    sys.path.insert(0, root)

from scripts.diagnostics.x431_command_caller_reference import main

if __name__ == '__main__':
    main()
