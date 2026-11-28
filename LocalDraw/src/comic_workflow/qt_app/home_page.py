"""Refined Editorial Home Page for LocalDraw (zero emojis, zero AI filler copy)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .icons import icon_pixmap, studio_icon
from .theme import current_theme, tokens

if TYPE_CHECKING:
    from ..app_core import StudioApp


class _CardBadgeIcon(QWidget):
    """Crisp 38x38 soft-tinted rounded badge with 20px monochrome vector icon."""

    def __init__(self, icon_name: str, *, primary: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._icon_name = icon_name
        self._primary = primary
        self.setFixedSize(38, 38)

    def paintEvent(self, event) -> None:
        t = tokens(current_theme())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(t["soft"] if self._primary else t["raised"]))
        p.drawRoundedRect(QRectF(0, 0, 38, 38), 10, 10)
        pm = icon_pixmap(self._icon_name, t["accent"] if self._primary else t["ink"], size=20, dpr=2.0)
        p.drawPixmap(9, 9, pm)
        p.end()


class HomePage(QWidget):
    """Landing dashboard preserving LocalDraw's familiar 4 workflows + recent works."""

    open_prompt_requested = Signal()
    new_story_requested = Signal()
    direct_import_requested = Signal()
    direct_storyboard_requested = Signal()
    prompt_board_requested = Signal()
    task_fusion_requested = Signal()
    queue_requested = Signal()
    characters_requested = Signal()
    manage_projects_requested = Signal()
    open_project_requested = Signal(str)
    open_import_setup_requested = Signal(str)
    open_draft_requested = Signal(str)
    resume_workspace_requested = Signal()

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        canvas = QWidget()
        canvas.setObjectName("Canvas")
        scroll.setWidget(canvas)

        center = QHBoxLayout(canvas)
        center.setContentsMargins(32, 24, 32, 32)
        center.addStretch(1)

        col = QWidget()
        col.setMaximumWidth(1140)
        col.setMinimumWidth(780)
        self._col = col
        self._wide: bool | None = None
        self._lay = QVBoxLayout(col)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(18)
        center.addWidget(col, 10)
        center.addStretch(1)

        # Clean Editorial Header Row
        hero = QHBoxLayout()
        hero.setSpacing(12)
        title = QLabel("创作空间")
        title.setObjectName("HomeHeroTitle")
        self._count_pill = QLabel("0 个作品")
        self._count_pill.setObjectName("BadgeAccent")
        self._resume_btn = QPushButton("回到工作台")
        self._resume_btn.setProperty("ghost", True)
        self._resume_btn.setIconSize(QSize(14, 14))
        self._resume_btn.clicked.connect(self.resume_workspace_requested.emit)
        hero.addWidget(title)
        hero.addWidget(self._count_pill, 0, Qt.AlignmentFlag.AlignVCenter)
        hero.addStretch(1)
        hero.addWidget(self._resume_btn)
        self._lay.addLayout(hero)

        # Live Activity Banner (hidden when idle)
        self._activity = QFrame()
        self._activity.setObjectName("JobBanner")
        self._activity.setVisible(False)
        al = QHBoxLayout(self._activity)
        al.setContentsMargins(14, 9, 14, 9)
        self._activity_text = QLabel("")
        self._activity_text.setStyleSheet("font-weight: 600;")
        al.addWidget(self._activity_text, 1)
        btn_q = QPushButton("查看队列")
        btn_q.setProperty("compact", True)
        btn_q.clicked.connect(self.queue_requested.emit)
        al.addWidget(btn_q)
        self._lay.addWidget(self._activity)

        # Body: stacked on 1080p-class widths, two columns (tasks | recent works) on 2K / ultrawide.
        self._body = QBoxLayout(QBoxLayout.Direction.TopToBottom)
        self._body.setSpacing(18)
        left_w = QWidget()
        left = QVBoxLayout(left_w)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(18)
        right_w = QWidget()
        right = QVBoxLayout(right_w)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(18)
        self._body.addWidget(left_w, 1)
        self._body.addWidget(right_w, 1)
        self._lay.addLayout(self._body)

        # 4 Primary Task Cards
        grid = QGridLayout()
        grid.setSpacing(14)
        self._grid = grid
        self._cards: list[QFrame] = []
        self._badges: list[_CardBadgeIcon] = []
        cards = [
            ("01", "spark", "提示词出图", "正负提示词、角色与 LoRA 出图，支持二采精修。", "开始出图", self.open_prompt_requested, True),
            ("02", "script", "创作新漫画", "从故事构想生成剧本、分镜、对白与候选图。", "新建漫画", self.new_story_requested, False),
            ("03", "import", "逐格提示词出图", "批量导入每格 TXT / JSON 提示词并统一出图。", "导入提示词", self.direct_import_requested, False),
            ("04", "storyboard", "分镜出图", "生成分镜画格、逐格选图并排版为整页。", "新建分镜", self.prompt_board_requested, False),
        ]
        for idx, (num, ic_name, card_title, desc, cta, sig, is_hero) in enumerate(cards):
            card = QFrame()
            card.setObjectName("HeroCard" if is_hero else "HomeCard")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(18, 16, 18, 16)
            cl.setSpacing(10)

            top_row = QHBoxLayout()
            badge = _CardBadgeIcon(ic_name, primary=is_hero)
            self._badges.append(badge)
            n_lbl = QLabel(num)
            n_lbl.setObjectName("Caption")
            n_lbl.setStyleSheet('font-family: "Consolas", monospace;')
            top_row.addWidget(badge)
            top_row.addStretch(1)
            top_row.addWidget(n_lbl, 0, Qt.AlignmentFlag.AlignTop)
            cl.addLayout(top_row)

            t_lbl = QLabel(card_title)
            t_lbl.setStyleSheet("font-size: 17px; font-weight: 700;")
            d_lbl = QLabel(desc)
            d_lbl.setObjectName("Muted")
            d_lbl.setWordWrap(True)
            d_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

            btn = QPushButton(cta)
            btn.setProperty("primary", is_hero)
            btn.setMinimumHeight(34)
            btn.clicked.connect(sig.emit)

            card.setFixedHeight(212)
            cl.addWidget(t_lbl)
            cl.addWidget(d_lbl, 1)
            cl.addWidget(btn)
            self._cards.append(card)
            grid.addWidget(card, 0, idx)
            grid.setColumnStretch(idx, 1)
        left.addLayout(grid)

        # Compact Secondary Tools Strip (quiet ghost buttons with vector icons)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self._tool_buttons: list[tuple[QPushButton, str]] = []
        for label, ic_name, sig in [
            ("导入分镜", "storyboard", self.direct_storyboard_requested),
            ("融合任务", "merge", self.task_fusion_requested),
            ("任务队列", "queue", self.queue_requested),
            ("角色库", "characters", self.characters_requested),
        ]:
            b = QPushButton(label)
            b.setProperty("ghost", True)
            b.setIconSize(QSize(15, 15))
            b.clicked.connect(sig.emit)
            tools.addWidget(b)
            self._tool_buttons.append((b, ic_name))
        tools.addStretch(1)
        left.addLayout(tools)
        left.addStretch(1)

        # Story Drafts Shelf
        self._draft_box = QFrame()
        self._draft_box.setObjectName("Pane")
        self._draft_box.setVisible(False)
        dl = QVBoxLayout(self._draft_box)
        dl.setContentsMargins(16, 14, 16, 14)
        dl.setSpacing(8)
        dh = QLabel("剧本草稿")
        dh.setObjectName("SectionTitle")
        dl.addWidget(dh)
        self._draft_list = QVBoxLayout()
        self._draft_list.setSpacing(4)
        dl.addLayout(self._draft_list)
        right.addWidget(self._draft_box)

        # Recent Works Pane
        recent_box = QFrame()
        recent_box.setObjectName("Pane")
        rl = QVBoxLayout(recent_box)
        rl.setContentsMargins(18, 14, 18, 14)
        rl.setSpacing(10)

        rh = QHBoxLayout()
        rh.setSpacing(8)
        rt = QLabel("最近作品")
        rt.setObjectName("SectionTitle")
        self._search = QLineEdit()
        self._search.setPlaceholderText("筛选作品…")
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(240)
        self._search.textChanged.connect(self._render_projects)
        self._manage_btn = QPushButton("管理")
        self._manage_btn.setProperty("ghost", True)
        self._manage_btn.setIconSize(QSize(14, 14))
        self._manage_btn.clicked.connect(self.manage_projects_requested.emit)
        rh.addWidget(rt)
        rh.addStretch(1)
        rh.addWidget(self._search)
        rh.addWidget(self._manage_btn)
        rl.addLayout(rh)

        self._proj_list = QVBoxLayout()
        self._proj_list.setSpacing(2)
        rl.addLayout(self._proj_list)
        right.addWidget(recent_box)
        right.addStretch(1)
        self._lay.addStretch(1)

        self._projects: list[dict] = []
        self._apply_layout_mode(False)
        self.update_theme(current_theme())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_layout_mode(self.width() >= 1800)

    def _apply_layout_mode(self, wide: bool) -> None:
        """Wide (>=1800 logical px, e.g. 2K / 3440x1440): 2x2 task cards beside recent works."""
        if wide == self._wide:
            return
        self._wide = wide
        self._body.setDirection(QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom)
        cols = 2 if wide else 4
        for card in self._cards:
            self._grid.removeWidget(card)
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // cols, i % cols)
        for c in range(4):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)
        self._col.setMaximumWidth(1680 if wide else 1140)

    def update_theme(self, theme_name: str) -> None:
        t = tokens(theme_name)
        for badge in self._badges:
            badge.update()
        for btn, ic_name in self._tool_buttons:
            btn.setIcon(studio_icon(ic_name, t["muted"], 15))
        self._resume_btn.setIcon(studio_icon("chevron-right", t["muted"], 14))
        self._manage_btn.setIcon(studio_icon("projects", t["muted"], 14))

    def refresh(self, projects: list[dict], status: dict | None = None) -> None:
        self._projects = list(projects or [])
        self._count_pill.setText(f"{len(self._projects)} 个作品")
        self._render_projects()
        self._render_drafts()
        if status is not None:
            self.update_activity(status)

    def update_activity(self, status: dict) -> None:
        jobs = status.get("jobs") or []
        queue = status.get("queue") or {}
        active = next((j for j in jobs if j.get("status") in {"running", "pausing", "paused"}), None)
        items = queue.get("items") or []
        waiting = sum(1 for i in items if i.get("status") == "waiting")
        attention = sum(1 for i in items if i.get("status") in {"failed", "partial", "interrupted"})
        if active:
            self._activity_text.setText(f"{active.get('label', '任务')} · {active.get('message', '')}")
            self._activity.setVisible(True)
        elif waiting or attention:
            parts = []
            if waiting:
                parts.append(f"待执行 {waiting} 项")
            if attention:
                parts.append(f"异常 {attention} 项")
            self._activity_text.setText("队列：" + " · ".join(parts))
            self._activity.setVisible(True)
        else:
            self._activity.setVisible(False)

    def _clear_layout(self, lay: QVBoxLayout) -> None:
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _render_drafts(self) -> None:
        self._clear_layout(self._draft_list)
        try:
            drafts = self.app.creative_drafts()
        except Exception:
            drafts = []
        self._draft_box.setVisible(bool(drafts))
        for d in drafts[:5]:
            row = QFrame()
            row.setObjectName("ListRow")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(10, 7, 10, 7)
            t = QLabel(d.get("title") or "未命名草稿")
            t.setStyleSheet("font-weight: 600;")
            m = QLabel("剧本草稿")
            m.setObjectName("Muted")
            btn = QPushButton("打开")
            btn.setProperty("compact", True)
            jid = d["id"]
            btn.clicked.connect(lambda _=False, j=jid: self.open_draft_requested.emit(j))
            hl.addWidget(t, 1)
            hl.addWidget(m)
            hl.addWidget(btn)
            self._draft_list.addWidget(row)

    def _render_projects(self) -> None:
        self._clear_layout(self._proj_list)
        q = self._search.text().strip().lower()
        last_pid = str(self.app.ui_state.get("last_project") or "")
        matches = [p for p in self._projects if not q or q in str(p.get("title", "")).lower()]
        matches.sort(key=lambda p: (0 if p["id"] == last_pid else 1))

        if not matches:
            empty = QLabel("无匹配作品" if self._projects else "暂无作品")
            empty.setObjectName("Muted")
            empty.setContentsMargins(6, 10, 6, 10)
            self._proj_list.addWidget(empty)
            return

        for p in matches[:10]:
            row = QFrame()
            row.setObjectName("ListRow")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(10, 8, 10, 8)
            hl.setSpacing(10)

            title = QLabel(p.get("title") or p["id"])
            title.setStyleSheet("font-weight: 600;")
            pages = int(p.get("pages") or 0)
            kind = "分镜出图" if p.get("prompt_board") else f"{pages} 页分镜"
            meta = QLabel(f"上次 · {kind}" if p["id"] == last_pid else kind)
            meta.setObjectName("Muted")

            hl.addWidget(title, 1)
            hl.addWidget(meta)

            pid = p["id"]
            if p.get("prompt_import") or pages > 0:
                setup_btn = QPushButton("出图设置")
                setup_btn.setProperty("ghost", True)
                setup_btn.setProperty("compact", True)
                setup_btn.clicked.connect(lambda _=False, x=pid: self.open_import_setup_requested.emit(x))
                hl.addWidget(setup_btn)

            open_btn = QPushButton("打开")
            open_btn.setProperty("compact", True)
            open_btn.clicked.connect(lambda _=False, x=pid: self.open_project_requested.emit(x))
            hl.addWidget(open_btn)
            self._proj_list.addWidget(row)

        if len(matches) > 10:
            more = QLabel(f"共 {len(matches)} 个作品")
            more.setObjectName("Muted")
            more.setContentsMargins(10, 6, 10, 2)
            self._proj_list.addWidget(more)
