"""Start a persistent studio from the Windows shortcuts."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from comic_workflow.startup import main

if __name__ == "__main__":
    raise SystemExit(main())
