import sys
from pathlib import Path

# Lambda modules are imported as top-level modules, as in the Lambda runtime.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "functions" / "sync_extraction"))
