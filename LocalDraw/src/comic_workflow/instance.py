"""Cross-process writer lock, released automatically on process exit."""

from .paths import DATA


class InstanceLock:
    def __init__(self, path=None):
        self.path = path if path is not None else DATA / "server.lock"

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        if self.file.seek(0, 2) == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            import msvcrt

            msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
        except ImportError:
            import fcntl

            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("工作室已经运行，请使用现有窗口 http://127.0.0.1:8765")
        return self

    def __exit__(self, *args):
        self.file.close()
