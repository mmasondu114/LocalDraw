"""Native desktop launcher script (direct PySide6 app, zero browser/HTTP)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from comic_workflow.qt_app import launch_desktop


if __name__ == "__main__":
    smoke = "--self-test" in sys.argv[1:]
    raise SystemExit(launch_desktop(smoke=smoke))
