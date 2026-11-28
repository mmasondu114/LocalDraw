"""Main native PySide6 window shell for LocalDraw (zero HTTP, zero WebView, zero emojis)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..repository import PROMPT_WORKSPACE_ID
from .comic_pages import DrawingPage, FinishedPage, ProblemsPage, StoryboardPage, StoryPage
from .dialogs import (
    CharacterLibraryDialog,
    DirectImportDialog,
    DisplaySettingsDialog,
    DrawingModelDialog,
    ImportedSetupDialog,
    ModelDownloadsDialog,
    NightQueueDialog,
    ProjectManagerDialog,
    PromptBoardStartDialog,
    StoryImportDialog,
    TaskFusionDialog,
    UpdateDialog,
)
from .home_page import HomePage
from .icons import icon_pixmap, studio_icon
from .motion import fade_widget_in, install_motion
from .prompt_page import PromptStudioPage
from .theme import apply_theme, current_theme, tokens
from .widgets import JobBannerWidget, ToastBanner

if TYPE_CHECKING:
    from ..app_core import StudioApp


class _BrandLogo(QWidget):
    """Minimal 24x24 studio emblem unified with LocalDraw.ico."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(24, 24)

    def paintEvent(self, event) -> None:
        t = tokens(current_theme())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QLinearGradient(QPointF(0, 0), QPointF(24, 24))
        if current_theme() == "linen":
            grad.setColorAt(0.0, QColor("#C46D45"))
            grad.setColorAt(1.0, QColor("#843C22"))
        else:
            grad.setColorAt(0.0, QColor("#31826C"))
            grad.setColorAt(1.0, QColor("#1A5041"))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, 24, 24), 6, 6)
        p.setBrush(QColor(255, 248, 238, 42))
        p.drawRoundedRect(QRectF(12.2, 6.2, 5.6, 11.6), 1.4, 1.4)
        pen = QPen(QColor(t["accent_ink"]), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(5.6, 5.6, 12.8, 12.8), 2.4, 2.4)
        p.drawLine(QPointF(11.8, 5.6), QPointF(11.8, 18.4))
        p.drawLine(QPointF(5.6, 12.5), QPointF(11.8, 12.5))
        p.end()


