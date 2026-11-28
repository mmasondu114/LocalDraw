"""Direct native PySide6 desktop entry point for LocalDraw (zero Web, zero HTTP server)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from comic_workflow.qt_app import launch_desktop


if __name__ == "__main__":
    smoke = "--self-test" in sys.argv[1:]
    rc = launch_desktop(smoke=smoke)
    if not smoke:
        from comic_workflow.processes import force_exit_current_process

        force_exit_current_process(rc)
    raise SystemExit(rc)
