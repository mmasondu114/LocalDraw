"""Desktop entry point, including program updates before connecting to the backend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from desktop_update import opened

if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--no-browser"]):
        raise SystemExit("Unsupported startup options")
    try:
        opened(open_browser="--no-browser" not in sys.argv)
    except Exception as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1)
