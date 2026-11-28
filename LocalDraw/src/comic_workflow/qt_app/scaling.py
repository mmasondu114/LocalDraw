"""Screen-aware UI scale for LocalDraw (1080p / 1440p / 3440x1440 ultrawide).

Qt only honours ``QT_SCALE_FACTOR`` when it is set *before* ``QApplication`` exists,
so the primary monitor's work area is read through Win32 first. The factor scales
every fixed pixel size, font and icon uniformly (crisp, no bitmap stretching).
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QDialog, QMessageBox

SCALE_CHOICES: list[tuple[str, str]] = [
    ("auto", "自动（按屏幕）"),
    ("0.9", "90%"),
    ("1.0", "100%"),
    ("1.1", "110%"),
    ("1.25", "125%"),
    ("1.5", "150%"),
]

# Factor actually applied in this process (QT_SCALE_FACTOR), for the settings dialog.
APPLIED: dict[str, object] = {"factor": 1.0, "setting": "auto", "area": None}


def primary_work_area() -> tuple[int, int] | None:
    """Logical (DPI-independent) work area of the primary monitor, e.g. 2752x1104 for 3440x1440 @125%."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    set_ctx = getattr(user32, "SetThreadDpiAwarenessContext", None)
    old = None
    try:
        if set_ctx is not None:
            set_ctx.restype = ctypes.c_void_p
            set_ctx.argtypes = [ctypes.c_void_p]
            old = set_ctx(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2, thread-local and restored below

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        hmon = user32.MonitorFromPoint(wintypes.POINT(0, 0), 1)  # MONITOR_DEFAULTTOPRIMARY
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None
        dx, dy = ctypes.c_uint(96), ctypes.c_uint(96)
        try:
            ctypes.windll.shcore.GetDpiForMonitor(ctypes.c_void_p(hmon), 0, ctypes.byref(dx), ctypes.byref(dy))
        except Exception:
            pass
        dpr = max(1.0, dx.value / 96.0)
        r = info.rcWork
        return round((r.right - r.left) / dpr), round((r.bottom - r.top) / dpr)
    except Exception:
        return None
    finally:
        if set_ctx is not None and old:
            set_ctx(ctypes.c_void_p(old))


def auto_scale(width: int, height: int) -> float:
    """Pick a factor from the logical work area so common screens get the same physical density.

    - 1366x768 / 1920x1080 @125% laptops (work area < ~860 px tall): 0.9 so the 3-column studio fits
    - 1920x1080 @100%, 2560x1440 @125%, 3440x1440 @125%, 4K @200%: 1.0 (design baseline)
    - 2560x1440 / 3440x1440 @100%, 4K @150% (work area >= 1250 px tall): 1.15, text otherwise reads small
    """
    if height < 860 or width < 1400:
        return 0.9
    if height >= 1250 and width >= 2200:
        return 1.15
    return 1.0


def resolve_scale(setting: str, area: tuple[int, int] | None) -> float:
    if setting and setting != "auto":
        try:
            return max(0.75, min(2.0, float(setting)))
        except ValueError:
            pass
    return auto_scale(*area) if area else 1.0


def prepare_scale_env(state_path: Path) -> float:
    """Read ui_scale from app-state.json and export QT_SCALE_FACTOR. Call before QApplication()."""
    setting = "auto"
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8"))
        setting = str(raw.get("ui_scale") or "auto")
    except Exception:
        pass
    area = primary_work_area()
    APPLIED.update(setting=setting, area=area)
    if os.environ.get("QT_SCALE_FACTOR"):  # explicit user/dev override wins
        try:
            APPLIED["factor"] = float(os.environ["QT_SCALE_FACTOR"])
        except ValueError:
            pass
        return float(APPLIED["factor"])
    factor = resolve_scale(setting, area)
    APPLIED["factor"] = factor
    if abs(factor - 1.0) > 1e-3:
        os.environ["QT_SCALE_FACTOR"] = f"{factor:g}"
    return factor


class DialogFitter(QObject):
    """Clamp every dialog to 94% x 90% of its screen so fixed resize() sizes never overflow 1080p."""

    def eventFilter(self, obj, event):  # noqa: N802 (Qt API)
        if event.type() == QEvent.Type.Show and isinstance(obj, QDialog) and not isinstance(obj, QMessageBox):
            if not obj.property("_ld_fitted"):
                obj.setProperty("_ld_fitted", True)
                screen = obj.screen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    w = min(obj.width(), int(avail.width() * 0.94))
                    h = min(obj.height(), int(avail.height() * 0.90))
                    if (w, h) != (obj.width(), obj.height()):
                        obj.resize(w, h)
                        geo = obj.frameGeometry()
                        geo.moveCenter(avail.center())
                        obj.move(geo.topLeft())
        return False
