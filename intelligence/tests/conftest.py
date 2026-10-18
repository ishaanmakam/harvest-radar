"""Make the flat intelligence/ modules importable from the tests (they use `from common import ...`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
