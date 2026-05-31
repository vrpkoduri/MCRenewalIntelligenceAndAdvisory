"""Make `src/` importable as top-level packages (common, transform, ...) in tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
