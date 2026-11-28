"""LocalDraw native PySide6 desktop application package."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from ..paths import DATA, ROOT


def launch_desktop(*, smoke: bool = False) -> int:
    from .win_shell import apply_window_identity, set_process_app_id

    # Must run BEFORE QApplication() so Windows taskbar never groups pythonw.exe under IDLE
    set_process_app_id()

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from ..app_core import StudioApp
    from .main_window import StudioMainWindow
    from .motion import install_motion
    from .scaling import DialogFitter, prepare_scale_env
    from .theme import apply_theme

    # Screen-aware UI scale (auto: 0.9 for 1080p@125% laptops, 1.0 for 1080p/1440p@125%/3440x1440@125%,
    # 1.15 for 1440p@100%); must be exported before QApplication exists.
    prepare_scale_env(DATA / "app-state.json")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(True)
    qt_app.setApplicationName("LocalDraw")
    qt_app.setApplicationDisplayName("LocalDraw")
    qt_app.setOrganizationName("LocalDraw")
    dialog_fitter = DialogFitter(qt_app)
    qt_app.installEventFilter(dialog_fitter)
    install_motion(qt_app)

    ico = ROOT / "scripts/windows/LocalDraw-app.ico"
    if not ico.is_file():
        ico = ROOT / "scripts/windows/LocalDraw.ico"
    app_icon = QIcon(str(ico)) if ico.is_file() else QIcon()
    if not app_icon.isNull():
        qt_app.setWindowIcon(app_icon)

    studio = StudioApp(keep_alive_comfy=True)
    theme_name = str(studio.ui_state.get("theme", "linen"))
    large_text = studio.ui_state.get("text_size", "standard") == "large"
    apply_theme(qt_app, theme_name, large_text)

    win = StudioMainWindow(studio, smoke=smoke)
    if not app_icon.isNull():
        win.setWindowIcon(app_icon)
    apply_window_identity(int(win.winId()), ROOT / "LocalDraw.exe", ico, display_name="LocalDraw")
    if smoke:
        win.resize(1280, 820)
        win.show()
        qt_app.processEvents()
        logs = DATA / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / "desktop-smoke.json").write_text(
            json.dumps(
                {
                    "title": win.windowTitle(),
                    "native_pyside6": True,
                    "zero_web": True,
                    "comfy_keep_alive": studio.comfy.keep_alive,
                    "theme": theme_name,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        win.close()
        studio.shutdown()
        return 0

    win.showMaximized()
    studio.warmup_caches_async()
    rc = qt_app.exec()
    studio.shutdown(keep_comfy=studio.comfy.keep_alive)
    from PySide6.QtCore import QThreadPool

    QThreadPool.globalInstance().clear()
    if qt_app.property("restart_requested"):
        # Display settings asked for a restart so a new QT_SCALE_FACTOR takes effect.
        from PySide6.QtCore import QProcess

        exe = ROOT / "LocalDraw.exe"
        if exe.is_file():
            QProcess.startDetached(str(exe), [])
        else:
            QProcess.startDetached(sys.executable, sys.argv)
    # Exit immediately before ~QApplication / Qt DLL teardown can hang or crash on background threads.
    try:
        if sys.stdout:
            sys.stdout.flush()
        if sys.stderr:
            sys.stderr.flush()
    except Exception:
        pass
    from ..processes import force_exit_current_process

    force_exit_current_process(rc)
