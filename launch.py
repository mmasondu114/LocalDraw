"""Double-click launchers use this file so the checkout needs no global install."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from comic_workflow.server import main

main()
