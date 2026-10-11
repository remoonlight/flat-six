"""Standalone entry usable with the Windows embedded Python from any cwd."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.diagnostics.selector_source_gap import main

if __name__=='__main__':raise SystemExit(main())