class _StatusDot(QWidget):
    """Crisp 7x7 painted live status dot inside the header capsule."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._state = "idle"
        self.setFixedSize(8, 8)

    def set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self.update()

    def paintEvent(self, event) -> None:
        t = tokens(current_theme())
        color = {
            "ready": t["success"],
            "busy": t["accent"],
            "idle": t["caption"],
        }.get(self._state, t["caption"])
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawEllipse(QRectF(0.5, 0.5, 7, 7))
        p.end()


class _StatusCapsule(QFrame):
    """Clickable header capsule showing active drawing model / ComfyUI state."""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatusPill")
        self.setFixedHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class StudioMainWindow(QMainWindow):
    """Top-level native LocalDraw application window."""

    def __init__(self, app: StudioApp, *, smoke: bool = False):
        super().__init__()
        self.app = app
        self._smoke = smoke
        self._current_pid: str | None = None
        self._current_stage: str = "home"
        self._last_transient_pid: str | None = None
        self._last_job_ids_done: set[str] = set()
        self._last_job_progress: dict[str, int] = {}
        self._initial_poll_done = False
        self._projects_cache: list[dict] = []
        self._header_icons: list[tuple[QPushButton, str]] = []
        self._nav_icons: list[tuple[QPushButton, str]] = []

        install_motion()
        self.setWindowTitle("本地漫画工作室 · LocalDraw")
        self.setMinimumSize(980, 650)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Fixed Slim 48px Header Bar
        root.addWidget(self._build_header())

        # Page Stack: 0 = HomePage, 1 = Workspace Shell
        self._root_stack = QStackedWidget()
        root.addWidget(self._root_stack, 1)

        # Floating Animated Notification Popup (never pushes page layout)
        self._toast = ToastBanner(self)

        self._home = HomePage(app, self)
        self._home.open_prompt_requested.connect(self.open_standalone_prompt)
        self._home.new_story_requested.connect(self.open_new_story)
        self._home.direct_import_requested.connect(lambda: self.open_direct_import(storyboard=False))
        self._home.direct_storyboard_requested.connect(lambda: self.open_direct_import(storyboard=True))
        self._home.prompt_board_requested.connect(self.open_prompt_board_start)
        self._home.task_fusion_requested.connect(self.open_task_fusion)
        self._home.queue_requested.connect(self.open_queue)
        self._home.characters_requested.connect(self.open_characters)
        self._home.manage_projects_requested.connect(self.open_project_manager)
        self._home.open_project_requested.connect(self.open_project)
        self._home.open_import_setup_requested.connect(self.open_import_setup_for_project)
        self._home.open_draft_requested.connect(self.open_story_draft)
        self._home.resume_workspace_requested.connect(self.resume_workspace)
        self._root_stack.addWidget(self._home)

        self._root_stack.addWidget(self._build_workspace_shell())
        self._refresh_icons()

        # Live in-memory status timer (no HTTP overhead)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(900)
        self._poll_timer.timeout.connect(self._poll_status)
        if not smoke:
            self._poll_timer.start()

        self._refresh_projects()
        self._poll_status()
        self.show_home()

    # ==================== BUILD SHELL ====================

    def _make_vdiv(self) -> QFrame:
        div = QFrame()
        div.setObjectName("HeaderDivider")
        div.setFixedSize(1, 16)
        return div

    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopHeader")
        bar.setFixedHeight(48)
        hl = QHBoxLayout(bar)
        hl.setContentsMargins(16, 0, 16, 0)
        hl.setSpacing(6)

        self._logo = _BrandLogo(self)
        title = QLabel("本地漫画工作室")
        title.setObjectName("BrandTitle")
        hl.addWidget(self._logo)
        hl.addSpacing(4)
        hl.addWidget(title)
        hl.addStretch(1)

        def ghost_btn(label: str, tip: str, ic_name: str, cb) -> QPushButton:
            b = QPushButton(label)
            b.setProperty("ghost", True)
            b.setProperty("compact", True)
            b.setToolTip(tip)
            b.setIconSize(QSize(14, 14))
            b.clicked.connect(cb)
            self._header_icons.append((b, ic_name))
            return b

        self._dl_btn = ghost_btn("模型", "模型下载与管理", "package", lambda: ModelDownloadsDialog(self.app, self).exec())
        self._display_btn = ghost_btn("外观", "主题与字号设置", "palette", self._open_display_dialog)
        self._update_btn = ghost_btn("更新", "检查版本更新", "refresh", lambda: UpdateDialog(self.app, self).exec())

        for w in (self._dl_btn, self._display_btn, self._update_btn):
            hl.addWidget(w)

        hl.addSpacing(4)
        hl.addWidget(self._make_vdiv())
        hl.addSpacing(4)

        # Unified Drawing Model / Preload Control Cluster
        self._status_pill = _StatusCapsule(bar)
        self._status_pill.clicked.connect(self._open_drawing_model_dialog)
        pl = QHBoxLayout(self._status_pill)
        pl.setContentsMargins(10, 0, 8, 0)
        pl.setSpacing(6)
        self._status_dot = _StatusDot(self._status_pill)
        self._model_status = QLabel("待命")
        self._model_status.setObjectName("Muted")
        self._model_status.setMaximumWidth(210)
        self._pill_chevron = QLabel()
        self._pill_chevron.setFixedSize(12, 12)
        pl.addWidget(self._status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        pl.addWidget(self._model_status, 0, Qt.AlignmentFlag.AlignVCenter)
        pl.addWidget(self._pill_chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._status_pill, 0, Qt.AlignmentFlag.AlignVCenter)

        self._preload_draw_btn = ghost_btn("预加载", "预加载首选绘图底模到显存", "play", self._quick_preload_drawing)
        self._unload_draw_btn = ghost_btn("卸载显存", "卸载绘图模型并释放显存", "stop", self._quick_unload_drawing)
        self._unload_draw_btn.setProperty("danger", True)
        self._unload_draw_btn.setVisible(False)
        hl.addWidget(self._preload_draw_btn)
        hl.addWidget(self._unload_draw_btn)

        hl.addSpacing(4)
        hl.addWidget(self._make_vdiv())
        hl.addSpacing(4)

        # Single contextual local Analysis LLM button
        self._start_llm_btn = ghost_btn(
            "分析模型",
            "启动本地分析模型",
            "play",
            lambda: self._run_header_action("start_model", "正在启动本地分析模型"),
        )
        self._stop_llm_btn = ghost_btn(
            "卸载分析",
            "卸载本地分析模型并释放显存",
            "stop",
            lambda: self._run_header_action("stop_model", "已卸载分析模型"),
        )
        self._stop_llm_btn.setProperty("danger", True)
        self._stop_llm_btn.setVisible(False)
        hl.addWidget(self._start_llm_btn)
        hl.addWidget(self._stop_llm_btn)
        return bar

    def _build_workspace_shell(self) -> QWidget:
        shell = QWidget()
        hl = QHBoxLayout(shell)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)

        # Left Sidebar (204px)
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(204)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 12, 10, 12)
        sl.setSpacing(3)

        cap = QLabel("工作空间")
        cap.setObjectName("Caption")
        cap.setContentsMargins(8, 2, 8, 4)
        sl.addWidget(cap)

        def nav_btn(text: str, ic_name: str | None, cb) -> QPushButton:
            b = QPushButton(text)
            b.setProperty("nav", True)
            if ic_name:
                b.setIconSize(QSize(15, 15))
                self._nav_icons.append((b, ic_name))
            b.clicked.connect(cb)
            return b

        self._nav_home = nav_btn("创作首页", "home", self.show_home)
        self._nav_queue = nav_btn("任务队列", "queue", self.open_queue)
        sl.addWidget(self._nav_home)
        sl.addWidget(self._nav_queue)

        sep1 = QFrame()
        sep1.setObjectName("SidebarSep")
        sep1.setFixedHeight(1)
        sl.addSpacing(6)
        sl.addWidget(sep1)
        sl.addSpacing(6)

        proj_cap = QLabel("当前作品")
        proj_cap.setObjectName("Caption")
        proj_cap.setContentsMargins(8, 0, 8, 4)
        sl.addWidget(proj_cap)

        self._proj_combo = QComboBox()
        self._proj_combo.setMinimumWidth(0)
        self._proj_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._proj_combo.activated.connect(self._on_sidebar_project_pick)
        sl.addWidget(self._proj_combo)
        sl.addSpacing(4)

        self._nav_prompt = nav_btn("提示词出图", "spark", self.open_standalone_prompt)
        self._nav_board = nav_btn("分镜出图", "storyboard", self.open_prompt_board_start)
        self._nav_import = nav_btn("导入提示词", "import", lambda: self.open_direct_import(storyboard=False))
        self._nav_story_imp = nav_btn("导入剧本 JSON", "script", self.open_story_import)
        self._nav_chars = nav_btn("角色库", "characters", self.open_characters)
        self._nav_manage = nav_btn("作品管理", "projects", self.open_project_manager)
        for b in (self._nav_prompt, self._nav_board, self._nav_import, self._nav_story_imp, self._nav_chars, self._nav_manage):
            sl.addWidget(b)

        # Workflow Stepper Section (hidden in standalone prompt mode to avoid duplicate items)
        self._step_section = QWidget()
        step_lay = QVBoxLayout(self._step_section)
        step_lay.setContentsMargins(0, 6, 0, 0)
        step_lay.setSpacing(3)

        sep2 = QFrame()
        sep2.setObjectName("SidebarSep")
        sep2.setFixedHeight(1)
        step_lay.addWidget(sep2)
        step_lay.addSpacing(6)

        step_cap = QLabel("当前流程")
        step_cap.setObjectName("Caption")
        step_cap.setContentsMargins(8, 0, 8, 4)
        step_lay.addWidget(step_cap)

        self._step_buttons: dict[str, QPushButton] = {}
        for key, lbl in [
            ("prompt", "01 提示词出图"),
            ("script", "01 剧本与角色"),
            ("page", "02 逐格分镜"),
            ("drawing", "03 绘图与排版"),
            ("problems", "04 问题格子"),
            ("finished", "05 成品查看"),
        ]:
            b = nav_btn(lbl, None, lambda _=False, s=key: self._switch_stage(s))
            self._step_buttons[key] = b
            step_lay.addWidget(b)
        sl.addWidget(self._step_section)

        sl.addStretch(1)
        self._export_btn = QPushButton("导出作品")
        self._export_btn.setProperty("ghost", True)
        self._export_btn.setProperty("compact", True)
        self._export_btn.setIconSize(QSize(14, 14))
        self._export_btn.clicked.connect(self._export_current_project)
        self._nav_icons.append((self._export_btn, "export"))
        sl.addWidget(self._export_btn)
        hl.addWidget(side)

        # Right main workspace area
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 8, 12, 10)
        rl.setSpacing(8)

        # Compact Stage Header (hidden in standalone Prompt Studio for max canvas height)
        self._stage_head = QWidget()
        head = QHBoxLayout(self._stage_head)
        head.setContentsMargins(2, 0, 2, 0)
        head.setSpacing(8)
        self._stage_title = QLabel("提示词出图")
        self._stage_title.setObjectName("StageTitle")
        self._stage_badge = QLabel("")
        self._stage_badge.setObjectName("BadgeAccent")
        head.addWidget(self._stage_title)
        head.addStretch(1)
        head.addWidget(self._stage_badge)
        rl.addWidget(self._stage_head)

        self._job_banner = JobBannerWidget(self)
        self._job_banner.setMaximumWidth(760)
        self._job_banner.control_requested.connect(self._control_job)
        rl.addWidget(self._job_banner)

        self._stage_stack = QStackedWidget()
        self._prompt_page = PromptStudioPage(self.app, self)
        self._prompt_page.notify_requested.connect(self.notify)
        self._prompt_page.job_started.connect(self._on_job_started)
        self._prompt_page.control_requested.connect(self._control_job)
        self._prompt_page.open_characters_requested.connect(self.open_characters)
        self._prompt_page.open_queue_requested.connect(self.open_queue)
        self._prompt_page.open_board_layout_requested.connect(lambda pid: self._switch_stage("finished"))

        self._story_page = StoryPage(self.app, self)
        self._story_page.notify_requested.connect(self.notify)
        self._story_page.project_created.connect(self.open_project)
        self._story_page.job_started.connect(self._on_job_started)

        self._sb_page = StoryboardPage(self.app, self)
        self._sb_page.notify_requested.connect(self.notify)
        self._sb_page.job_started.connect(self._on_job_started)
        self._sb_page.project_updated.connect(self._reload_current_project)
        self._sb_page.go_drawing_requested.connect(lambda: self._switch_stage("drawing"))

        self._drawing_page = DrawingPage(self.app, self)
        self._drawing_page.notify_requested.connect(self.notify)
        self._drawing_page.job_started.connect(self._on_job_started)
        self._drawing_page.go_problems_requested.connect(lambda: self._switch_stage("problems"))
        self._drawing_page.open_import_setup_requested.connect(self.open_import_setup_for_project)

        self._problems_page = ProblemsPage(self.app, self)
        self._problems_page.notify_requested.connect(self.notify)
        self._problems_page.job_started.connect(self._on_job_started)
        self._problems_page.go_finished_requested.connect(lambda: self._switch_stage("finished"))

        self._finished_page = FinishedPage(self.app, self)
        self._finished_page.notify_requested.connect(self.notify)

        self._stage_widgets = {
            "prompt": self._prompt_page,
            "script": self._story_page,
            "page": self._sb_page,
            "drawing": self._drawing_page,
            "problems": self._problems_page,
            "finished": self._finished_page,
        }
        for w in self._stage_widgets.values():
            self._stage_stack.addWidget(w)
        rl.addWidget(self._stage_stack, 1)

        hl.addWidget(right, 1)
        return shell

    def _refresh_icons(self) -> None:
        t = tokens(current_theme())
        for btn, ic_name in self._header_icons:
            btn.setIcon(studio_icon(ic_name, t["muted"], 14))
        for btn, ic_name in self._nav_icons:
            col = t["nav_active_ink"] if btn.property("active") else t["muted"]
            btn.setIcon(studio_icon(ic_name, col, 15))
        self._logo.update()
        self._status_dot.update()
        if hasattr(self, "_pill_chevron"):
            self._pill_chevron.setPixmap(icon_pixmap("chevron-down", t["muted"], size=11, dpr=2.0))
        if hasattr(self, "_prompt_page"):
            self._prompt_page.refresh_icons()

    def _set_btn_active(self, btn: QPushButton, active: bool) -> None:
        btn.setProperty("active", active)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    # ==================== NAVIGATION & STATE ====================

    def notify(self, message: str, error: bool = False) -> None:
        if self._smoke:
            return
        clean = str(message or "").strip()
        if not clean:
            return
        self._toast.show_message(clean, error=error)

    def _refresh_projects(self) -> None:
        self._projects_cache = self.app.repo.listing()
        self._proj_combo.blockSignals(True)
        self._proj_combo.clear()
        self._proj_combo.addItem("切换作品…", "")
        for p in self._projects_cache:
            self._proj_combo.addItem(p.get("title") or p["id"], p["id"])
        if self._current_pid:
            idx = self._proj_combo.findData(self._current_pid)
            if idx >= 0:
                self._proj_combo.setCurrentIndex(idx)
        self._proj_combo.blockSignals(False)
        self._home.refresh(self._projects_cache)

    def show_home(self) -> None:
        self._refresh_projects()
        self._home.update_activity(self.app.status())
        self._set_btn_active(self._nav_home, True)
        self._set_btn_active(self._nav_prompt, False)
        self._set_btn_active(self._nav_board, False)
        self._refresh_icons()
        was_home = self._root_stack.currentIndex() == 0
        self._root_stack.setCurrentIndex(0)
        if not was_home:
            fade_widget_in(self._home, duration=165)

    def resume_workspace(self) -> None:
        if self._current_stage == "home":
            self.open_standalone_prompt()
            return
        self._root_stack.setCurrentIndex(1)

    def open_standalone_prompt(self) -> None:
        if (
            self._root_stack.currentIndex() == 1
            and self._current_stage == "prompt"
            and self._current_pid is None
            and not self._prompt_page._board_pid
        ):
            return
        self._current_pid = None
        reuse_pid = (
            self._last_transient_pid
            if self._last_transient_pid and self._last_transient_pid in self.app.repo.transient_ids
            else (PROMPT_WORKSPACE_ID if self.app.repo.has_prompt_workspace() else None)
        )
        self._prompt_page.load_session(reuse_pid, board=False)
        self._configure_stepper("standalone")
        self._switch_stage("prompt")
        self._root_stack.setCurrentIndex(1)

    def open_new_story(self) -> None:
        self._current_pid = None
        self._story_page.load_project(None)
        self._configure_stepper("new_story")
        self._switch_stage("script")
        self._root_stack.setCurrentIndex(1)

    def open_story_draft(self, draft: dict) -> None:
        self._current_pid = None
        self._story_page.load_project(None, draft=draft)
        self._configure_stepper("new_story")
        self._switch_stage("script")
        self._root_stack.setCurrentIndex(1)

    def open_project(self, pid: str) -> None:
        if not pid:
            return
        try:
            snap = self.app.snapshot(pid)
        except Exception as exc:
            self.notify(f"打开作品失败：{exc}", True)
            return
        self._current_pid = pid
        if not pid.startswith("tmp-"):
            self.app.ui_state.set("last_project", pid)
        self._refresh_projects()
        proj = snap["project"]
        if proj.get("prompt_board"):
            self._prompt_page.load_session(pid, board=True)
            self._finished_page.load_project(pid, snap)
            self._configure_stepper("board")
            self._switch_stage("prompt")
        elif proj.get("quick_sample"):
            self._prompt_page.load_session(pid, board=False)
            self._configure_stepper("standalone")
            self._switch_stage("prompt")
        else:
            self._configure_stepper("comic")
            has_pages = bool(proj.get("pages"))
            self._switch_stage("drawing" if has_pages else "script")
        self._root_stack.setCurrentIndex(1)

    def _configure_stepper(self, mode: str) -> None:
        vis = {
            "standalone": [],
            "board": ["prompt", "finished"],
            "new_story": ["script"],
            "comic": ["script", "page", "drawing", "problems", "finished"],
        }[mode]
        self._step_section.setVisible(bool(vis))
        for k, b in self._step_buttons.items():
            b.setVisible(k in vis)

    def _switch_stage(self, stage: str) -> None:
        self._current_stage = stage
        w = self._stage_widgets.get(stage)
        if w is not None:
            self._stage_stack.setCurrentWidget(w)

        is_standalone_prompt = stage == "prompt" and not self._prompt_page._board_pid
        self._stage_head.setVisible(not is_standalone_prompt)
        self._job_banner.setVisible(stage != "prompt")

        self._set_btn_active(self._nav_home, False)
        self._set_btn_active(self._nav_prompt, is_standalone_prompt)
        self._set_btn_active(self._nav_board, bool(self._prompt_page._board_pid and stage == "prompt"))
        self._refresh_icons()

        titles = {
            "prompt": "分镜出图" if self._prompt_page._board_pid else "提示词出图",
            "script": "01 剧本与角色",
            "page": "02 逐格分镜与对白",
            "drawing": "03 绘图与排版",
            "problems": "04 问题格子",
            "finished": "整图排版" if self._prompt_page._board_pid else "05 成品查看",
        }
        self._stage_title.setText(titles.get(stage, stage))
        for k, b in self._step_buttons.items():
            self._set_btn_active(b, k == stage)

        if self._current_pid and stage in ("script", "page", "drawing", "problems", "finished"):
            snap = self.app.snapshot(self._current_pid)
            self._stage_badge.setText(f"{snap['project'].get('brief', {}).get('title', self._current_pid)}")
            if stage == "script":
                self._story_page.load_project(snap)
            elif stage == "page":
                self._sb_page.load_project(self._current_pid, snap)
            elif stage == "drawing":
                self._drawing_page.load_project(self._current_pid, snap)
            elif stage == "problems":
                self._problems_page.load_project(self._current_pid)
            elif stage == "finished":
                self._finished_page.load_project(self._current_pid, snap)
        elif self._prompt_page._board_pid:
            snap = self.app.snapshot(self._prompt_page._board_pid)
            self._stage_badge.setText(f"{snap['project'].get('brief', {}).get('title', self._prompt_page._board_pid)}")
        else:
            self._stage_badge.setText("")

    def _reload_current_project(self) -> None:
        if self._current_pid:
            self._switch_stage(self._current_stage)

    def _on_sidebar_project_pick(self, idx: int) -> None:
        pid = self._proj_combo.itemData(idx)
        if pid:
            self.open_project(pid)

    # ==================== DIALOG TRIGGERS ====================

    def _open_drawing_model_dialog(self) -> None:
        dlg = DrawingModelDialog(self.app, self)
        dlg.job_started.connect(self._on_job_started)
        dlg.exec()
        self._poll_status()

    def _open_display_dialog(self) -> None:
        dlg = DisplaySettingsDialog(self.app, self)
        dlg.theme_changed.connect(self._apply_theme_live)
        dlg.exec()

    def _apply_theme_live(self, theme_name: str, large_text: bool) -> None:
        qt_app = QApplication.instance()
        if qt_app is not None:
            apply_theme(qt_app, theme_name, large_text)
        self._home.update_theme(theme_name)
        self._toast.refresh_theme()
        self._refresh_icons()

    def open_characters(self) -> None:
        dlg = CharacterLibraryDialog(self.app, self)
        dlg.characters_updated.connect(
            lambda: self._prompt_page.load_session(
                self._prompt_page._session_pid,
                board=bool(self._prompt_page._board_pid),
            )
        )
        dlg.job_started.connect(self._on_job_started)
        dlg.exec()

    def open_queue(self) -> None:
        dlg = NightQueueDialog(self.app, self)
        dlg.open_project_requested.connect(self.open_project)
        dlg.add_prompt_task_requested.connect(self.open_standalone_prompt)
        dlg.open_fusion_requested.connect(self.open_task_fusion)
        dlg.exec()

    def open_project_manager(self) -> None:
        dlg = ProjectManagerDialog(self.app, self)
        dlg.projects_changed.connect(self._refresh_projects)
        dlg.exec()

    def open_direct_import(self, *, storyboard: bool = False) -> None:
        dlg = DirectImportDialog(self.app, storyboard_mode=storyboard, parent=self)
        if storyboard:
            dlg.imported.connect(lambda pid, _b, _p: self.open_project(pid))
        else:
            dlg.imported.connect(self._open_import_setup)
        dlg.exec()

    def open_import_setup_for_project(self, pid: str) -> None:
        if not pid:
            return
        try:
            imp = self.app.prompt_import_state(pid)
            bundle = imp.get("bundle")
            if not bundle:
                self.notify("该作品尚未导入逐格提示词文件", True)
                return
            self._open_import_setup(pid, bundle["id"], bundle.get("panels") or [])
        except Exception as exc:
            self.notify(str(exc), True)

    def _open_import_setup(self, pid: str, bundle_id: str, panels: list[dict]) -> None:
        dlg = ImportedSetupDialog(self.app, pid, bundle_id, panels, self)
        dlg.drawing_started.connect(lambda p, jid: (self.open_project(p), self._on_job_started(jid)))
        dlg.exec()

    def open_prompt_board_start(self) -> None:
        dlg = PromptBoardStartDialog(self.app, self)
        dlg.board_ready.connect(self.open_project)
        dlg.exec()

    def open_task_fusion(self) -> None:
        dlg = TaskFusionDialog(self.app, self)
        dlg.fused.connect(self._open_import_setup)
        dlg.exec()

    def open_story_import(self) -> None:
        dlg = StoryImportDialog(self.app, self)
        dlg.story_imported.connect(self.open_project)
        dlg.exec()

    def _export_current_project(self) -> None:
        if not self._current_pid:
            self.notify("当前没有可导出的作品", True)
            return
        try:
            data = self.app.action({"action": "export", "project": self._current_pid})
            dest, _ = QFileDialog.getSaveFileName(self, "导出作品 JSON", f"{self._current_pid}.json", "JSON (*.json)")
            if dest:
                Path(dest).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                self.notify("已导出：" + dest, False)
        except Exception as exc:
            self.notify(str(exc), True)

    # ==================== LIVE STATUS & JOB POLLING ====================

    def _quick_preload_drawing(self) -> None:
        cp = self._prompt_page._current_checkpoint() if self._current_stage == "prompt" else ""
        cp = cp or self.app.preferred_checkpoint()
        if not cp:
            self._open_drawing_model_dialog()
            return
        try:
            res = self.app.action({"action": "preload_drawing_model", "checkpoint": cp})
            self.notify(f"正在预加载 {Path(cp).stem}", False)
            if isinstance(res, dict) and res.get("job"):
                self._on_job_started(res["job"])
            self._poll_status()
        except Exception as exc:
            self.notify(str(exc), True)

    def _quick_unload_drawing(self) -> None:
        try:
            res = self.app.action({"action": "unload_drawing_model"})
            self.notify("正在卸载绘图模型并释放显存", False)
            if isinstance(res, dict) and res.get("job"):
                self._on_job_started(res["job"])
            self._poll_status()
        except Exception as exc:
            self.notify(str(exc), True)

    def _run_header_action(self, action: str, ok_msg: str) -> None:
        try:
            res = self.app.action({"action": action})
            self.notify(ok_msg, False)
            if isinstance(res, dict) and res.get("job"):
                self._on_job_started(res["job"])
            self._poll_status()
        except Exception as exc:
            self.notify(str(exc), True)

    def _on_job_started(self, jid: str) -> None:
        spid = self._prompt_page._session_pid
        if spid and not self._prompt_page._board_pid and spid in self.app.repo.transient_ids:
            self._last_transient_pid = spid
        self._poll_status()

    def _control_job(self, action: str, jid: str) -> None:
        try:
            self.app.action({"action": action, "job": jid, "confirm": True})
            self._poll_status()
        except Exception as exc:
            self.notify(str(exc), True)

    def _poll_status(self) -> None:
        try:
            st = self.app.status()
        except Exception:
            return

        jobs = st.get("jobs") or []
        active = self._job_banner.update_jobs(jobs)
        self._prompt_page.update_jobs(jobs)
        dm = st.get("drawing_model") or {}
        model = st.get("model") or {}

        # Compact status capsule (clickable -> 底模选择与保活设置)
        pref_label = str(dm.get("preferred_label") or Path(str(dm.get("preferred") or "")).stem or "")
        short_pref = pref_label.split("·")[0].strip()[:18] if pref_label else ""
        if active:
            pill_state = "busy"
            text = f"运行中 · {active.get('label', '任务')}"
            tip = active.get("message") or text
        elif dm.get("loaded"):
            pill_state = "ready"
            loaded_label = next(
                (m["label"] for m in (dm.get("models") or []) if m.get("value") == dm["loaded"]),
                Path(str(dm["loaded"])).stem,
            )
            text = f"已加载 · {loaded_label.split('·')[0].strip()[:18]}"
            tip = f"已预加载：{loaded_label}"
        elif dm.get("comfy_running"):
            pill_state = "ready"
            text = "ComfyUI 保活"
            tip = "ComfyUI 保活中"
        elif model.get("ready"):
            pill_state = "ready"
            text = "分析模型就绪"
            tip = "分析模型运行中"
        else:
            pill_state = "idle"
            text = f"待命 · {short_pref}" if short_pref else ("保活待命" if dm.get("keep_alive") else "待命")
            tip = f"首选底模：{pref_label or '未选择'}"

        self._status_dot.set_state(pill_state)
        self._model_status.setText(text)
        self._status_pill.setToolTip(tip)
        if self._status_pill.property("state") != pill_state:
            self._status_pill.setProperty("state", pill_state)
            self._status_pill.style().unpolish(self._status_pill)
            self._status_pill.style().polish(self._status_pill)

        # Inline 1-click Preload / Unload controls for Drawing Model
        draw_active = bool(dm.get("loaded") or dm.get("comfy_running"))
        busy = bool(active)
        self._preload_draw_btn.setVisible(not draw_active)
        self._preload_draw_btn.setEnabled(not busy and bool(dm.get("preferred") or dm.get("models")))
        self._preload_draw_btn.setToolTip(f"预加载 {pref_label}" if pref_label else "预加载首选底模")
        self._unload_draw_btn.setVisible(draw_active)
        self._unload_draw_btn.setEnabled(not busy)

        # Sync inline preload/unload control inside Prompt Studio
        self._prompt_page.update_drawing_model_state(dm, busy=busy)

        # Single contextual LLM button
        llm_owned = bool(model.get("owned"))
        llm_busy = bool(model.get("generating"))
        self._start_llm_btn.setVisible(not llm_owned)
        self._start_llm_btn.setEnabled(not llm_busy and not model.get("ready"))
        self._stop_llm_btn.setVisible(llm_owned)
        self._stop_llm_btn.setEnabled(not llm_busy)

        # Queue count
        q_items = (st.get("queue") or {}).get("items") or []
        q_cnt = sum(1 for i in q_items if i.get("status") in {"running", "waiting"})
        self._nav_queue.setText(f"任务队列 ({q_cnt})" if q_cnt else "任务队列")

        # Home activity sync
        if self._root_stack.currentIndex() == 0:
            self._home.update_activity(st)

        # Live progressive refresh as images complete during running drawing / second-pass jobs
        live_progress_bumped = False
        if active:
            ajid = active.get("id")
            prog_done = int((active.get("progress") or {}).get("completed") or 0)
            if ajid and prog_done > self._last_job_progress.get(ajid, 0):
                self._last_job_progress[ajid] = prog_done
                live_progress_bumped = True

        # Seed already-finished historical jobs silently on startup so old failures never pop up unprompted
        if not self._initial_poll_done:
            self._initial_poll_done = True
            for j in jobs:
                if j.get("status") in {"done", "failed", "cancelled", "interrupted"} and j.get("id"):
                    self._last_job_ids_done.add(j["id"])
            return

        # Auto-refresh stage when jobs finish
        newly_done = [j for j in jobs if j.get("status") in {"done", "failed", "cancelled"} and j["id"] not in self._last_job_ids_done]
        if newly_done:
            for j in newly_done:
                self._last_job_ids_done.add(j["id"])
                self._last_job_progress.pop(j["id"], None)
                if j.get("status") == "failed":
                    self.notify(f"{j.get('label','任务')}失败：{j.get('error') or j.get('message','')}", True)
            if self._current_stage == "prompt":
                self._prompt_page.refresh_gallery(live_update=False)
            elif self._current_stage == "drawing" and self._current_pid:
                self._drawing_page.load_project(self._current_pid, self.app.snapshot(self._current_pid))
            elif self._current_stage == "page" and self._current_pid:
                self._sb_page.load_project(self._current_pid, self.app.snapshot(self._current_pid))
            elif self._current_stage == "problems" and self._current_pid:
                self._problems_page.load_project(self._current_pid)
        elif live_progress_bumped and self._current_stage == "prompt":
            self._prompt_page.refresh_gallery(live_update=True)

    def closeEvent(self, event) -> None:
        force_interrupt = False
        if not self._smoke and self.app.has_active_work():
            ans = QMessageBox.question(
                self,
                "任务运行中",
                "当前仍有任务在执行，确定退出 LocalDraw 并中断正在进行的任务？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            force_interrupt = True
        if self._prompt_page._draft_timer.isActive():
            self._prompt_page._draft_timer.stop()
            try:
                self._prompt_page._save_draft()
            except Exception:
                pass
        self._poll_timer.stop()
        self._toast.stop_animations()
        if not self._smoke:
            for w in list(QApplication.topLevelWidgets()):
                if w is not self and w.isVisible():
                    try:
                        w.close()
                    except Exception:
                        pass
        self.app.shutdown(keep_comfy=self.app.comfy.keep_alive, interrupt_active=force_interrupt)
        event.accept()
        if not self._smoke:
            QApplication.quit()
