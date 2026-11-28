"""Native PySide6 3-Column Prompt Studio + Second Pass Secondary Panel."""

from __future__ import annotations

import copy
import json
import math
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..character_prompt_cleanup import default_visual_prompt
from ..clothing_policy import resolve_selections
from ..repository import PROMPT_WORKSPACE_ID
from .icons import icon_pixmap, studio_icon
from .motion import fade_widget_in
from .theme import current_theme, tokens
from .widgets import (
    LORA_STAR_COLOR,
    ROLE_LORA_FAV,
    DecimalSpinBox,
    ImageLightboxDialog,
    ImageSizeWidget,
    JobBannerWidget,
    ZoomableImageView,
    apply_lora_triggers,
    attach_lora_star_delegate,
    character_matches_search,
    checkpoint_family_and_dir,
    copy_images_to_clipboard,
    image_cache,
    insert_trigger_phrase,
    lora_choice_name,
    lora_detail_tag,
    lora_disambiguated_names,
    lora_display_name,
    lora_matches_search,
    lora_variant,
    remove_trigger_phrase,
    resolve_lora_trigger,
    run_async,
    sort_loras_favorites_first,
    trigger_present_in_prompt,
)

if TYPE_CHECKING:
    from ..app_core import StudioApp


_CHAT_CARD_RE = re.compile(
    r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\*\*)?(?:personality|speech\s+style|interaction\s+guidelines|background|relationships|system\s+prompt|性格(?:与行为习惯)?|说话方式(?:与台词样例)?|互动(?:规则|指南)|人物关系|背景(?:设定)?|系统提示词)\s*(?:\*\*)?\s*[:：]",
    re.I,
)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")

_SP_SAMPLERS = ["er_sde", "dpmpp_2m_sde", "res_multistep", "heunpp2", "dpmpp_2m", "euler", "euler_ancestral", "uni_pc"]
_SP_SCHEDULERS = ["beta", "simple", "sgm_uniform", "karras", "kl_optimal", "normal"]


class _GrowingTextEdit(QPlainTextEdit):
    """Prefers its minimum height, so 1080p never scrolls while tall screens grow it via layout stretch."""

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        return QSize(hint.width(), self.minimumHeight() or hint.height())


class _GrowingList(QListWidget):
    """Search result list that prefers its minimum height and grows into spare pane height."""

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        hint = super().sizeHint()
        return QSize(hint.width(), self.minimumHeight() or hint.height())


class _SelectedLoraRow(QFrame):
    """Compact selected LoRA card with inline weight, favorite star, trigger preview & metadata editor."""

    changed = Signal()
    removed = Signal(str)
    favorite_toggled = Signal(str)
    insert_trigger_requested = Signal(str)
    trigger_edited = Signal(str, str, str)
    trigger_text_changed = Signal()

    def __init__(
        self,
        file: str,
        weight: float,
        label_info: dict,
        characters: list[dict],
        on_save_label,
        *,
        favorite: bool = False,
        detail_tag: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.file = file
        self.label_info = dict(label_info or {})
        self._on_save_label = on_save_label
        self._favorite = bool(favorite)
        self.setObjectName("SubCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(9, 7, 9, 7)
        lay.setSpacing(5)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.check = QCheckBox()
        self.check.setChecked(True)
        self.check.toggled.connect(self._on_toggle)
        base_name = lora_display_name(file)
        shown_name = f"{base_name} · {detail_tag}" if (detail_tag and detail_tag.lower() not in base_name.lower()) else base_name
        self._name_lbl = QLabel(shown_name)
        self._name_lbl.setStyleSheet("font-weight: 600;")
        self._name_lbl.setToolTip(file)
        self._name_lbl.setWordWrap(True)
        top.addWidget(self.check)
        top.addWidget(self._name_lbl, 1)

        self.fav_btn = QPushButton()
        self.fav_btn.setProperty("ghost", True)
        self.fav_btn.setProperty("compact", True)
        self.fav_btn.setFixedSize(24, 24)
        self.fav_btn.setIconSize(QSize(13, 13))
        self.fav_btn.clicked.connect(lambda: self.favorite_toggled.emit(self.file))
        self.set_favorite(self._favorite)
        top.addWidget(self.fav_btn)

        self.weight = DecimalSpinBox()
        self.weight.setRange(0.0, 2.0)
        self.weight.setSingleStep(0.05)
        self.weight.setDecimals(2)
        self.weight.setValue(float(weight))
        self.weight.setFixedWidth(82)
        self.weight.valueChanged.connect(lambda _: self.changed.emit())
        top.addWidget(self.weight)
        lay.addLayout(top)

        sub = QHBoxLayout()
        sub.setSpacing(6)
        vkind, vlabel = lora_variant(file)
        if vlabel:
            pill = QLabel(vlabel)
            pill.setObjectName("BadgeSuccess" if vkind == "character" else "BadgeWarn")
            sub.addWidget(pill)

        trig_raw = str(self.label_info.get("trigger_words") or "").strip()
        self._trig_preview = QLabel(
            f"后台触发词：{trig_raw[:18]}{'…' if len(trig_raw) > 18 else ''}" if trig_raw else "未设触发词"
        )
        self._trig_preview.setObjectName("Muted")
        self._trig_preview.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._trig_preview.setMinimumWidth(0)
        self._trig_preview.setToolTip(f"出图时后台自动加入：{trig_raw}" if trig_raw else file)
        sub.addWidget(self._trig_preview, 1)

        self._edit_btn = QPushButton("编辑")
        self._edit_btn.setProperty("ghost", True)
        self._edit_btn.setProperty("compact", True)
        self._edit_btn.setCheckable(True)
        self._edit_btn.toggled.connect(self._toggle_details)
        sub.addWidget(self._edit_btn)
        lay.addLayout(sub)

        self._details = QWidget()
        self._details.setVisible(False)
        dl = QVBoxLayout(self._details)
        dl.setContentsMargins(2, 4, 2, 2)
        dl.setSpacing(5)

        fpath = QLabel(file)
        fpath.setObjectName("Muted")
        fpath.setWordWrap(True)
        dl.addWidget(fpath)

        trow = QHBoxLayout()
        trow.setSpacing(4)
        self.trigger_edit = QLineEdit(str(self.label_info.get("trigger_words", "")))
        self.trigger_edit.setPlaceholderText("触发词（出图时后台自动加入）")
        self.trigger_edit.textChanged.connect(lambda _t: self.trigger_text_changed.emit())
        self.trigger_edit.editingFinished.connect(self._save_meta)
        add_trig = QPushButton("插入正文")
        add_trig.setProperty("ghost", True)
        add_trig.setProperty("compact", True)
        add_trig.setToolTip("手动插入到正向提示词正文（多人图分句定位防串特征）")
        add_trig.clicked.connect(lambda: self.insert_trigger_requested.emit(self.file))
        trow.addWidget(self.trigger_edit, 1)
        trow.addWidget(add_trig)
        dl.addLayout(trow)

        orow = QHBoxLayout()
        orow.addWidget(QLabel("归属"))
        self.owner_combo = QComboBox()
        self.owner_combo.addItem("全图风格", "")
        cur_owner = str(self.label_info.get("character_id") or "")
        for c in characters:
            self.owner_combo.addItem(f"角色 · {c['name']}", c["id"])
        idx = self.owner_combo.findData(cur_owner)
        self.owner_combo.setCurrentIndex(max(0, idx))
        self.owner_combo.currentIndexChanged.connect(self._save_meta)
        orow.addWidget(self.owner_combo, 1)
        dl.addLayout(orow)

        lay.addWidget(self._details)

    def set_favorite(self, favorite: bool) -> None:
        self._favorite = bool(favorite)
        if self._favorite:
            self.fav_btn.setIcon(studio_icon("star-filled", LORA_STAR_COLOR, 13))
            self.fav_btn.setToolTip("取消收藏")
        else:
            self.fav_btn.setIcon(studio_icon("star", tokens(current_theme())["muted"], 13))
            self.fav_btn.setToolTip("收藏此 LoRA（搜索同类型时排第一）")

    def _on_toggle(self, checked: bool) -> None:
        if not checked:
            self.removed.emit(self.file)

    def _toggle_details(self, shown: bool) -> None:
        self._details.setVisible(shown)

    def _save_meta(self) -> None:
        old_trig = str(self.label_info.get("trigger_words") or "").strip()
        trig = self.trigger_edit.text().strip()
        self.label_info["trigger_words"] = trig
        self.label_info["character_id"] = self.owner_combo.currentData() or ""
        self._trig_preview.setText(
            f"后台触发词：{trig[:18]}{'…' if len(trig) > 18 else ''}" if trig else "未设触发词"
        )
        self._trig_preview.setToolTip(f"出图时后台自动加入：{trig}" if trig else self.file)
        self._on_save_label(self.file, self.label_info, self.weight.value())
        if old_trig != trig:
            self.trigger_edited.emit(self.file, old_trig, trig)
        self.changed.emit()


class _SelectedCharacterRow(QFrame):
    """Selected character card with thumbnail and editable visual appearance prompt."""

    changed = Signal()
    removed = Signal(str)

    def __init__(self, card: dict, image_path: Path | None, prompt_text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.card = card
        self.default_prompt = default_visual_prompt(card.get("profile") or {}) or card.get("name", "")
        self.setObjectName("SubCard")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(9, 7, 9, 7)
        lay.setSpacing(4)

        top = QHBoxLayout()
        self.check = QCheckBox(card.get("name", ""))
        self.check.setChecked(True)
        self.check.setStyleSheet("font-weight: 600;")
        self.check.toggled.connect(lambda c: None if c else self.removed.emit(card["id"]))
        toggle_btn = QPushButton("外观")
        toggle_btn.setProperty("ghost", True)
        toggle_btn.setProperty("compact", True)
        toggle_btn.setCheckable(True)
        toggle_btn.toggled.connect(lambda v: self._detail.setVisible(v))
        top.addWidget(self.check, 1)
        top.addWidget(toggle_btn)
        lay.addLayout(top)

        self._detail = QWidget()
        self._detail.setVisible(False)
        dl = QHBoxLayout(self._detail)
        dl.setContentsMargins(0, 4, 0, 0)
        dl.setSpacing(6)
        if image_path and image_path.is_file():
            thumb = QLabel()
            thumb.setFixedSize(52, 72)
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_cache().request_thumbnail(
                image_path,
                52,
                72,
                lambda _p, pm, lbl=thumb: lbl.setPixmap(pm) if not pm.isNull() else None,
            )
            dl.addWidget(thumb, 0, Qt.AlignmentFlag.AlignTop)

        right = QVBoxLayout()
        self.prompt_edit = QPlainTextEdit(prompt_text if prompt_text is not None else self.default_prompt)
        self.prompt_edit.setMaximumHeight(78)
        self.prompt_edit.textChanged.connect(self.changed.emit)
        reset_btn = QPushButton("重置默认外观")
        reset_btn.setProperty("compact", True)
        reset_btn.clicked.connect(lambda: self.prompt_edit.setPlainText(self.default_prompt))
        right.addWidget(self.prompt_edit)
        right.addWidget(reset_btn, 0, Qt.AlignmentFlag.AlignRight)
        dl.addLayout(right, 1)
        lay.addWidget(self._detail)


class _ThumbCard(QPushButton):
    """Labeled thumbnail tile backed by shared async ImageCache; selection flips without rebuild."""

    double_clicked = Signal()

    def __init__(
        self,
        path: Path,
        caption: str,
        tooltip: str,
        *,
        selected: bool = False,
        applied: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._path = path
        self._selected = selected
        self.setFixedSize(70, 84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setProperty("thumb", True)
        self.setProperty("selected", selected)
        self.setProperty("applied", applied)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 3)
        lay.setSpacing(2)

        self._img_lbl = QLabel()
        self._img_lbl.setFixedSize(60, 58)
        self._img_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._img_lbl, 0, Qt.AlignmentFlag.AlignCenter)

        self._cap_lbl = QLabel(caption)
        self._cap_lbl.setObjectName("ThumbTagApplied" if applied else "ThumbTag")
        self._cap_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cap_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        lay.addWidget(self._cap_lbl)

        if path and path.is_file():
            image_cache().request_thumbnail(path, 60, 58, self._apply_thumb)
        else:
            self._img_lbl.setText("缺失")

    def _apply_thumb(self, path: Path, pm: QPixmap) -> None:
        if path != self._path or pm.isNull():
            return
        self._img_lbl.setPixmap(pm)

    def set_selected(self, selected: bool) -> None:
        if self._selected == selected:
            return
        self._selected = selected
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PromptStudioPage(QWidget):
    """3-Column Prompt Drawing Workspace + In-Place Second Pass Secondary Panel."""

    notify_requested = Signal(str, bool)
    job_started = Signal(str)
    control_requested = Signal(str, str)
    open_characters_requested = Signal()
    open_queue_requested = Signal()
    open_board_layout_requested = Signal(str)

    def __init__(self, app: StudioApp, parent: QWidget | None = None):
        super().__init__(parent)
        self.app = app
        self._board_pid: str | None = None
        self._session_pid: str | None = None
        self._project_snap: dict | None = None
        self._drawing_opts: dict = {}
        self._characters_all: list[dict] = []
        self._char_by_id: dict[str, dict] = {}
        self._selected_chars: dict[str, _SelectedCharacterRow] = {}
        self._selected_loras: dict[str, _SelectedLoraRow] = {}
        self._auto_inserted_triggers: dict[str, str] = {}
        self._sp_selected_loras: dict[str, float] = {}
        self._sp_auto_inserted_triggers: dict[str, str] = {}
        self._gallery_entries: list[dict] = []  # [{base, variants, applied_id, active, active_id, path}]
        self._known_base_ids: set[str] = set()
        self._board_selections: dict[str, dict] = {}
        self._thumb_sig: tuple | None = None
        self._thumb_cards: list[tuple[str, _ThumbCard]] = []
        self._active_gallery_idx = 0
        self._sp_state: dict | None = None
        self._applying_sp_preset = False
        self._sp_denoise_manual = False
        self._sp_scale_manual = False
        self._before_translate: dict | None = None
        self._cleared_backup: dict | None = None
        self._loading_ui = False
        self._themed_icons: list[tuple[QPushButton, str, int]] = []

        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(450)
        self._draft_timer.timeout.connect(self._save_draft)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(self._splitter, 1)

        # Left column host: compact persistent JobBanner on top (only spans left controls),
        # stacked workspace below (Page 0 = Input + Selection, Page 1 = Second Pass).
        left_host = QWidget()
        left_lay = QVBoxLayout(left_host)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        self._job_banner = JobBannerWidget(left_host)
        self._job_banner.control_requested.connect(self.control_requested.emit)
        left_lay.addWidget(self._job_banner)

        self._left_stack = QStackedWidget()
        self._normal_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._normal_splitter.addWidget(self._build_input_pane())
        self._normal_splitter.addWidget(self._build_selection_pane())
        self._normal_splitter.setSizes([395, 295])
        self._left_stack.addWidget(self._normal_splitter)
        self._left_stack.addWidget(self._build_second_pass_pane())
        left_lay.addWidget(self._left_stack, 1)

        self._splitter.addWidget(left_host)
        self._splitter.addWidget(self._build_gallery_pane())
        # Extra width (e.g. 3440x1440 ultrawide) goes to the image preview; the editing
        # columns keep a readable measure. Initial widths are set once in _apply_default_split.
        self._normal_splitter.setStretchFactor(0, 1)
        self._normal_splitter.setStretchFactor(1, 0)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([690, 540])
        for sp in (self._splitter, self._normal_splitter):
            sp.setChildrenCollapsible(False)
        self._split_initialized = False

        self.refresh_icons()

        # Shortcuts
        QShortcut(QKeySequence("Ctrl+Return"), self, self._on_ctrl_enter)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._on_ctrl_enter)
        QShortcut(QKeySequence("Escape"), self, self._on_escape)
        QShortcut(QKeySequence("Alt+Left"), self, lambda: self._step_gallery(-1))
        QShortcut(QKeySequence("Alt+Right"), self, lambda: self._step_gallery(1))
        self._undo_delete_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self._undo_delete_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._undo_delete_shortcut.activated.connect(self._on_ctrl_z)
        for n in range(1, 10):
            QShortcut(QKeySequence(f"Alt+{n}"), self, lambda idx=n - 1: self._on_alt_number(idx))

    def _bind_icon(self, btn: QPushButton, icon_name: str, size: int = 14) -> None:
        btn.setIconSize(QSize(size, size))
        self._themed_icons.append((btn, icon_name, size))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._maybe_init_split()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._maybe_init_split()

    def _maybe_init_split(self) -> None:
        # Only once the real (maximized) window is up, so we never size against Qt's 640x480 default.
        if not self._split_initialized and self.isVisible() and self.width() >= 900:
            self._split_initialized = True
            self._apply_default_split(self.width())

    def _apply_default_split(self, total: int) -> None:
        """Prompt input ~30% (400-600px), LoRA/角色 ~21% (290-420px), preview gets the rest."""
        inp = max(400, min(600, int(total * 0.30)))
        sel = max(290, min(420, int(total * 0.21)))
        left = inp + sel + self._normal_splitter.handleWidth()
        gallery = max(420, total - left - self._splitter.handleWidth())
        self._normal_splitter.setSizes([inp, sel])
        self._splitter.setSizes([left, gallery])

    def refresh_icons(self) -> None:
        t = tokens(current_theme())
        for btn, ic_name, sz in self._themed_icons:
            btn.setIcon(studio_icon(ic_name, t["muted"], sz))
        if hasattr(self, "_empty_art_icon"):
            self._empty_art_icon.setPixmap(icon_pixmap("image", t["caption"], size=32, dpr=2.0))
        if hasattr(self, "_sp_warn_icon"):
            self._sp_warn_icon.setPixmap(icon_pixmap("alert", t["warning"], size=14, dpr=2.0))
        self._update_inline_preload_btn()
        if hasattr(self, "_lora_results") and self._drawing_opts:
            self._refresh_lora_search_list()
            self._refresh_char_search_list()
            if self._sp_state is not None:
                self._refresh_sp_lora_results()
                self._render_sp_loras()

    # ==================== BUILD UI ====================

    def _build_input_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("Pane")
        outer = QVBoxLayout(pane)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(8)

        # Top utility bar
        top = QHBoxLayout()
        clear_btn = QPushButton("清空")
        clear_btn.setProperty("ghost", True)
        clear_btn.setProperty("compact", True)
        self._bind_icon(clear_btn, "trash", 13)
        clear_btn.clicked.connect(self._clear_all)
        self._undo_clear_btn = QPushButton("撤销")
        self._undo_clear_btn.setProperty("ghost", True)
        self._undo_clear_btn.setProperty("compact", True)
        self._undo_clear_btn.setVisible(False)
        self._undo_clear_btn.clicked.connect(self._undo_clear)
        self._draft_lbl = QLabel("自动保存")
        self._draft_lbl.setObjectName("Caption")
        top.addWidget(clear_btn)
        top.addWidget(self._undo_clear_btn)
        top.addStretch(1)
        top.addWidget(self._draft_lbl)
        outer.addLayout(top)

        # Scrollable form area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setMinimumWidth(0)
        form = QVBoxLayout(inner)
        form.setContentsMargins(0, 0, 6, 0)
        form.setSpacing(8)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # Board panel picker (hidden unless board mode)
        self._board_bar = QWidget()
        self._board_bar.setVisible(False)
        bl = QVBoxLayout(self._board_bar)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        brow = QHBoxLayout()
        brow.addWidget(QLabel("当前画格"))
        self._board_panel = QComboBox()
        self._board_panel.currentIndexChanged.connect(self._on_board_panel_changed)
        brow.addWidget(self._board_panel, 1)
        bl.addLayout(brow)
        self._board_hint = QLabel("")
        self._board_hint.setObjectName("Muted")
        self._board_hint.setWordWrap(True)
        bl.addWidget(self._board_hint)
        form.addWidget(self._board_bar)

        # Segmented Mode Switcher (borderless stack, replaces bulky QTabWidget)
        seg_track = QFrame()
        seg_track.setObjectName("SegmentTrack")
        seg_lay = QHBoxLayout(seg_track)
        seg_lay.setContentsMargins(3, 3, 3, 3)
        seg_lay.setSpacing(3)
        self._mode_seg_btns: list[QPushButton] = []
        for idx, lbl in enumerate(("正向 / 负面", "JSON", "Anima 衣装")):
            sb = QPushButton(lbl)
            sb.setProperty("segment", True)
            sb.setProperty("active", idx == 0)
            sb.clicked.connect(lambda _=False, i=idx: self._set_mode_tab(i))
            self._mode_seg_btns.append(sb)
            seg_lay.addWidget(sb, 1)
        form.addWidget(seg_track)

        self._mode_stack = QStackedWidget()

        # Mode 0: Positive / Negative
        t_pn = QWidget()
        pnl = QVBoxLayout(t_pn)
        pnl.setContentsMargins(0, 4, 0, 0)
        pnl.setSpacing(6)
        ph = QHBoxLayout()
        ph.addWidget(QLabel("正向提示词"))
        ph.addStretch(1)
        cp_pos = QPushButton("复制")
        cp_pos.setProperty("ghost", True)
        cp_pos.setProperty("compact", True)
        self._bind_icon(cp_pos, "copy", 13)
        cp_pos.clicked.connect(lambda: self._copy_text(self.positive.toPlainText()))
        ps_pos = QPushButton("粘贴")
        ps_pos.setProperty("ghost", True)
        ps_pos.setProperty("compact", True)
        self._bind_icon(ps_pos, "paste", 13)
        ps_pos.clicked.connect(self._paste_positive)
        ph.addWidget(cp_pos)
        ph.addWidget(ps_pos)
        pnl.addLayout(ph)

        self.positive = _GrowingTextEdit()
        self.positive.setPlaceholderText("输入画面描述（Ctrl+Enter 生成）…")
        self.positive.setMinimumHeight(120)
        self.positive.textChanged.connect(self._on_input_changed)
        pnl.addWidget(self.positive, 3)

        nh = QHBoxLayout()
        self._neg_title = QLabel("负面提示词")
        nh.addWidget(self._neg_title)
        nh.addStretch(1)
        cp_neg = QPushButton("复制")
        cp_neg.setProperty("ghost", True)
        cp_neg.setProperty("compact", True)
        self._bind_icon(cp_neg, "copy", 13)
        cp_neg.clicked.connect(lambda: self._copy_text(self.negative.toPlainText()))
        nh.addWidget(cp_neg)
        pnl.addLayout(nh)

        self.negative = _GrowingTextEdit()
        self.negative.setPlaceholderText("可留空")
        self.negative.setMinimumHeight(60)
        self.negative.setMaximumHeight(200)
        self.negative.textChanged.connect(self._on_input_changed)
        pnl.addWidget(self.negative, 1)
        self._mode_stack.addWidget(t_pn)

        # Mode 1: JSON Mode
        t_js = QWidget()
        jsl = QVBoxLayout(t_js)
        jsl.setContentsMargins(0, 4, 0, 0)
        jsl.setSpacing(6)
        jbar = QHBoxLayout()
        cp_js = QPushButton("复制 JSON")
        cp_js.setProperty("ghost", True)
        cp_js.setProperty("compact", True)
        self._bind_icon(cp_js, "copy", 13)
        cp_js.clicked.connect(lambda: self._copy_text(self._json_edit.toPlainText()))
        sync_js = QPushButton("同步到正负面")
        sync_js.setProperty("compact", True)
        sync_js.clicked.connect(self._apply_json_to_fields)
        jbar.addWidget(cp_js)
        jbar.addWidget(sync_js)
        jbar.addStretch(1)
        jsl.addLayout(jbar)
        self._json_edit = _GrowingTextEdit()
        self._json_edit.setMinimumHeight(180)
        self._json_edit.setStyleSheet('font-family: "Consolas", monospace;')
        jsl.addWidget(self._json_edit)
        self._json_err = QLabel("")
        self._json_err.setObjectName("Muted")
        jsl.addWidget(self._json_err)
        self._mode_stack.addWidget(t_js)

        # Mode 2: Anima Wardrobe V4
        t_wd = QWidget()
        wdl = QVBoxLayout(t_wd)
        wdl.setContentsMargins(0, 4, 0, 0)
        wdl.setSpacing(6)
        wgrid = QGridLayout()
        wgrid.setSpacing(6)
        self._wd_scope = QComboBox()
        for val, lbl in [("outfit", "仅服装"), ("character", "角色与服装"), ("scene", "完整画面")]:
            self._wd_scope.addItem(lbl, val)
        self._wd_style = QComboBox()
        self._wd_level = QComboBox()
        self._wd_char_search = QLineEdit()
        self._wd_char_search.setPlaceholderText("筛选角色…")
        self._wd_char_search.textChanged.connect(self._filter_wardrobe_chars)
        self._wd_char = QComboBox()
        self._wd_seed = QLineEdit()
        self._wd_seed.setPlaceholderText("随机种子")
        wgrid.addWidget(QLabel("范围"), 0, 0)
        wgrid.addWidget(self._wd_scope, 0, 1)
        wgrid.addWidget(QLabel("风格"), 0, 2)
        wgrid.addWidget(self._wd_style, 0, 3)
        wgrid.addWidget(QLabel("分级"), 1, 0)
        wgrid.addWidget(self._wd_level, 1, 1)
        wgrid.addWidget(QLabel("种子"), 1, 2)
        wgrid.addWidget(self._wd_seed, 1, 3)
        wgrid.addWidget(QLabel("筛角色"), 2, 0)
        wgrid.addWidget(self._wd_char_search, 2, 1)
        wgrid.addWidget(QLabel("角色"), 2, 2)
        wgrid.addWidget(self._wd_char, 2, 3)
        wdl.addLayout(wgrid)

        self._wd_preview = QPlainTextEdit()
        self._wd_preview.setReadOnly(True)
        self._wd_preview.setMaximumHeight(88)
        self._wd_preview.setPlaceholderText("衣装预览")
        wdl.addWidget(self._wd_preview)

        wb = QHBoxLayout()
        gen_wd = QPushButton("抽一组")
        gen_wd.setProperty("compact", True)
        gen_wd.setProperty("primary", True)
        gen_wd.clicked.connect(self._run_wardrobe_preview)
        app_wd = QPushButton("追加")
        app_wd.setProperty("compact", True)
        app_wd.clicked.connect(lambda: self._apply_wardrobe(replace=False))
        rep_wd = QPushButton("替换")
        rep_wd.setProperty("compact", True)
        rep_wd.clicked.connect(lambda: self._apply_wardrobe(replace=True))
        wb.addWidget(gen_wd)
        wb.addWidget(app_wd)
        wb.addWidget(rep_wd)
        wb.addStretch(1)
        wdl.addLayout(wb)
        wdl.addStretch(1)
        self._mode_stack.addWidget(t_wd)
        # The prompt editors absorb spare height on tall screens instead of leaving a blank gap.
        form.addWidget(self._mode_stack, 1)

        # Language warning + FlashNext translate
        self._lang_warn = QLabel("")
        self._lang_warn.setObjectName("BadgeWarn")
        self._lang_warn.setWordWrap(True)
        self._lang_warn.setVisible(False)
        form.addWidget(self._lang_warn)

        tr_row = QHBoxLayout()
        self._translate_btn = QPushButton("Flash Next 整理英文")
        self._translate_btn.setProperty("compact", True)
        self._bind_icon(self._translate_btn, "wand", 13)
        self._translate_btn.clicked.connect(self._translate_prompt)
        self._undo_tr_btn = QPushButton("撤销转换")
        self._undo_tr_btn.setProperty("ghost", True)
        self._undo_tr_btn.setProperty("compact", True)
        self._undo_tr_btn.setVisible(False)
        self._undo_tr_btn.clicked.connect(self._undo_translate)
        tr_row.addWidget(self._translate_btn)
        tr_row.addWidget(self._undo_tr_btn)
        tr_row.addStretch(1)
        form.addLayout(tr_row)

        # Model + params
        pgrid = QGridLayout()
        pgrid.setSpacing(7)
        self.model_combo = QComboBox()
        self.model_combo.setMinimumWidth(0)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self._dm_loaded_cp: str | None = None
        self._dm_running: bool = False
        self._dm_busy: bool = False
        self._inline_preload_btn = QPushButton("预加载")
        self._inline_preload_btn.setProperty("ghost", True)
        self._inline_preload_btn.setProperty("compact", True)
        self._bind_icon(self._inline_preload_btn, "play", 12)
        self._inline_preload_btn.setToolTip("预加载当前所选底模到显存")
        self._inline_preload_btn.clicked.connect(self._toggle_inline_preload)
        open_mdir = QPushButton("目录")
        open_mdir.setProperty("ghost", True)
        open_mdir.setProperty("compact", True)
        self._bind_icon(open_mdir, "folder", 13)
        open_mdir.clicked.connect(self._open_model_folder)
        mwrap = QHBoxLayout()
        mwrap.setSpacing(4)
        mwrap.addWidget(self.model_combo, 1)
        mwrap.addWidget(self._inline_preload_btn)
        mwrap.addWidget(open_mdir)

        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 16)
        self.count_spin.setValue(2)
        self.count_spin.setSuffix(" 张")
        self.count_spin.setMinimumWidth(92)
        self.count_spin.setMaximumWidth(104)
        self.count_spin.valueChanged.connect(self._on_input_changed)

        self.seed_edit = QLineEdit()
        self.seed_edit.setMinimumWidth(0)
        self.seed_edit.setPlaceholderText("随机（整数递增）")
        self.seed_edit.textChanged.connect(self._on_input_changed)

        self.task_name_edit = QLineEdit()
        self.task_name_edit.setMinimumWidth(0)
        self.task_name_edit.setMaxLength(80)
        self.task_name_edit.setPlaceholderText("任务名（可选）")

        pgrid.addWidget(QLabel("底模"), 0, 0)
        pgrid.addLayout(mwrap, 0, 1, 1, 3)
        pgrid.addWidget(QLabel("张数"), 1, 0)
        pgrid.addWidget(self.count_spin, 1, 1)
        pgrid.addWidget(QLabel("种子"), 1, 2)
        pgrid.addWidget(self.seed_edit, 1, 3)
        pgrid.addWidget(QLabel("队列名"), 2, 0)
        pgrid.addWidget(self.task_name_edit, 2, 1, 1, 3)
        pgrid.setColumnStretch(1, 0)
        pgrid.setColumnStretch(3, 1)
        form.addLayout(pgrid)

        self.size_widget = ImageSizeWidget(allow_storyboard=False)
        self.size_widget.changed.connect(self._on_input_changed)
        form.addWidget(self.size_widget)

        # DeltaMix v6 checkboxes
        self._delta_box = QWidget()
        dl = QVBoxLayout(self._delta_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(4)
        self.matte_chk = QCheckBox("柔和皮肤 / 去高光")
        self.matte_chk.setChecked(True)
        self.consistency_chk = QCheckBox("一致性 LoRA（Anima Edit v2）")
        self.consistency_chk.setChecked(False)
        dl.addWidget(self.matte_chk)
        dl.addWidget(self.consistency_chk)
        form.addWidget(self._delta_box)

        # Krea2 text encoder toggle
        self._krea_box = QWidget()
        kl = QVBoxLayout(self._krea_box)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(4)
        self.krea_ablit_chk = QCheckBox("Abliterated 文本编码器")
        self.krea_ablit_chk.setChecked(False)
        self.krea_ablit_chk.toggled.connect(self._on_krea_ablit_toggled)
        kl.addWidget(self.krea_ablit_chk)
        self._krea_box.setVisible(False)
        self._krea_ablit_manual = False
        form.addWidget(self._krea_box)

        # Resolved final preview toggle
        self._preview_toggle = QPushButton("最终提示词预览")
        self._preview_toggle.setProperty("ghost", True)
        self._preview_toggle.setProperty("compact", True)
        self._preview_toggle.setCheckable(True)
        self._bind_icon(self._preview_toggle, "chevron-right", 13)
        self._preview_toggle.toggled.connect(self._on_preview_toggled)
        self._final_preview = QPlainTextEdit()
        self._final_preview.setReadOnly(True)
        self._final_preview.setMaximumHeight(100)
        self._final_preview.setVisible(False)
        form.addWidget(self._preview_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        form.addWidget(self._final_preview)

        # Pinned submit area
        sub_box = QFrame()
        sub_box.setObjectName("PaneFooter")
        sl = QVBoxLayout(sub_box)
        sl.setContentsMargins(0, 10, 0, 0)
        sl.setSpacing(6)

        srow = QHBoxLayout()
        srow.setSpacing(8)
        self._start_btn = QPushButton("开始画图")
        self._start_btn.setProperty("primary", True)
        self._start_btn.setMinimumHeight(38)
        self._start_btn.clicked.connect(self._start_draw)
        self._queue_btn = QPushButton("加入队列")
        self._queue_btn.setMinimumHeight(38)
        self._queue_btn.clicked.connect(lambda: self._start_draw(queued=True))
        srow.addWidget(self._start_btn, 2)
        srow.addWidget(self._queue_btn, 1)
        sl.addLayout(srow)
        outer.addWidget(sub_box)
        return pane

    def _build_selection_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("Pane")
        pane.setMinimumWidth(280)
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setMinimumWidth(0)
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 6, 0)
        il.setSpacing(8)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)

        # Selected LoRAs section
        lh = QHBoxLayout()
        lt = QLabel("LoRA")
        lt.setObjectName("SectionTitle")
        open_ldir = QPushButton("目录")
        open_ldir.setProperty("ghost", True)
        open_ldir.setProperty("compact", True)
        self._bind_icon(open_ldir, "folder", 13)
        open_ldir.clicked.connect(self._open_lora_folder)
        lh.addWidget(lt)
        lh.addStretch(1)
        lh.addWidget(open_ldir)
        il.addLayout(lh)

        self._lora_empty = QLabel("未选 LoRA")
        self._lora_empty.setObjectName("Muted")
        il.addWidget(self._lora_empty)

        self._lora_selected_box = QVBoxLayout()
        self._lora_selected_box.setSpacing(6)
        il.addLayout(self._lora_selected_box)

        self._lora_bleed_warn = QLabel("")
        self._lora_bleed_warn.setObjectName("BadgeWarn")
        self._lora_bleed_warn.setWordWrap(True)
        self._lora_bleed_warn.setVisible(False)
        il.addWidget(self._lora_bleed_warn)

        self._lora_search = QLineEdit()
        self._lora_search.setMinimumWidth(0)
        self._lora_search.setPlaceholderText("筛选 LoRA…")
        self._lora_search.setClearButtonEnabled(True)
        self._lora_search.textChanged.connect(self._refresh_lora_search_list)
        il.addWidget(self._lora_search)

        self._lora_results = _GrowingList()
        self._lora_results.setUniformItemSizes(True)
        self._lora_results.setMinimumWidth(0)
        self._lora_results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lora_results.setMinimumHeight(140)
        self._lora_results.itemClicked.connect(self._on_pick_lora)
        attach_lora_star_delegate(self._lora_results, self._toggle_favorite_lora)
        il.addWidget(self._lora_results, 3)

        # Hairline divider
        sep = QFrame()
        sep.setObjectName("SidebarSep")
        sep.setFixedHeight(1)
        il.addSpacing(2)
        il.addWidget(sep)
        il.addSpacing(2)

        # Selected Characters section
        ch = QHBoxLayout()
        ct = QLabel("角色")
        ct.setObjectName("SectionTitle")
        manage_c = QPushButton("管理")
        manage_c.setProperty("ghost", True)
        manage_c.setProperty("compact", True)
        self._bind_icon(manage_c, "characters", 13)
        manage_c.clicked.connect(self.open_characters_requested.emit)
        ch.addWidget(ct)
        ch.addStretch(1)
        ch.addWidget(manage_c)
        il.addLayout(ch)

        self._char_empty = QLabel("未选角色")
        self._char_empty.setObjectName("Muted")
        il.addWidget(self._char_empty)

        self._char_selected_box = QVBoxLayout()
        self._char_selected_box.setSpacing(6)
        il.addLayout(self._char_selected_box)

        self._char_search = QLineEdit()
        self._char_search.setMinimumWidth(0)
        self._char_search.setPlaceholderText("筛选角色…")
        self._char_search.setClearButtonEnabled(True)
        self._char_search.textChanged.connect(self._refresh_char_search_list)
        il.addWidget(self._char_search)

        self._char_results = _GrowingList()
        self._char_results.setUniformItemSizes(True)
        self._char_results.setMinimumWidth(0)
        self._char_results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._char_results.setMinimumHeight(120)
        self._char_results.itemClicked.connect(self._on_pick_character)
        il.addWidget(self._char_results, 2)
        return pane

    def _build_second_pass_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("Pane")
        outer = QVBoxLayout(pane)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        back_btn = QPushButton("返回")
        back_btn.setProperty("ghost", True)
        back_btn.setProperty("compact", True)
        self._bind_icon(back_btn, "chevron-left", 14)
        back_btn.clicked.connect(self._exit_second_pass)
        title = QLabel("二次采样")
        title.setObjectName("SectionTitle")
        esc_lbl = QLabel("Esc")
        esc_lbl.setObjectName("Keycap")
        head.addWidget(back_btn)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(esc_lbl)
        outer.addLayout(head)

        # Presets Segmented Track
        preset_track = QFrame()
        preset_track.setObjectName("SegmentTrack")
        preset_row = QHBoxLayout(preset_track)
        preset_row.setContentsMargins(3, 3, 3, 3)
        preset_row.setSpacing(3)
        self._sp_preset_btns: dict[str, QPushButton] = {}
        for key, lbl in [("krea2", "Krea2 增质"), ("moss", "MOSS 精修"), ("anima29", "Anima 2.9B")]:
            b = QPushButton(lbl)
            b.setProperty("segment", True)
            b.clicked.connect(lambda _=False, k=key: self._apply_sp_preset(k))
            self._sp_preset_btns[key] = b
            preset_row.addWidget(b, 1)
        outer.addWidget(preset_track)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setMinimumWidth(0)
        sl = QVBoxLayout(inner)
        sl.setContentsMargins(0, 0, 6, 0)
        sl.setSpacing(9)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        grid = QGridLayout()
        grid.setSpacing(8)
        self._sp_model = QComboBox()
        self._sp_model.setMinimumWidth(0)
        self._sp_model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self._sp_scale = QComboBox()
        self._sp_scale.setMinimumWidth(0)
        for v, l in [(1.0, "1.0× 原尺寸"), (1.25, "1.25×"), (1.4, "1.4× 潜空间"), (1.5, "1.5×"), (2.0, "2.0×")]:
            self._sp_scale.addItem(l, v)
        self._sp_method = QComboBox()
        self._sp_method.setMinimumWidth(0)
        for v, l in [("latent", "潜空间重绘"), ("upscale", "保真超分"), ("upscale_only", "纯超分锐化")]:
            self._sp_method.addItem(l, v)
        self._sp_mode = QComboBox()
        self._sp_mode.setMinimumWidth(0)
        for v, l in [("auto", "自动分块"), ("full", "整图精修"), ("tiled", "分块高清")]:
            self._sp_mode.addItem(l, v)
        self._sp_sampler = QComboBox()
        self._sp_sampler.setMinimumWidth(0)
        for s in _SP_SAMPLERS:
            self._sp_sampler.addItem(s, s)
        self._sp_scheduler = QComboBox()
        self._sp_scheduler.setMinimumWidth(0)
        for s in _SP_SCHEDULERS:
            self._sp_scheduler.addItem(s, s)
        self._sp_denoise = DecimalSpinBox()
        self._sp_denoise.setRange(0.05, 1.0)
        self._sp_denoise.setSingleStep(0.05)
        self._sp_denoise.setValue(0.45)
        self._sp_steps = QSpinBox()
        self._sp_steps.setRange(1, 100)
        self._sp_steps.setValue(8)
        self._sp_seed = QLineEdit()
        self._sp_seed.setPlaceholderText("随机")
        self._sp_count = QSpinBox()
        self._sp_count.setRange(1, 8)
        self._sp_count.setValue(1)
        self._sp_count.setSuffix(" 张")
        self._sp_post_up = QComboBox()
        self._sp_post_up.setMinimumWidth(0)
        for v, l in [(1.0, "关闭"), (1.5, "1.5× 超分"), (2.0, "2.0× 超分")]:
            self._sp_post_up.addItem(l, v)
        self._sp_upscaler = QComboBox()
        self._sp_upscaler.setMinimumWidth(0)

        fields = [
            ("底模", self._sp_model, "缩放", self._sp_scale),
            ("方式", self._sp_method, "策略", self._sp_mode),
            ("采样器", self._sp_sampler, "调度器", self._sp_scheduler),
            ("重绘", self._sp_denoise, "步数", self._sp_steps),
            ("种子", self._sp_seed, "张数", self._sp_count),
            ("后置超分", self._sp_post_up, "超分模型", self._sp_upscaler),
        ]
        for r, (l1, w1, l2, w2) in enumerate(fields):
            grid.addWidget(QLabel(l1), r, 0)
            grid.addWidget(w1, r, 1)
            grid.addWidget(QLabel(l2), r, 2)
            grid.addWidget(w2, r, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        sl.addLayout(grid)

        self._sp_krea_ablit_chk = QCheckBox("Abliterated 文本编码器")
        self._sp_krea_ablit_chk.setChecked(False)
        self._sp_krea_ablit_chk.setVisible(False)
        self._sp_krea_ablit_chk.toggled.connect(self._on_sp_krea_ablit_toggled)
        self._sp_krea_ablit_manual = False
        sl.addWidget(self._sp_krea_ablit_chk)

        # Clean info/warning box with vector alert icon
        self._sp_size_box = QFrame()
        self._sp_size_box.setObjectName("InfoBox")
        sbl = QHBoxLayout(self._sp_size_box)
        sbl.setContentsMargins(10, 7, 10, 7)
        sbl.setSpacing(6)
        self._sp_warn_icon = QLabel()
        self._sp_warn_icon.setFixedSize(14, 14)
        self._sp_warn_icon.setVisible(False)
        self._sp_size_hint = QLabel("")
        self._sp_size_hint.setWordWrap(True)
        self._sp_size_hint.setStyleSheet('font-family: "Consolas", "Microsoft YaHei UI", monospace; font-size: 13px;')
        sbl.addWidget(self._sp_warn_icon, 0, Qt.AlignmentFlag.AlignTop)
        sbl.addWidget(self._sp_size_hint, 1)
        sl.addWidget(self._sp_size_box)

        # Second pass LoRAs
        self._sp_lora_section = QWidget()
        sll = QVBoxLayout(self._sp_lora_section)
        sll.setContentsMargins(0, 0, 0, 0)
        sll.setSpacing(6)
        sp_lt = QLabel("二采 LoRA")
        sp_lt.setObjectName("SectionTitle")
        sll.addWidget(sp_lt)
        self._sp_lora_box = QVBoxLayout()
        self._sp_lora_box.setSpacing(5)
        sll.addLayout(self._sp_lora_box)
        self._sp_lora_bleed_warn = QLabel("")
        self._sp_lora_bleed_warn.setObjectName("BadgeWarn")
        self._sp_lora_bleed_warn.setWordWrap(True)
        self._sp_lora_bleed_warn.setVisible(False)
        sll.addWidget(self._sp_lora_bleed_warn)
        self._sp_lora_search = QLineEdit()
        self._sp_lora_search.setPlaceholderText("筛选二采 LoRA…")
        self._sp_lora_search.setClearButtonEnabled(True)
        self._sp_lora_search.textChanged.connect(self._refresh_sp_lora_results)
        self._sp_lora_results = QListWidget()
        self._sp_lora_results.setUniformItemSizes(True)
        self._sp_lora_results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._sp_lora_results.setMaximumHeight(110)
        self._sp_lora_results.itemClicked.connect(self._on_pick_sp_lora)
        attach_lora_star_delegate(self._sp_lora_results, self._toggle_favorite_lora)
        sll.addWidget(self._sp_lora_search)
        sll.addWidget(self._sp_lora_results)
        sl.addWidget(self._sp_lora_section)

        # Cross-model prompt tweak
        self._sp_prompt_toggle = QPushButton("提示词微调")
        self._sp_prompt_toggle.setProperty("ghost", True)
        self._sp_prompt_toggle.setProperty("compact", True)
        self._sp_prompt_toggle.setCheckable(True)
        self._bind_icon(self._sp_prompt_toggle, "chevron-right", 13)
        self._sp_prompt_toggle.toggled.connect(self._on_sp_prompt_toggled)
        sl.addWidget(self._sp_prompt_toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self._sp_prompt_box = QWidget()
        self._sp_prompt_box.setVisible(False)
        spl = QVBoxLayout(self._sp_prompt_box)
        spl.setContentsMargins(0, 4, 0, 0)
        spl.setSpacing(6)
        self._sp_cross_warn = QLabel("")
        self._sp_cross_warn.setObjectName("BadgeWarn")
        self._sp_cross_warn.setWordWrap(True)
        self._sp_cross_warn.setVisible(False)
        spl.addWidget(self._sp_cross_warn)

        pr = QHBoxLayout()
        pr.addWidget(QLabel("正向提示词"))
        pr.addStretch(1)
        clr_p = QPushButton("清空")
        clr_p.setProperty("ghost", True)
        clr_p.setProperty("compact", True)
        clr_p.clicked.connect(lambda: self._sp_pos.clear())
        pr.addWidget(clr_p)
        spl.addLayout(pr)
        self._sp_pos = QPlainTextEdit()
        self._sp_pos.setMaximumHeight(88)
        spl.addWidget(self._sp_pos)

        nr = QHBoxLayout()
        nr.addWidget(QLabel("负面提示词"))
        nr.addStretch(1)
        clr_n = QPushButton("清空")
        clr_n.setProperty("ghost", True)
        clr_n.setProperty("compact", True)
        clr_n.clicked.connect(lambda: self._sp_neg.clear())
        nr.addWidget(clr_n)
        spl.addLayout(nr)
        self._sp_neg = QPlainTextEdit()
        self._sp_neg.setMaximumHeight(54)
        spl.addWidget(self._sp_neg)

        self._sp_final_preview_lbl = QLabel("二采最终提示词预览")
        self._sp_final_preview_lbl.setObjectName("Muted")
        spl.addWidget(self._sp_final_preview_lbl)
        self._sp_final_preview = QPlainTextEdit()
        self._sp_final_preview.setReadOnly(True)
        self._sp_final_preview.setMaximumHeight(74)
        spl.addWidget(self._sp_final_preview)
        sl.addWidget(self._sp_prompt_box)
        sl.addStretch(1)

        # Footer
        sp_foot = QFrame()
        sp_foot.setObjectName("PaneFooter")
        sfl = QVBoxLayout(sp_foot)
        sfl.setContentsMargins(0, 10, 0, 0)
        self._sp_start_btn = QPushButton("开始二次采样")
        self._sp_start_btn.setProperty("primary", True)
        self._sp_start_btn.setMinimumHeight(38)
        self._sp_start_btn.clicked.connect(self._run_second_pass)
        sfl.addWidget(self._sp_start_btn)
        outer.addWidget(sp_foot)

        # Signals
        self._sp_model.currentIndexChanged.connect(self._on_sp_model_changed)
        self._sp_method.currentIndexChanged.connect(self._on_sp_method_changed)
        self._sp_scale.currentIndexChanged.connect(self._on_sp_scale_changed)
        for w in (self._sp_mode, self._sp_post_up, self._sp_upscaler):
            w.currentIndexChanged.connect(self._on_sp_param_changed)
        for w in (self._sp_sampler, self._sp_scheduler):
            w.currentIndexChanged.connect(self._clear_sp_preset_active)
        self._sp_denoise.valueChanged.connect(self._on_sp_denoise_changed)
        self._sp_steps.valueChanged.connect(self._clear_sp_preset_active)
        self._sp_pos.textChanged.connect(self._update_sp_hint)
        self._sp_pos.textChanged.connect(self._update_sp_final_preview)
        return pane

    def _build_gallery_pane(self) -> QWidget:
        pane = QFrame()
        pane.setObjectName("GalleryPane")
        lay = QVBoxLayout(pane)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        # Top actions (quiet ghost toolbar)
        top = QHBoxLayout()
        top.setSpacing(4)
        self._copy_one_btn = QPushButton("复制")
        self._copy_one_btn.setProperty("ghost", True)
        self._copy_one_btn.setProperty("compact", True)
        self._bind_icon(self._copy_one_btn, "copy", 13)
        self._copy_one_btn.clicked.connect(self._copy_current_image)
        self._copy_all_btn = QPushButton("复制全部")
        self._copy_all_btn.setProperty("ghost", True)
        self._copy_all_btn.setProperty("compact", True)
        self._bind_icon(self._copy_all_btn, "copy", 13)
        self._copy_all_btn.clicked.connect(self._copy_all_images)
        self._save_as_btn = QPushButton("保存")
        self._save_as_btn.setProperty("ghost", True)
        self._save_as_btn.setProperty("compact", True)
        self._bind_icon(self._save_as_btn, "download", 13)
        self._save_as_btn.clicked.connect(self._save_current_image)
        self._clear_imgs_btn = QPushButton("清空")
        self._clear_imgs_btn.setProperty("danger", True)
        self._clear_imgs_btn.setProperty("compact", True)
        self._bind_icon(self._clear_imgs_btn, "trash", 13)
        self._clear_imgs_btn.clicked.connect(self._clear_images)
        self._board_layout_btn = QPushButton("整图排版")
        self._board_layout_btn.setProperty("ghost", True)
        self._board_layout_btn.setProperty("compact", True)
        self._bind_icon(self._board_layout_btn, "layout", 13)
        self._board_layout_btn.setVisible(False)
        self._board_layout_btn.clicked.connect(lambda: self.open_board_layout_requested.emit(self._board_pid or ""))

        top.addWidget(self._copy_one_btn)
        top.addWidget(self._copy_all_btn)
        top.addWidget(self._save_as_btn)
        top.addStretch(1)
        top.addWidget(self._board_layout_btn)
        top.addWidget(self._clear_imgs_btn)
        lay.addLayout(top)

        # Main viewer stack: 0 = empty state, 1 = dark viewing mat + zoomable viewer
        self._gal_stack = QStackedWidget()
        empty = QFrame()
        empty.setObjectName("GalleryEmpty")
        el = QVBoxLayout(empty)
        el.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.setSpacing(10)
        self._empty_art_icon = QLabel()
        self._empty_art_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        et = QLabel("等待出图")
        et.setObjectName("Muted")
        et.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self._empty_art_icon)
        el.addWidget(et)
        self._gal_stack.addWidget(empty)

        stage = QFrame()
        stage.setObjectName("GalleryStage")
        stage_lay = QVBoxLayout(stage)
        stage_lay.setContentsMargins(6, 6, 6, 6)
        self._viewer = ZoomableImageView(self, defer_single_click=True)
        self._viewer.clicked.connect(self._open_lightbox)
        self._viewer.double_clicked.connect(self._on_viewer_double_clicked)
        self._viewer.step_requested.connect(self._step_gallery)
        stage_lay.addWidget(self._viewer, 1)
        self._gal_stack.addWidget(stage)
        lay.addWidget(self._gal_stack, 1)

        # Status & Apply/Adopt bar
        self._meta_bar = QHBoxLayout()
        self._meta_bar.setSpacing(6)
        self._img_badge = QLabel("")
        self._img_badge.setObjectName("BadgeSuccess")
        self._img_badge.setVisible(False)
        self._img_info = QLabel("")
        self._img_info.setObjectName("Muted")
        self._img_info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._img_info.setMinimumWidth(0)
        self._img_info.setStyleSheet('font-family: "Consolas", "Microsoft YaHei UI", monospace; font-size: 12px;')
        self._apply_sp_btn = QPushButton("应用此图")
        self._apply_sp_btn.setProperty("compact", True)
        self._apply_sp_btn.setProperty("primary", True)
        self._apply_sp_btn.setVisible(False)
        self._apply_sp_btn.clicked.connect(self._apply_active_sp)
        self._unapply_sp_btn = QPushButton("恢复原图")
        self._unapply_sp_btn.setProperty("ghost", True)
        self._unapply_sp_btn.setProperty("compact", True)
        self._unapply_sp_btn.setVisible(False)
        self._unapply_sp_btn.clicked.connect(self._unapply_active_sp)
        self._adopt_btn = QPushButton("采用到此格")
        self._adopt_btn.setProperty("compact", True)
        self._adopt_btn.setProperty("primary", True)
        self._adopt_btn.setVisible(False)
        self._adopt_btn.clicked.connect(self._adopt_board_image)
        self._unadopt_btn = QPushButton("取消采用")
        self._unadopt_btn.setProperty("ghost", True)
        self._unadopt_btn.setProperty("compact", True)
        self._unadopt_btn.setVisible(False)
        self._unadopt_btn.clicked.connect(self._unadopt_board_image)
        self._meta_bar.addWidget(self._img_badge)
        self._meta_bar.addWidget(self._img_info, 1)
        self._meta_bar.addWidget(self._apply_sp_btn)
        self._meta_bar.addWidget(self._unapply_sp_btn)
        self._meta_bar.addWidget(self._adopt_btn)
        self._meta_bar.addWidget(self._unadopt_btn)
        lay.addLayout(self._meta_bar)

        # Thumbnail strip
        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setFixedHeight(96)
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._thumb_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._thumb_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._thumb_host = QWidget()
        self._thumb_row = QHBoxLayout(self._thumb_host)
        self._thumb_row.setContentsMargins(2, 2, 2, 2)
        self._thumb_row.setSpacing(6)
        self._thumb_row.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._thumb_scroll.setWidget(self._thumb_host)
        self._thumb_scroll.setVisible(False)
        lay.addWidget(self._thumb_scroll)

        # Footer pager + Second Pass CTA
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self._prev_img_btn = QPushButton()
        self._prev_img_btn.setToolTip("上一张")
        self._prev_img_btn.setProperty("ghost", True)
        self._prev_img_btn.setProperty("compact", True)
        self._prev_img_btn.setFixedSize(28, 28)
        self._bind_icon(self._prev_img_btn, "chevron-left", 15)
        self._prev_img_btn.clicked.connect(lambda: self._step_gallery(-1))
        self._pager_lbl = QLabel("")
        self._pager_lbl.setObjectName("Muted")
        self._pager_lbl.setStyleSheet('font-family: "Consolas", monospace; font-size: 13px;')
        self._next_img_btn = QPushButton()
        self._next_img_btn.setToolTip("下一张")
        self._next_img_btn.setProperty("ghost", True)
        self._next_img_btn.setProperty("compact", True)
        self._next_img_btn.setFixedSize(28, 28)
        self._bind_icon(self._next_img_btn, "chevron-right", 15)
        self._next_img_btn.clicked.connect(lambda: self._step_gallery(1))

        self._delete_img_btn = QPushButton("删除当前图片")
        self._delete_img_btn.setToolTip("删除当前图片（移入缓存，按 Ctrl+Z 撤销，最多 15 步）")
        self._delete_img_btn.setProperty("danger", True)
        self._delete_img_btn.setProperty("compact", True)
        self._bind_icon(self._delete_img_btn, "trash", 13)
        self._delete_img_btn.setVisible(False)
        self._delete_img_btn.clicked.connect(self._delete_current_image)

        self._prev_panel_btn = QPushButton("上一格")
        self._prev_panel_btn.setProperty("ghost", True)
        self._prev_panel_btn.setProperty("compact", True)
        self._prev_panel_btn.setVisible(False)
        self._prev_panel_btn.clicked.connect(lambda: self._step_board_panel(-1))
        self._next_panel_btn = QPushButton("下一格")
        self._next_panel_btn.setProperty("ghost", True)
        self._next_panel_btn.setProperty("compact", True)
        self._next_panel_btn.setVisible(False)
        self._next_panel_btn.clicked.connect(lambda: self._step_board_panel(1))

        self._open_sp_btn = QPushButton("二采精修")
        self._open_sp_btn.setProperty("primary", True)
        self._open_sp_btn.setVisible(False)
        self._open_sp_btn.clicked.connect(self._enter_second_pass)

        foot.addWidget(self._prev_img_btn)
        foot.addWidget(self._pager_lbl)
        foot.addWidget(self._next_img_btn)
        foot.addWidget(self._delete_img_btn)
        foot.addStretch(1)
        foot.addWidget(self._prev_panel_btn)
        foot.addWidget(self._next_panel_btn)
        foot.addWidget(self._open_sp_btn)
        lay.addLayout(foot)
        return pane

    def _set_mode_tab(self, idx: int) -> None:
        changed = self._mode_stack.currentIndex() != idx
        self._mode_stack.setCurrentIndex(idx)
        for i, b in enumerate(self._mode_seg_btns):
            b.setProperty("active", i == idx)
            b.style().unpolish(b)
            b.style().polish(b)
        self._on_mode_tab_changed(idx)
        if changed:
            fade_widget_in(self._mode_stack.currentWidget(), duration=150)

    def _on_preview_toggled(self, shown: bool) -> None:
        self._final_preview.setVisible(shown)
        self._preview_toggle.setIcon(studio_icon("chevron-down" if shown else "chevron-right", tokens()["muted"], 13))
        if shown:
            fade_widget_in(self._final_preview, duration=150)

    def _on_sp_prompt_toggled(self, shown: bool) -> None:
        self._sp_prompt_box.setVisible(shown)
        self._sp_prompt_toggle.setIcon(studio_icon("chevron-down" if shown else "chevron-right", tokens()["muted"], 13))
        if shown:
            fade_widget_in(self._sp_prompt_box, duration=150)

    # ==================== DATA LOADING ====================

    def load_session(self, pid: str | None = None, *, board: bool = False) -> None:
        self._exit_second_pass(silent=True)
        self._board_pid = pid if board else None
        self._session_pid = pid
        self._active_gallery_idx = 0
        self._known_base_ids = set()
        self._board_selections = {}
        self._thumb_sig = None
        self._board_bar.setVisible(board)
        self._board_layout_btn.setVisible(board)
        self._clear_imgs_btn.setVisible(not board)
        self._prev_panel_btn.setVisible(board)
        self._next_panel_btn.setVisible(board)

        self._loading_ui = True
        try:
            self._drawing_opts = self.app.drawing_options()
            char_payload = self.app.characters_data(compact=True)
            self._characters_all = char_payload.get("characters") or []
            self._char_by_id = {c["id"]: c for c in self._characters_all}

            # Populate models
            self.model_combo.blockSignals(True)
            self.model_combo.clear()
            diff = self._drawing_opts.get("diffusion_models") or {}
            ckpts = (self._drawing_opts.get("comfy") or {}).get("checkpoints") or []
            for name in ckpts:
                lbl = (diff.get(name) or {}).get("label") or name
                self.model_combo.addItem(lbl, name)
            self.model_combo.blockSignals(False)

            # Populate wardrobe
            self._load_wardrobe_catalog()

            # Load board panels if board mode
            self._project_snap = self.app.snapshot(pid) if pid else None
            if board and self._project_snap:
                proj = self._project_snap["project"]
                self._board_panel.blockSignals(True)
                self._board_panel.clear()
                for page in sorted(proj.get("pages", {}).values(), key=lambda x: x["number"]):
                    for panel in page.get("panels", []):
                        self._board_panel.addItem(
                            f"{panel['id']} · {panel.get('aspect_ratio', '3:2')}",
                            panel,
                        )
                self._board_panel.blockSignals(False)
                cur_panel = self._board_panel.currentData()
                if isinstance(cur_panel, dict):
                    self._board_hint.setText(cur_panel.get("drawing_description") or cur_panel.get("main_instant") or "")

            # Restore saved draft or project prompt_input
            draft_key = pid if (pid and not pid.startswith("tmp-") and pid != PROMPT_WORKSPACE_ID) else "new"
            saved = self.app.ui_state.prompt_draft(draft_key)
            if not saved and self._project_snap and self._project_snap["project"].get("quick_sample"):
                saved = self._project_snap["project"].get("prompt_input") or {}

            self.positive.setPlainText(str(saved.get("text") or ""))
            self.negative.setPlainText(str(saved.get("negative") or ""))
            preloaded = self._drawing_opts.get("preloaded_checkpoint")
            preferred = self._drawing_opts.get("preferred_checkpoint")
            default_cp = preloaded or saved.get("checkpoint") or preferred or (self._drawing_opts.get("comfy") or {}).get("configured")
            idx = self.model_combo.findData(default_cp)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            self.count_spin.setValue(int(saved.get("count") or 2))
            self.seed_edit.setText(str(saved.get("seed") or ""))
            self.size_widget.apply(saved)
            self.matte_chk.setChecked(bool(saved.get("delta_matte_skin", True)))
            self.consistency_chk.setChecked(bool(saved.get("consistency_lora", False)))
            if "krea2_abliterated_encoder" in saved:
                self._krea_ablit_manual = True
                self.krea_ablit_chk.blockSignals(True)
                self.krea_ablit_chk.setChecked(bool(saved["krea2_abliterated_encoder"]))
                self.krea_ablit_chk.blockSignals(False)
            else:
                self._krea_ablit_manual = False
                self.krea_ablit_chk.blockSignals(True)
                self.krea_ablit_chk.setChecked(False)
                self.krea_ablit_chk.blockSignals(False)

            # Restore selected characters & LoRAs
            self._clear_selected_chars()
            for c in saved.get("characters") or []:
                card = next((x for x in self._characters_all if x["name"] == c.get("name")), None)
                if card:
                    self._add_selected_character(card, prompt_text=c.get("prompt"), auto_lora=False)

            self._clear_selected_loras()
            available_loras = {
                str(x).replace("\\", "/")
                for x in ((self._drawing_opts.get("comfy") or {}).get("loras") or [])
            }
            for l in saved.get("loras") or []:
                f = str(l.get("file") or "").replace("\\", "/")
                if f in available_loras:
                    self._add_selected_lora(f, weight=l.get("weight"), insert_trigger=False)
        finally:
            self._loading_ui = False

        self._on_model_changed()
        self._refresh_char_search_list()
        self.refresh_gallery()

    def _load_wardrobe_catalog(self) -> None:
        if self._wd_style.count() > 0:
            return
        try:
            cat = self.app.wardrobe_catalog()
            self._wd_chars_all = cat.get("characters") or []
            self._wd_style.clear()
            for s in cat.get("styles") or ["不限"]:
                self._wd_style.addItem(s, s)
            self._wd_level.clear()
            for k, lbl in (cat.get("levels") or {"sfw": "常规"}).items():
                self._wd_level.addItem(lbl, k)
            self._filter_wardrobe_chars()
        except Exception:
            self._wd_chars_all = []

    def _filter_wardrobe_chars(self) -> None:
        q = self._wd_char_search.text().strip().lower()
        self._wd_char.clear()
        self._wd_char.addItem("不限角色（随机）", "")
        shown = 0
        for row in getattr(self, "_wd_chars_all", []):
            hay = f"{row.get('zh','')} {row.get('name','')} {row.get('work','')} {row.get('tag','')}".lower()
            if not q or q in hay:
                lbl = f"{row.get('zh') or row.get('name')} · {row.get('work','')}"
                self._wd_char.addItem(lbl, row["tag"])
                shown += 1
                if shown >= 80:
                    break

    # ==================== MODEL / LORA / CHAR INTERACTIONS ====================

    def _current_checkpoint(self) -> str:
        return str(self.model_combo.currentData() or "")

    def _krea_abliterated_available(self) -> bool:
        comfy = self._drawing_opts.get("comfy") or {}
        if "krea2_abliterated_available" in comfy:
            return bool(comfy["krea2_abliterated_available"])
        from ..prompt_models import krea2_abliterated_available

        return krea2_abliterated_available()

    def _on_krea_ablit_toggled(self, _checked: bool) -> None:
        if not self._loading_ui:
            self._krea_ablit_manual = True
            self._on_input_changed()

    def _on_model_changed(self) -> None:
        cp = self._current_checkpoint()
        if not self._loading_ui and cp:
            self.app.remember_checkpoint(cp)
        diff = self._drawing_opts.get("diffusion_models") or {}
        fam = (diff.get(cp) or {}).get("family", "")
        self.size_widget.set_alignment(16 if fam == "moss_image" else 8)
        is_delta = cp == "animaDeltaMix38BQwen0_deltaMixV60.safetensors"
        self._delta_box.setVisible(is_delta)
        show_krea_ablit = fam == "krea2" and self._krea_abliterated_available()
        self._krea_box.setVisible(show_krea_ablit)
        if show_krea_ablit and not self._krea_ablit_manual:
            self.krea_ablit_chk.blockSignals(True)
            self.krea_ablit_chk.setChecked(False)
            self.krea_ablit_chk.blockSignals(False)
        self._update_inline_preload_btn()
        self._neg_title.setText("负面提示词")

        # Prune incompatible selected LoRAs when switching architecture
        target_fam, target_dir = checkpoint_family_and_dir(cp, diff)
        insp = self._drawing_opts.get("lora_inspection") or {}
        for f in list(self._selected_loras):
            info = insp.get(f) or {}
            if not f.startswith(target_dir + "/") or info.get("family") != target_fam:
                self._remove_selected_lora(f)

        self._refresh_lora_search_list()
        self._on_input_changed()

    def _family_loras_for(self, cp: str) -> list[str]:
        diff = self._drawing_opts.get("diffusion_models") or {}
        target_fam, target_dir = checkpoint_family_and_dir(cp, diff)
        insp = self._drawing_opts.get("lora_inspection") or {}
        all_loras = (self._drawing_opts.get("comfy") or {}).get("loras") or []
        return [
            f
            for f in all_loras
            if f.startswith(target_dir + "/") and (insp.get(f) or {}).get("family") == target_fam
        ]

    def _lora_label_map_for(self, cp: str) -> dict[str, str]:
        return lora_disambiguated_names(self._family_loras_for(cp))

    def _toggle_favorite_lora(self, file: str) -> None:
        norm = str(file or "").replace("\\", "/")
        if not norm:
            return
        is_fav = self.app.ui_state.toggle_favorite_lora(norm)
        row = self._selected_loras.get(norm)
        if row is not None:
            row.set_favorite(is_fav)
        self._refresh_lora_search_list()
        if self._sp_state is not None:
            self._render_sp_loras()
            self._refresh_sp_lora_results()

    def _refresh_lora_search_list(self) -> None:
        cp = self._current_checkpoint()
        insp = self._drawing_opts.get("lora_inspection") or {}
        q = self._lora_search.text().strip()
        family_loras = self._family_loras_for(cp)
        label_map = lora_disambiguated_names(family_loras)
        favs = self.app.ui_state.favorite_loras()
        fav_set = set(favs)

        matches = [
            f
            for f in family_loras
            if f not in self._selected_loras and lora_matches_search(f, insp.get(f) or {}, q)
        ]
        matches = sort_loras_favorites_first(matches, favs)

        plus_ic = studio_icon("plus", tokens()["muted"], 13)
        self._lora_results.setUpdatesEnabled(False)
        try:
            self._lora_results.clear()
            for f in matches:
                it = QListWidgetItem(plus_ic, label_map.get(f, lora_choice_name(f)))
                it.setData(Qt.ItemDataRole.UserRole, f)
                it.setData(ROLE_LORA_FAV, f in fav_set)
                it.setToolTip(f)
                self._lora_results.addItem(it)

            if not matches:
                it = QListWidgetItem("无匹配 LoRA")
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self._lora_results.addItem(it)
        finally:
            self._lora_results.setUpdatesEnabled(True)

    def _on_pick_lora(self, item: QListWidgetItem) -> None:
        file = item.data(Qt.ItemDataRole.UserRole)
        if not file:
            return
        if len(self._selected_loras) >= 5:
            self.notify_requested.emit("最多选择 5 个 LoRA", True)
            return
        self._add_selected_lora(file)
        self._lora_search.clear()
        self._refresh_lora_search_list()
        self._on_input_changed()

    def _resolve_lora_trigger(self, file: str) -> str:
        norm = str(file or "").replace("\\", "/")
        row = self._selected_loras.get(norm)
        if row is not None:
            return row.trigger_edit.text().strip()
        labels = self._drawing_opts.setdefault("lora_labels", {})
        trig = resolve_lora_trigger(norm, labels)
        if trig:
            entry = dict(labels.get(norm) or {})
            if not str(entry.get("trigger_words") or "").strip():
                entry["trigger_words"] = trig
                labels[norm] = entry
        return trig

    def _selected_lora_triggers(self) -> list[str]:
        out: list[str] = []
        for row in self._selected_loras.values():
            trig = row.trigger_edit.text().strip()
            if trig:
                out.append(trig)
        return out

    def _sync_json_view_if_active(self) -> None:
        if self._mode_stack.currentIndex() == 1:
            self._on_mode_tab_changed(1)

    def _add_selected_lora(self, file: str, weight: float | None = None, *, insert_trigger: bool = False) -> None:
        del insert_trigger  # Triggers stay out of the editable prompt and inject silently at render time.
        file = str(file or "").replace("\\", "/")
        if file in self._selected_loras:
            return
        labels = self._drawing_opts.setdefault("lora_labels", {})
        info = dict(labels.get(file) or {})
        resolved_trig = resolve_lora_trigger(file, labels)
        if resolved_trig and not str(info.get("trigger_words") or "").strip():
            info["trigger_words"] = resolved_trig
            labels[file] = dict(info)
        remembered = self.app.ui_state.lora_weights().get(file)
        w = weight if weight is not None else (remembered if remembered is not None else info.get("weight", 0.7))
        family_loras = self._family_loras_for(self._current_checkpoint())
        same_base = sum(1 for x in family_loras if lora_choice_name(x) == lora_choice_name(file)) > 1
        row = _SelectedLoraRow(
            file,
            float(w),
            info,
            self._characters_all,
            self._save_lora_label,
            favorite=self.app.ui_state.is_lora_favorite(file),
            detail_tag=lora_detail_tag(file) if same_base else "",
        )
        row.changed.connect(self._on_lora_row_changed)
        row.removed.connect(self._remove_selected_lora)
        row.favorite_toggled.connect(self._toggle_favorite_lora)
        row.insert_trigger_requested.connect(lambda f: self._insert_lora_trigger(f, silent=False))
        row.trigger_edited.connect(self._on_lora_trigger_edited)
        row.trigger_text_changed.connect(self._on_input_changed)
        self._selected_loras[file] = row
        self._lora_selected_box.addWidget(row)
        self._lora_empty.setVisible(False)
        if not self._loading_ui:
            self._on_input_changed()

    def _remove_selected_lora(self, file: str) -> None:
        row = self._selected_loras.pop(file, None)
        if row is not None:
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._auto_inserted_triggers.pop(file, None)
        self._lora_empty.setVisible(not self._selected_loras)
        self._refresh_lora_search_list()
        self._on_input_changed()

    def _clear_selected_loras(self) -> None:
        for f in list(self._selected_loras):
            row = self._selected_loras.pop(f)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._auto_inserted_triggers.clear()
        self._lora_empty.setVisible(True)

    def _on_lora_trigger_edited(self, _file: str, _old_trig: str, _new_trig: str) -> None:
        self._on_input_changed()

    def _on_lora_row_changed(self) -> None:
        for f, row in self._selected_loras.items():
            self.app.ui_state.remember_lora_weight(f, row.weight.value())
        self._on_input_changed()

    def _save_lora_label(self, file: str, info: dict, weight: float) -> None:
        try:
            self.app.action({
                "action": "save_lora_labels",
                "items": [{
                    "file": file,
                    "name": lora_display_name(file),
                    "weight": float(weight),
                    "character_id": info.get("character_id", ""),
                    "trigger_words": info.get("trigger_words", ""),
                    "checkpoint": self._current_checkpoint(),
                    "confirmed": True,
                }],
            })
            self._drawing_opts.setdefault("lora_labels", {})[file] = dict(info)
        except Exception as exc:
            self.notify_requested.emit(f"保存 LoRA 标注失败：{exc}", True)

    def _insert_lora_trigger(self, file: str, *, silent: bool = False) -> None:
        words = self._resolve_lora_trigger(file)
        if not words:
            if not silent:
                self.notify_requested.emit("请先填写触发词", True)
            return
        updated, inserted = insert_trigger_phrase(self.positive.toPlainText(), words)
        if inserted:
            self.positive.setPlainText(updated)
            self._sync_json_view_if_active()
            if not silent:
                self.notify_requested.emit("已手动插入到正向提示词正文", False)
        elif not silent:
            self.notify_requested.emit("正向提示词正文中已包含该触发词", False)

    def _refresh_char_search_list(self) -> None:
        q = self._char_search.text().strip()
        matches = [c for c in self._characters_all if c["id"] not in self._selected_chars and character_matches_search(c, q)]
        plus_ic = studio_icon("plus", tokens()["muted"], 13)
        self._char_results.setUpdatesEnabled(False)
        try:
            self._char_results.clear()
            for c in matches:
                it = QListWidgetItem(plus_ic, c["name"])
                it.setData(Qt.ItemDataRole.UserRole, c["id"])
                self._char_results.addItem(it)
            if not matches:
                it = QListWidgetItem("无匹配角色")
                it.setFlags(Qt.ItemFlag.NoItemFlags)
                self._char_results.addItem(it)
        finally:
            self._char_results.setUpdatesEnabled(True)

    def _on_pick_character(self, item: QListWidgetItem) -> None:
        cid = item.data(Qt.ItemDataRole.UserRole)
        card = self._char_by_id.get(cid)
        if not card:
            return
        self._add_selected_character(card, auto_lora=True)
        self._char_search.clear()
        self._refresh_char_search_list()
        self._on_input_changed()

    def _add_selected_character(self, card: dict, prompt_text: str | None = None, *, auto_lora: bool = True) -> None:
        cid = card["id"]
        if cid in self._selected_chars:
            return
        img_path = None
        if card.get("image_id"):
            try:
                img_path = self.app.character_image_path(card["image_id"])
            except Exception:
                img_path = None
        row = _SelectedCharacterRow(card, img_path, prompt_text)
        row.changed.connect(self._on_input_changed)
        row.removed.connect(self._remove_selected_character)
        self._selected_chars[cid] = row
        self._char_selected_box.addWidget(row)
        self._char_empty.setVisible(False)

        if auto_lora:
            # Auto-select preferred compatible LoRA for this character
            cp = self._current_checkpoint()
            diff = self._drawing_opts.get("diffusion_models") or {}
            target_fam, target_dir = checkpoint_family_and_dir(cp, diff)
            insp = self._drawing_opts.get("lora_inspection") or {}
            labels = self._drawing_opts.get("lora_labels") or {}
            owned = [
                f for f, lbl in labels.items()
                if lbl.get("character_id") == cid
                and f.startswith(target_dir + "/")
                and (insp.get(f) or {}).get("family") == target_fam
            ]
            if owned and len(self._selected_loras) < 5:
                pref = next((f for f in owned if labels[f].get("preferred")), owned[0])
                self._add_selected_lora(pref)
                self._refresh_lora_search_list()

    def _remove_selected_character(self, cid: str) -> None:
        row = self._selected_chars.pop(cid, None)
        if row is not None:
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._char_empty.setVisible(not self._selected_chars)
        self._refresh_char_search_list()
        self._on_input_changed()

    def _clear_selected_chars(self) -> None:
        for cid in list(self._selected_chars):
            row = self._selected_chars.pop(cid)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._char_empty.setVisible(True)

    # ==================== PROMPT EDITING & SUBMISSION ====================

    def _on_mode_tab_changed(self, idx: int) -> None:
        if idx == 1:
            panel_id = self._current_board_panel_id() or "P001-K01"
            obj = [{
                "id": panel_id,
                "visual_prompt": self.positive.toPlainText(),
                "negative": self.negative.toPlainText(),
            }]
            self._json_edit.setPlainText(json.dumps(obj, ensure_ascii=False, indent=2))
            self._json_err.setText("")

    def _apply_json_to_fields(self) -> bool:
        try:
            data = json.loads(self._json_edit.toPlainText())
            row = data[0] if isinstance(data, list) and data else data
            if not isinstance(row, dict):
                raise ValueError("JSON 需为对象或单格数组")
            self.positive.setPlainText(str(row.get("visual_prompt") or row.get("positive") or ""))
            self.negative.setPlainText(str(row.get("negative") or ""))
            self._json_err.setText("已同步")
            self._set_mode_tab(0)
            return True
        except Exception as exc:
            self._json_err.setText(f"JSON 格式错误：{exc}")
            return False

    def _run_wardrobe_preview(self) -> None:
        seed_txt = self._wd_seed.text().strip()
        seed = int(seed_txt) if seed_txt.isdigit() else None
        try:
            res = self.app.action({
                "action": "wardrobe_preview",
                "scope": self._wd_scope.currentData() or "character",
                "style": self._wd_style.currentData() or "不限",
                "level": self._wd_level.currentData() or "sfw",
                "character": self._wd_char.currentData() or "",
                "seed": seed,
            })
            self._wd_preview.setPlainText(res.get("text", ""))
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _apply_wardrobe(self, *, replace: bool) -> None:
        txt = self._wd_preview.toPlainText().strip()
        if not txt:
            return
        if replace or not self.positive.toPlainText().strip():
            self.positive.setPlainText(txt)
        else:
            self.positive.setPlainText(self.positive.toPlainText().rstrip() + ", " + txt)
        self._set_mode_tab(0)

    def _paste_positive(self) -> None:
        from PySide6.QtGui import QGuiApplication

        txt = QGuiApplication.clipboard().text()
        if not txt:
            return
        # Auto-detect single-panel JSON paste
        stripped = txt.strip()
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
                row = parsed[0] if isinstance(parsed, list) and parsed else parsed
                if isinstance(row, dict) and ("visual_prompt" in row or "positive" in row):
                    self.positive.setPlainText(str(row.get("visual_prompt") or row.get("positive") or ""))
                    if "negative" in row:
                        self.negative.setPlainText(str(row.get("negative") or ""))
                    if self._board_pid and row.get("id"):
                        for i in range(self._board_panel.count()):
                            p = self._board_panel.itemData(i)
                            if p and p.get("id") == row["id"]:
                                self._board_panel.setCurrentIndex(i)
                                break
                    return
            except Exception:
                pass
        self.positive.setPlainText(txt)

    def _copy_text(self, text: str) -> None:
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText(text)
        self.notify_requested.emit("已复制到剪贴板", False)

    def _on_input_changed(self) -> None:
        if self._loading_ui:
            return
        pos = self.positive.toPlainText()
        sels = [
            {"name": r.card["name"], "revision": r.card["revision"], "prompt": r.prompt_edit.toPlainText()}
            for r in self._selected_chars.values()
        ]
        cards = [r.card for r in self._selected_chars.values()]
        resolved = resolve_selections(pos, sels, cards) if sels else []
        combined = "\n\n".join([pos] + [x["prompt"] for x in resolved if x["prompt"].strip()])
        trigs = self._selected_lora_triggers()
        final_text = apply_lora_triggers(combined, trigs)
        self._final_preview.setPlainText(final_text)

        unique_trigs: list[str] = []
        seen_trigs: set[str] = set()
        for t in trigs:
            low = t.lower()
            if low not in seen_trigs:
                seen_trigs.add(low)
                unique_trigs.append(t)
        auto_trigs = [t for t in unique_trigs if not trigger_present_in_prompt(combined, t)]
        if auto_trigs:
            self._preview_toggle.setText(f"最终提示词预览（后台附带 {len(auto_trigs)} 个触发词）")
        else:
            self._preview_toggle.setText("最终提示词预览")
        if len(auto_trigs) >= 2:
            self._lora_bleed_warn.setText(
                f"已选 {len(auto_trigs)} 个带不同触发词的 LoRA，叠加出图易串特征；建议调低权重（0.6-0.75），多人图可点「编辑-插入正文」分句定位"
            )
            self._lora_bleed_warn.setVisible(True)
        else:
            self._lora_bleed_warn.setVisible(False)

        contaminated = [r.card["name"] for r in self._selected_chars.values() if _CHAT_CARD_RE.search(r.prompt_edit.toPlainText())]
        if contaminated:
            self._lang_warn.setText(f"{'、'.join(contaminated)} 外观含对话设定，请先整理为英文。")
            self._lang_warn.setVisible(True)
        else:
            self._lang_warn.setVisible(False)

        self._draft_timer.start()

    def _save_draft(self) -> None:
        key = (
            self._session_pid
            if (self._session_pid and not self._session_pid.startswith("tmp-") and self._session_pid != PROMPT_WORKSPACE_ID)
            else "new"
        )
        draft = {
            "text": self.positive.toPlainText(),
            "negative": self.negative.toPlainText(),
            "checkpoint": self._current_checkpoint(),
            "count": self.count_spin.value(),
            "seed": self.seed_edit.text().strip(),
            **self.size_widget.read(),
            "delta_matte_skin": self.matte_chk.isChecked(),
            "consistency_lora": self.consistency_chk.isChecked(),
            "krea2_abliterated_encoder": self.krea_ablit_chk.isChecked(),
            "characters": [
                {"name": r.card["name"], "prompt": r.prompt_edit.toPlainText()}
                for r in self._selected_chars.values()
            ],
            "loras": [
                {"file": f, "weight": row.weight.value()}
                for f, row in self._selected_loras.items()
            ],
        }
        self.app.ui_state.save_prompt_draft(key, draft)
        self._draft_lbl.setText("已保存 · " + datetime.now().strftime("%H:%M"))

    def _clear_all(self) -> None:
        self._cleared_backup = {
            "pos": self.positive.toPlainText(),
            "neg": self.negative.toPlainText(),
            "chars": [(cid, r.prompt_edit.toPlainText()) for cid, r in self._selected_chars.items()],
            "loras": [(f, r.weight.value()) for f, r in self._selected_loras.items()],
            "triggers": dict(self._auto_inserted_triggers),
        }
        self.positive.clear()
        self.negative.clear()
        self._clear_selected_chars()
        self._clear_selected_loras()
        self._undo_clear_btn.setVisible(True)
        self._refresh_char_search_list()
        self._refresh_lora_search_list()
        self._on_input_changed()

    def _undo_clear(self) -> None:
        if not self._cleared_backup:
            return
        b = self._cleared_backup
        self.positive.setPlainText(b["pos"])
        self.negative.setPlainText(b["neg"])
        for cid, pt in b["chars"]:
            if cid in self._char_by_id:
                self._add_selected_character(self._char_by_id[cid], prompt_text=pt, auto_lora=False)
        for f, w in b["loras"]:
            self._add_selected_lora(f, weight=w, insert_trigger=False)
        self._auto_inserted_triggers = dict(b.get("triggers") or {})
        self._undo_clear_btn.setVisible(False)
        self._refresh_char_search_list()
        self._refresh_lora_search_list()
        self._on_input_changed()

    def _translate_prompt(self) -> None:
        self._before_translate = {
            "pos": self.positive.toPlainText(),
            "neg": self.negative.toPlainText(),
            "chars": {cid: r.prompt_edit.toPlainText() for cid, r in self._selected_chars.items()},
        }
        self._translate_btn.setEnabled(False)
        payload = {
            "action": "prompt_translate",
            "text": self.positive.toPlainText(),
            "negative": self.negative.toPlainText(),
            "checkpoint": self._current_checkpoint(),
            "characters": [
                {"name": r.card["name"], "revision": r.card["revision"], "prompt": r.prompt_edit.toPlainText()}
                for r in self._selected_chars.values()
            ],
        }

        def _do():
            res = self.app.action(payload)
            jid = res["job"]
            import time

            for _ in range(300):
                job = self.app.jobs.get(jid)
                if job["status"] == "done":
                    return job.get("result") or {}
                if job["status"] in ("failed", "cancelled"):
                    raise RuntimeError(job.get("error") or job.get("message") or "转换失败")
                time.sleep(0.4)
            raise RuntimeError("转换超时")

        def _ok(res: dict):
            self._translate_btn.setEnabled(True)
            if "text" in res:
                self.positive.setPlainText(res["text"])
            if "negative" in res:
                self.negative.setPlainText(res["negative"])
            for c in res.get("characters") or []:
                for row in self._selected_chars.values():
                    if row.card["name"] == c.get("name"):
                        row.prompt_edit.setPlainText(c.get("prompt", ""))
            self._undo_tr_btn.setVisible(True)
            self.notify_requested.emit("英文提示词已整理", False)

        def _err(msg: str):
            self._translate_btn.setEnabled(True)
            self.notify_requested.emit(msg, True)

        run_async(_do, _ok, _err)

    def _undo_translate(self) -> None:
        if not self._before_translate:
            return
        self.positive.setPlainText(self._before_translate["pos"])
        self.negative.setPlainText(self._before_translate["neg"])
        for cid, txt in self._before_translate["chars"].items():
            if cid in self._selected_chars:
                self._selected_chars[cid].prompt_edit.setPlainText(txt)
        self._undo_tr_btn.setVisible(False)

    def _start_draw(self, *, queued: bool = False) -> None:
        if self._mode_stack.currentIndex() == 1:
            if not self._apply_json_to_fields():
                self.notify_requested.emit(self._json_err.text() or "JSON 格式错误", True)
                return
        text = self.positive.toPlainText().strip()
        if not text:
            self.notify_requested.emit("请先填写正向提示词", True)
            return
        cp = self._current_checkpoint()
        if not cp:
            self.notify_requested.emit("请选择绘图模型", True)
            return
        seed_txt = self.seed_edit.text().strip()
        seed_val = None
        if seed_txt:
            try:
                seed_val = int(seed_txt)
                if not (0 <= seed_val < 2**53):
                    raise ValueError
            except ValueError:
                self.notify_requested.emit("种子须为 0 至 9007199254740991 的整数", True)
                return

        loras = [
            {
                "file": f,
                "weight": float(row.weight.value()),
                "character_id": row.owner_combo.currentData() or "",
                "trigger_words": row.trigger_edit.text().strip(),
                "selected": True,
            }
            for f, row in self._selected_loras.items()
        ]
        chars = [
            {"name": r.card["name"], "revision": r.card["revision"], "prompt": r.prompt_edit.toPlainText()}
            for r in self._selected_chars.values()
        ]
        req: dict[str, Any] = {
            "text": self.positive.toPlainText(),
            "negative": self.negative.toPlainText(),
            "checkpoint": cp,
            "count": self.count_spin.value(),
            **self.size_widget.read(),
            "loras": loras,
            "characters": chars,
            "consistency_lora": self.consistency_chk.isChecked() and not self._delta_box.isHidden(),
            "delta_matte_skin": self.matte_chk.isChecked(),
            "krea2_abliterated_encoder": self.krea_ablit_chk.isChecked() and not self._krea_box.isHidden(),
        }
        if seed_val is not None:
            req["seed"] = seed_val
        if queued and self.task_name_edit.text().strip():
            req["task_name"] = self.task_name_edit.text().strip()

        if self._board_pid:
            req["project"] = self._board_pid
            req["target"] = self._current_board_panel_id()
            req["action"] = "queue_board_panel" if queued else "draw_board_panel"
        elif queued:
            req["action"] = "queue_prompt"
        else:
            req["action"] = "draw_quick_sample"
            if self._session_pid and (
                self._session_pid == PROMPT_WORKSPACE_ID
                or self._session_pid in self.app.repo.transient_ids
            ):
                req["project"] = self._session_pid
            elif not self._session_pid:
                req["project"] = PROMPT_WORKSPACE_ID

        self._save_draft()
        try:
            res = self.app.action(req)
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)
            return

        if queued:
            self.task_name_edit.clear()
            self.notify_requested.emit("已加入队列", False)
            return
        if not self._board_pid and res.get("project"):
            new_pid = res["project"]
            if new_pid != self._session_pid:
                self._active_gallery_idx = 0
                self._known_base_ids = set()
                self._thumb_sig = None
            self._session_pid = new_pid
            self._project_snap = res.get("snapshot")
        jid = res["job"] if isinstance(res.get("job"), str) else (res.get("job") or {}).get("id", "")
        self.notify_requested.emit("已开始生成", False)
        if jid:
            self.job_started.emit(jid)

    # ==================== BOARD HELPERS ====================

    def _current_board_panel_id(self) -> str | None:
        if not self._board_pid:
            return None
        p = self._board_panel.currentData()
        return p["id"] if isinstance(p, dict) else None

    def _on_board_panel_changed(self) -> None:
        if self._sp_state is not None:
            self._exit_second_pass(silent=True)
        self._active_gallery_idx = 0
        self._known_base_ids = set()
        self._thumb_sig = None
        p = self._board_panel.currentData()
        if isinstance(p, dict):
            self._board_hint.setText(p.get("drawing_description") or p.get("main_instant") or "")
        self.refresh_gallery()

    def _step_board_panel(self, delta: int) -> None:
        cnt = self._board_panel.count()
        if cnt <= 0:
            return
        self._board_panel.setCurrentIndex((self._board_panel.currentIndex() + delta) % cnt)

    def _adopt_board_image(self) -> None:
        if not self._board_pid or not self._gallery_entries:
            return
        entry = self._gallery_entries[self._active_gallery_idx]
        target = self._current_board_panel_id()
        if not target:
            return
        try:
            self.app.action({
                "action": "select_image",
                "project": self._board_pid,
                "target": target,
                "image": entry["active_id"],
                "allow_unreviewed": True,
                "allow_rejected": True,
            })
            self.notify_requested.emit(f"已采用到 {target}", False)
            self.refresh_gallery()
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _unadopt_board_image(self) -> None:
        if not self._board_pid or not self._gallery_entries:
            return
        entry = self._gallery_entries[self._active_gallery_idx]
        target = self._current_board_panel_id()
        if not target:
            return
        try:
            self.app.action({
                "action": "unselect_board_image",
                "project": self._board_pid,
                "target": target,
                "image": entry["active_id"],
            })
            self.notify_requested.emit(f"已取消采用 {target}", False)
            self.refresh_gallery()
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    # ==================== GALLERY & SECOND PASS ====================

    @staticmethod
    def _batch_second_pass_source_id(batch: dict) -> tuple[bool, str | None]:
        """Return (is_second_pass, source_image_id) matching second_pass.py batch schema."""
        sp_info = batch.get("second_pass") if isinstance(batch.get("second_pass"), dict) else None
        ro_sp = (batch.get("render_options") or {}).get("second_pass")
        is_sp = (
            batch.get("kind") == "second_pass"
            or sp_info is not None
            or bool(ro_sp)
        )
        if not is_sp:
            return (False, None)
        source_id = (
            batch.get("source_image_id")
            or (sp_info or {}).get("source_image_id")
            or (ro_sp.get("source_image_id") if isinstance(ro_sp, dict) else None)
        )
        return (True, str(source_id) if source_id else None)

    @staticmethod
    def _set_badge(badge: QLabel, text: str, object_name: str, visible: bool = True) -> None:
        badge.setText(text)
        badge.setVisible(visible)
        if badge.objectName() != object_name:
            badge.setObjectName(object_name)
            badge.style().unpolish(badge)
            badge.style().polish(badge)

    @staticmethod
    def _image_dims(rec: dict) -> tuple[int, int]:
        w = int(rec.get("width") or 0)
        h = int(rec.get("height") or 0)
        if w > 0 and h > 0:
            return (w, h)
        return image_cache().image_size(rec.get("path"))

    def _format_image_info(self, rec: dict, fallback_batch: dict | None = None) -> str:
        batch = rec.get("batch") or fallback_batch or {}
        w, h = self._image_dims(rec)
        size_txt = f"{w}×{h}" if w > 0 and h > 0 else "—"
        sp_meta = batch.get("second_pass") if isinstance(batch.get("second_pass"), dict) else None
        ckpt_name = Path(str(batch.get("checkpoint", ""))).stem
        seed_part = f" · seed {rec['seed']}" if rec.get("seed") not in (None, "") else ""
        if sp_meta:
            scale = sp_meta.get("scale")
            scale_txt = f"{scale:g}×" if isinstance(scale, (int, float)) else ""
            if sp_meta.get("method") == "upscale_only":
                up = Path(str(sp_meta.get("upscaler") or "")).stem
                up_part = f" · {up}" if up else ""
                return f"{size_txt} · 纯超分 {scale_txt}{up_part}"
            parts = [size_txt, ckpt_name]
            if scale_txt:
                parts.append(scale_txt)
            post = sp_meta.get("post_upscale")
            if isinstance(post, (int, float)) and post > 1.0:
                parts.append(f"+超分 {post:g}×")
            if sp_meta.get("mode") == "tiled":
                tiles = sp_meta.get("tiles")
                parts.append(f"分块({tiles})" if tiles else "分块")
            if sp_meta.get("denoise") is not None:
                parts.append(f"重绘 {sp_meta['denoise']}")
            if sp_meta.get("steps"):
                parts.append(f"{sp_meta['steps']} 步")
            loras_used = batch.get("loras") or []
            if loras_used:
                parts.append("LoRA: " + ", ".join(lora_display_name(l["file"]) for l in loras_used))
            return " · ".join(p for p in parts if p) + seed_part
        loras_used = batch.get("loras") or []
        lora_txt = f" · LoRA: {', '.join(lora_display_name(l['file']) for l in loras_used)}" if loras_used else ""
        return f"{size_txt} · {ckpt_name}{seed_part}{lora_txt}"

    def _sp_items_for(self, entry: dict) -> list[tuple[str, dict]]:
        return [("原图", entry["base"])] + [(f"二采 {i + 1}", v) for i, v in enumerate(entry["variants"])]

    def _current_sp_entry(self) -> dict | None:
        if self._sp_state is None:
            return None
        return next((e for e in self._gallery_entries if e["base"]["id"] == self._sp_state["base_id"]), None)

    def refresh_gallery(self, *, live_update: bool = False) -> None:
        pid = self._board_pid or self._session_pid
        if not pid:
            self._gallery_entries = []
            self._known_base_ids = set()
            self._board_selections = {}
            self._render_gallery()
            return
        try:
            state = self.app.images_state(pid).get("images") or {}
        except Exception:
            self._gallery_entries = []
            self._known_base_ids = set()
            self._board_selections = {}
            self._render_gallery()
            return

        prev_active_base_id = (
            self._gallery_entries[self._active_gallery_idx]["base"]["id"]
            if 0 <= self._active_gallery_idx < len(self._gallery_entries)
            else None
        )

        target_panel = self._current_board_panel_id() if self._board_pid else None
        applied_map = state.get("second_pass_applied") or {}
        self._board_selections = state.get("selections") or {}
        sp_by_base: dict[str, list[dict]] = {}
        base_items: list[dict] = []

        for batch in state.get("batches") or []:
            if target_panel and batch.get("panel_id") != target_panel:
                continue
            is_sp, sp_source_id = self._batch_second_pass_source_id(batch)
            for img in batch.get("images") or []:
                path = self.app.image_file_path(pid, img["file"])
                rec = {**img, "batch": batch, "path": path, "pid": pid}
                if is_sp:
                    if sp_source_id:
                        sp_by_base.setdefault(sp_source_id, []).append(rec)
                else:
                    base_items.append(rec)

        entries = []
        for base in reversed(base_items):
            variants = sp_by_base.get(base["id"], [])
            applied_id = applied_map.get(base["id"])
            active = next((v for v in variants if v["id"] == applied_id), base)
            entries.append({
                "base": base,
                "variants": variants,
                "applied_id": applied_id,
                "active": active,
                "active_id": active["id"],
                "path": active["path"],
            })

        new_base_ids = [e["base"]["id"] for e in entries if e["base"]["id"] not in self._known_base_ids]
        had_known = bool(self._known_base_ids)
        self._known_base_ids = {e["base"]["id"] for e in entries}
        self._gallery_entries = entries

        if self._sp_state is not None:
            sp_entry = self._current_sp_entry()
            if sp_entry is None:
                self._exit_second_pass(silent=True)
            else:
                pending_prev = self._sp_state.get("pending_prev_ids")
                if pending_prev is not None:
                    fresh = [v for v in sp_entry["variants"] if v["id"] not in pending_prev]
                    expected = int(self._sp_state.get("pending_count") or 1)
                    if fresh:
                        fresh_ids = {v["id"] for v in fresh}
                        if sp_entry["applied_id"] and sp_entry["applied_id"] in fresh_ids:
                            self._sp_state["view_id"] = sp_entry["applied_id"]
                        elif not self._sp_state.get("pending_jumped"):
                            self._sp_state["view_id"] = fresh[0]["id"]
                            self._sp_state["pending_jumped"] = True
                        if (not live_update) or len(fresh) >= expected:
                            self._sp_state["pending_prev_ids"] = None
                            self._sp_state["pending_count"] = 0
                            self._sp_state["pending_jumped"] = False
                            if sp_entry["applied_id"] and sp_entry["applied_id"] in fresh_ids:
                                self.notify_requested.emit("二采完成，已自动应用；下方可切换对比原图", False)
                            else:
                                self.notify_requested.emit(
                                    f"已生成 {len(fresh)} 张二采候选，请在下方挑选并点击「应用此图」",
                                    False,
                                )
                    elif not live_update:
                        self._sp_state["pending_prev_ids"] = None
                        self._sp_state["pending_count"] = 0
                        self._sp_state["pending_jumped"] = False

        if not entries:
            self._active_gallery_idx = 0
        elif new_base_ids and had_known and self._sp_state is None:
            # New first-pass image arrived: show the newest image immediately.
            self._active_gallery_idx = 0
        elif prev_active_base_id is not None:
            found_idx = next((i for i, e in enumerate(entries) if e["base"]["id"] == prev_active_base_id), None)
            if found_idx is not None:
                self._active_gallery_idx = found_idx
            else:
                self._active_gallery_idx = max(0, min(self._active_gallery_idx, len(entries) - 1))
        elif self._active_gallery_idx >= len(entries):
            self._active_gallery_idx = max(0, len(entries) - 1)

        self._render_gallery()

    def _clear_thumbnails(self) -> None:
        self._thumb_sig = None
        self._thumb_cards = []
        while self._thumb_row.count():
            w = self._thumb_row.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _sync_thumbnails(
        self,
        specs: list[tuple[str, Path, str, str, bool, Any, Any]],
        selected_id: str,
    ) -> None:
        """Reuse existing thumbnail cards when only selection changed; rebuild only when items change."""
        if len(specs) <= 1:
            self._thumb_scroll.setVisible(False)
            if self._thumb_cards:
                self._clear_thumbnails()
            return

        sig = (
            self._sp_state is not None,
            tuple(
                (item_id, image_cache().file_key(path), caption, tooltip, applied)
                for item_id, path, caption, tooltip, applied, _cb, _dbl_cb in specs
            ),
        )
        if sig == self._thumb_sig and len(self._thumb_cards) == len(specs):
            self._thumb_scroll.setVisible(True)
            selected_card = None
            for item_id, card in self._thumb_cards:
                is_sel = item_id == selected_id
                card.set_selected(is_sel)
                if is_sel:
                    selected_card = card
            if selected_card is not None:
                self._thumb_scroll.ensureWidgetVisible(selected_card, 24, 0)
            return

        self._clear_thumbnails()
        self._thumb_sig = sig
        selected_card = None
        for item_id, path, caption, tooltip, applied, cb, dbl_cb in specs:
            is_sel = item_id == selected_id
            card = _ThumbCard(path, caption, tooltip, selected=is_sel, applied=applied, parent=self._thumb_host)
            card.clicked.connect(cb)
            if dbl_cb is not None:
                card.double_clicked.connect(dbl_cb)
            self._thumb_row.addWidget(card)
            self._thumb_cards.append((item_id, card))
            if is_sel:
                selected_card = card
        self._thumb_scroll.setVisible(True)
        if selected_card is not None:
            self._thumb_scroll.ensureWidgetVisible(selected_card, 24, 0)

    def _prefetch_neighbors(self, paths: list[Path], cur_idx: int) -> None:
        n = len(paths)
        if n <= 1:
            return
        targets: list[Path] = []
        for offset in (1, -1, 2, -2):
            idx = (cur_idx + offset) % n
            p = paths[idx]
            if p not in targets:
                targets.append(p)
        image_cache().prefetch(targets)

    def _sync_gallery_controls(self, *, has_images: bool, item_count: int) -> None:
        in_sp = self._sp_state is not None
        is_board = bool(self._board_pid)
        self._copy_one_btn.setEnabled(has_images)
        self._copy_all_btn.setEnabled(has_images)
        self._copy_all_btn.setText(f"复制全部 ({item_count})" if has_images and item_count > 1 else "复制全部")
        self._save_as_btn.setEnabled(has_images)
        self._clear_imgs_btn.setVisible(not is_board and not in_sp)
        self._clear_imgs_btn.setEnabled(has_images and not in_sp)
        self._board_layout_btn.setVisible(is_board and not in_sp)
        self._prev_panel_btn.setVisible(is_board and not in_sp)
        self._next_panel_btn.setVisible(is_board and not in_sp)
        can_step = has_images and item_count > 1
        self._prev_img_btn.setEnabled(can_step)
        self._next_img_btn.setEnabled(can_step)
        self._delete_img_btn.setVisible(has_images)
        self._delete_img_btn.setEnabled(has_images)

    def _render_gallery(self) -> None:
        if not self._gallery_entries:
            self._clear_thumbnails()
            self._viewer.set_image(None)
            self._viewer.setToolTip("")
            self._gal_stack.setCurrentIndex(0)
            self._thumb_scroll.setVisible(False)
            self._open_sp_btn.setVisible(False)
            self._img_badge.setVisible(False)
            self._apply_sp_btn.setVisible(False)
            self._unapply_sp_btn.setVisible(False)
            self._adopt_btn.setVisible(False)
            self._unadopt_btn.setVisible(False)
            self._img_info.setText("")
            self._img_info.setToolTip("")
            self._pager_lbl.setText("")
            self._sync_gallery_controls(has_images=False, item_count=0)
            return

        self._gal_stack.setCurrentIndex(1)

        if self._sp_state is not None:
            entry = self._current_sp_entry()
            if entry is None:
                self._exit_second_pass(silent=True)
                self._render_gallery()
                return
            self._open_sp_btn.setVisible(False)
            items = self._sp_items_for(entry)
            view_id = self._sp_state.get("view_id") or entry["active_id"]
            cur_idx = next((i for i, (_, it) in enumerate(items) if it["id"] == view_id), None)
            if cur_idx is None:
                cur_idx = next((i for i, (_, it) in enumerate(items) if it["id"] == entry["active_id"]), len(items) - 1)
            cur_lbl, cur_item = items[cur_idx]
            self._sp_state["view_id"] = cur_item["id"]

            self._viewer.set_image(cur_item["path"])
            is_base = cur_item["id"] == entry["base"]["id"]
            is_applied = (not is_base) and (entry["applied_id"] == cur_item["id"])
            if is_base:
                self._viewer.setToolTip("单击查看大图 · 滚轮缩放")
            elif is_applied:
                self._viewer.setToolTip("单击查看大图 · 已应用此二采图 · 滚轮缩放")
            else:
                self._viewer.setToolTip("单击查看大图 · 双击应用此二采图 · 滚轮缩放")
            if is_applied:
                self._set_badge(self._img_badge, "已应用二采", "BadgeSuccess", True)
            elif is_base and entry["applied_id"]:
                self._set_badge(self._img_badge, "原图（当前已应用二采）", "BadgeMuted", True)
            elif is_base:
                self._set_badge(self._img_badge, "原图", "BadgeMuted", True)
            else:
                self._set_badge(self._img_badge, cur_lbl, "BadgeAccent", True)

            info_txt = self._format_image_info(cur_item, entry["base"].get("batch"))
            self._img_info.setText(info_txt)
            self._img_info.setToolTip(info_txt)

            self._apply_sp_btn.setVisible((not is_base) and (not is_applied))
            self._unapply_sp_btn.setVisible(bool(entry["applied_id"]))
            self._adopt_btn.setVisible(False)
            self._unadopt_btn.setVisible(False)
            self._pager_lbl.setText(f"{cur_idx + 1} / {len(items)}")
            self._sync_gallery_controls(has_images=True, item_count=len(items))

            thumb_specs = []
            for idx_i, (lbl, it) in enumerate(items):
                is_item_base = it["id"] == entry["base"]["id"]
                item_applied = (not is_item_base) and (entry["applied_id"] == it["id"])
                cap = "原图" if idx_i == 0 else ("已应用" if item_applied else f"二采 {idx_i}")
                tip = lbl if is_item_base else (f"{lbl}（已应用）" if item_applied else f"{lbl}（双击应用）")
                thumb_specs.append((
                    it["id"],
                    it["path"],
                    cap,
                    tip,
                    item_applied,
                    lambda _=False, iid=it["id"]: self._select_sp_view(iid),
                    None if is_item_base else (lambda iid=it["id"]: self._on_thumb_double_clicked_sp(iid)),
                ))
            self._sync_thumbnails(thumb_specs, cur_item["id"])
            self._prefetch_neighbors([it["path"] for _, it in items], cur_idx)
            return

        self._open_sp_btn.setVisible(True)
        entry = self._gallery_entries[self._active_gallery_idx]
        active = entry["active"]
        self._viewer.set_image(active["path"])
        self._viewer.setToolTip("单击查看大图 · 双击进入二采精修 · 滚轮缩放")
        info_txt = self._format_image_info(active, entry["base"].get("batch"))
        self._img_info.setText(info_txt)
        self._img_info.setToolTip(info_txt)

        if entry["applied_id"]:
            self._set_badge(self._img_badge, "已应用二采", "BadgeSuccess", True)
            self._unapply_sp_btn.setVisible(True)
        elif entry["variants"]:
            self._set_badge(self._img_badge, f"{len(entry['variants'])} 张二采候选", "BadgeAccent", True)
            self._unapply_sp_btn.setVisible(False)
        else:
            self._img_badge.setVisible(False)
            self._unapply_sp_btn.setVisible(False)
        self._apply_sp_btn.setVisible(False)

        if self._board_pid:
            target = self._current_board_panel_id()
            sel = (self._board_selections.get(target) or {}) if target else {}
            is_adopted = bool(sel and (sel.get("image") or {}).get("id") == entry["active_id"])
            self._adopt_btn.setVisible(True)
            self._adopt_btn.setText("已采用" if is_adopted else "采用到此格")
            self._adopt_btn.setEnabled(not is_adopted)
            self._adopt_btn.setProperty("primary", not is_adopted)
            self._adopt_btn.style().unpolish(self._adopt_btn)
            self._adopt_btn.style().polish(self._adopt_btn)
            self._unadopt_btn.setVisible(is_adopted)
        else:
            self._adopt_btn.setVisible(False)
            self._unadopt_btn.setVisible(False)

        self._pager_lbl.setText(f"{self._active_gallery_idx + 1} / {len(self._gallery_entries)}")
        self._sync_gallery_controls(has_images=True, item_count=len(self._gallery_entries))

        thumb_specs = []
        for i, e_item in enumerate(self._gallery_entries[:200]):
            has_applied = bool(e_item["applied_id"])
            var_cnt = len(e_item["variants"])
            if has_applied:
                cap = "二采"
                tip = f"#{i + 1} · 已应用二采"
            elif var_cnt:
                cap = f"#{i + 1}·{var_cnt}采"
                tip = f"#{i + 1} · {var_cnt} 张二采候选"
            else:
                cap = f"#{i + 1}"
                tip = f"#{i + 1}"
            thumb_specs.append((
                e_item["base"]["id"],
                e_item["path"],
                cap,
                f"{tip}（双击进入二采）",
                has_applied,
                lambda _=False, idx=i: self._select_gallery_index(idx),
                lambda idx=i: self._on_thumb_double_clicked_gallery(idx),
            ))
        self._sync_thumbnails(thumb_specs, entry["base"]["id"])
        self._prefetch_neighbors([e["path"] for e in self._gallery_entries], self._active_gallery_idx)

    def _on_viewer_double_clicked(self) -> None:
        if not self._gallery_entries:
            return
        if self._sp_state is None:
            self._enter_second_pass()
            return
        entry = self._current_sp_entry()
        if not entry:
            return
        view_id = self._sp_state.get("view_id") or entry["active_id"]
        if not view_id or view_id == entry["base"]["id"]:
            return
        if entry["applied_id"] == view_id:
            self.notify_requested.emit("当前已应用该二采图", False)
            return
        self._apply_active_sp()

    def _on_thumb_double_clicked_gallery(self, idx: int) -> None:
        if 0 <= idx < len(self._gallery_entries):
            self._active_gallery_idx = idx
            self._enter_second_pass()

    def _on_thumb_double_clicked_sp(self, image_id: str) -> None:
        if not self._sp_state:
            return
        entry = self._current_sp_entry()
        if not entry or image_id == entry["base"]["id"]:
            return
        self._select_sp_view(image_id)
        if entry["applied_id"] == image_id:
            self.notify_requested.emit("当前已应用该二采图", False)
            return
        self._apply_active_sp()

    def _on_ctrl_enter(self) -> None:
        if self._sp_state is not None:
            self._run_second_pass()
        else:
            self._start_draw()

    def _on_ctrl_z(self) -> None:
        if not self.isVisible():
            return
        focus = QApplication.focusWidget()
        if isinstance(focus, QPlainTextEdit) and not focus.isReadOnly() and focus.document().isUndoAvailable():
            focus.undo()
            return
        if isinstance(focus, QLineEdit) and not focus.isReadOnly() and focus.isUndoAvailable():
            focus.undo()
            return
        self._undo_delete_image()

    def _delete_current_image(self) -> None:
        pid = self._board_pid or self._session_pid
        if not pid or not self._gallery_entries:
            return
        if self._sp_state is not None:
            entry = self._current_sp_entry()
            if not entry:
                return
            view_id = self._sp_state.get("view_id") or entry["active_id"]
            is_base = view_id == entry["base"]["id"]
            target_id = view_id
            items = self._sp_items_for(entry)
            cur_idx = next((i for i, (_, it) in enumerate(items) if it["id"] == view_id), 0)
            next_view_id = None
            if not is_base and len(items) > 1:
                remaining = [it["id"] for _, it in items if it["id"] != view_id]
                next_view_id = remaining[min(cur_idx, len(remaining) - 1)] if remaining else entry["base"]["id"]
        else:
            entry = self._gallery_entries[self._active_gallery_idx]
            target_id = entry["base"]["id"]
            is_base = True
            next_view_id = None

        prev_idx = self._active_gallery_idx
        try:
            res = self.app.action({
                "action": "delete_gallery_image",
                "project": pid,
                "image_id": target_id,
                "delete_variants": is_base,
            })
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)
            return

        if self._sp_state is not None:
            if is_base:
                self._exit_second_pass(silent=True)
            elif next_view_id:
                self._sp_state["view_id"] = next_view_id

        self._thumb_sig = None
        self.refresh_gallery()
        if self._sp_state is None and self._gallery_entries:
            self._active_gallery_idx = max(0, min(prev_idx, len(self._gallery_entries) - 1))
            self._render_gallery()
        depth = int(res.get("undo_depth") or 0)
        self.notify_requested.emit(f"已移入缓存（Ctrl+Z 撤销 · {depth}/15）", False)

    def _undo_delete_image(self) -> None:
        pid = self._board_pid or self._session_pid
        if not pid:
            return
        try:
            res = self.app.action({
                "action": "undo_delete_gallery_image",
                "project": pid,
            })
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)
            return
        if not res.get("restored"):
            self.notify_requested.emit("没有可撤销的删除记录", False)
            return

        restored_id = res.get("restored_image_id")
        scope = res.get("scope")
        source_base_id = res.get("source_base_id")
        self._thumb_sig = None
        self.refresh_gallery()
        if self._sp_state is not None:
            if scope == "variant" and source_base_id == self._sp_state.get("base_id") and restored_id:
                self._sp_state["view_id"] = restored_id
                self._render_gallery()
        elif restored_id:
            target_base = source_base_id if scope == "variant" else restored_id
            found = next((i for i, e_item in enumerate(self._gallery_entries) if e_item["base"]["id"] == target_base), None)
            if found is not None:
                self._active_gallery_idx = found
                self._render_gallery()
        rem = int(res.get("remaining_undo") or 0)
        self.notify_requested.emit(f"已撤销删除（剩余可撤销 {rem} 步）", False)

    def _on_alt_number(self, idx: int) -> None:
        if self._sp_state is not None:
            entry = self._current_sp_entry()
            if not entry:
                return
            items = self._sp_items_for(entry)
            if 0 <= idx < len(items):
                self._select_sp_view(items[idx][1]["id"])
            return
        self._select_gallery_index(idx)

    def _select_gallery_index(self, idx: int) -> None:
        if 0 <= idx < len(self._gallery_entries) and idx != self._active_gallery_idx:
            self._active_gallery_idx = idx
            self._render_gallery()

    def _step_gallery(self, delta: int) -> None:
        if self._sp_state is not None:
            entry = self._current_sp_entry()
            if not entry:
                return
            ids = [entry["base"]["id"]] + [v["id"] for v in entry["variants"]]
            if len(ids) <= 1:
                return
            cur = ids.index(self._sp_state["view_id"]) if self._sp_state.get("view_id") in ids else 0
            self._sp_state["pending_prev_ids"] = None
            self._sp_state["view_id"] = ids[(cur + delta) % len(ids)]
            self._render_gallery()
            return
        if len(self._gallery_entries) > 1:
            self._active_gallery_idx = (self._active_gallery_idx + delta) % len(self._gallery_entries)
            self._render_gallery()

    def _open_lightbox(self) -> None:
        if self._sp_state is not None:
            entry = self._current_sp_entry()
            if not entry:
                return
            items = self._sp_items_for(entry)
            view_id = self._sp_state.get("view_id") or entry["active_id"]
            paths: list[Path] = []
            labels: list[str] = []
            start_idx = 0
            for lbl, it in items:
                p = it["path"]
                if p and p.is_file():
                    if it["id"] == view_id:
                        start_idx = len(paths)
                    tag = f"{lbl}（已应用）" if (it["id"] != entry["base"]["id"] and entry["applied_id"] == it["id"]) else lbl
                    paths.append(p)
                    labels.append(tag)
            if paths:
                ImageLightboxDialog(paths, start_idx, self, labels=labels).exec()
            return

        paths = []
        labels = []
        start_idx = 0
        for idx, e_item in enumerate(self._gallery_entries):
            p = e_item["path"]
            if p and p.is_file():
                if idx == self._active_gallery_idx:
                    start_idx = len(paths)
                paths.append(p)
                labels.append(f"#{idx + 1} · 已应用二采" if e_item["applied_id"] else f"#{idx + 1}")
        if not paths:
            return
        ImageLightboxDialog(paths, start_idx, self, labels=labels).exec()

    def _copy_current_image(self) -> None:
        p = self._viewer.current_path()
        if p and copy_images_to_clipboard([p]):
            self.notify_requested.emit("已复制当前原图到剪贴板", False)

    def _copy_all_images(self) -> None:
        if self._sp_state is not None:
            entry = self._current_sp_entry()
            paths = [it["path"] for _, it in self._sp_items_for(entry) if it["path"].is_file()] if entry else []
        else:
            paths = [e["path"] for e in self._gallery_entries if e["path"].is_file()]
        n = copy_images_to_clipboard(paths)
        if n:
            self.notify_requested.emit(f"已复制 {n} 张原图到剪贴板", False)

    def _save_current_image(self) -> None:
        p = self._viewer.current_path()
        if not p or not p.is_file():
            return
        default_name = f"LocalDraw-{p.parent.name[:12]}-{p.stem}.png"
        dest, _ = QFileDialog.getSaveFileName(self, "保存原图", default_name, "PNG 图片 (*.png)")
        if dest:
            shutil.copy2(p, dest)
            self.notify_requested.emit("已保存到：" + dest, False)

    def _clear_images(self) -> None:
        if not self._session_pid:
            return
        try:
            self._exit_second_pass(silent=True)
            self.app.action({"action": "clear_prompt_images", "project": self._session_pid})
            self._active_gallery_idx = 0
            self._known_base_ids = set()
            self._thumb_sig = None
            self.refresh_gallery()
            self.notify_requested.emit("已清空临时图片", False)
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    # ==================== SECOND PASS LOGIC ====================

    def _set_sp_preset_active(self, active_key: str | None) -> None:
        for k, b in self._sp_preset_btns.items():
            want = k == active_key
            if bool(b.property("active")) != want:
                b.setProperty("active", want)
                b.style().unpolish(b)
                b.style().polish(b)

    def _clear_sp_preset_active(self) -> None:
        if not self._applying_sp_preset:
            self._set_sp_preset_active(None)

    def _enter_second_pass(self) -> None:
        if not self._gallery_entries:
            return
        from ..second_pass import base_used_krea2_abliterated, resolution_tier

        entry = self._gallery_entries[self._active_gallery_idx]
        base = entry["base"]
        orig_w, orig_h = self._image_dims(base)
        ow = orig_w or 1024
        oh = orig_h or 1024
        tier, _ = resolution_tier(ow, oh)
        self._sp_state = {
            "base_id": base["id"],
            "pid": base["pid"],
            "base_checkpoint": base["batch"].get("checkpoint", ""),
            "base_abliterated": base_used_krea2_abliterated(base["batch"]),
            "orig_w": ow,
            "orig_h": oh,
            "view_id": entry["active_id"],
            "pending_prev_ids": None,
            "pending_count": 0,
            "pending_jumped": False,
        }
        self._sp_denoise_manual = False
        self._sp_scale_manual = False
        self._sp_krea_ablit_manual = False
        self._set_sp_preset_active(None)
        self._sp_seed.clear()
        self._sp_count.setValue(1)
        self._sp_lora_search.clear()

        # Populate SP models & upscalers
        diff = self._drawing_opts.get("diffusion_models") or {}
        ckpts = [c for c in ((self._drawing_opts.get("comfy") or {}).get("checkpoints") or []) if (diff.get(c) or {}).get("family") != "qwen_edit"]
        self._sp_model.blockSignals(True)
        self._sp_model.clear()
        for c in ckpts:
            self._sp_model.addItem((diff.get(c) or {}).get("label") or c, c)
        idx = self._sp_model.findData(self._sp_state["base_checkpoint"])
        self._sp_model.setCurrentIndex(max(0, idx))
        self._sp_model.blockSignals(False)

        self._sp_upscaler.blockSignals(True)
        self._sp_upscaler.clear()
        ups = list(self._drawing_opts.get("upscale_models") or [])
        if ups:
            for u in ups:
                self._sp_upscaler.addItem(u, u)
        else:
            self._sp_upscaler.addItem("未安装超分模型", "")
        self._sp_upscaler.blockSignals(False)

        # Resolution-aware initial scale: 1K -> 1.4x, 1.5K -> 1.25x, 2K/4K -> 1.0x
        init_scale = 1.4 if tier == "1k" else (1.25 if tier == "1.5k" else 1.0)
        self._applying_sp_preset = True
        try:
            self._set_combo_data(self._sp_scale, init_scale)
            self._set_combo_data(self._sp_method, "latent")
            self._set_combo_data(self._sp_mode, "tiled" if tier == "4k" else "auto")
            self._set_combo_data(self._sp_post_up, 1.0)
            self._sp_prompt_toggle.setChecked(False)
            self._sp_pos.setPlainText(base.get("user_positive") or base.get("positive") or self.positive.toPlainText())
            self._sp_neg.setPlainText(base.get("negative") or self.negative.toPlainText())
        finally:
            self._applying_sp_preset = False

        # Inherit compatible base LoRAs (triggers stay out of _sp_pos and inject at render time)
        self._sp_selected_loras = {}
        self._sp_auto_inserted_triggers = {}
        for l in base["batch"].get("loras") or []:
            lf = str(l["file"]).replace("\\", "/")
            self._sp_selected_loras[lf] = float(l.get("weight", 0.7))

        self._left_stack.setCurrentIndex(1)
        fade_widget_in(self._left_stack.currentWidget(), duration=170)
        self._on_sp_model_changed()
        self._sync_sp_method_controls()
        self._update_sp_final_preview()
        self._render_gallery()

    def _exit_second_pass(self, *, silent: bool = False) -> None:
        if self._sp_state is None:
            return
        exited_base_id = self._sp_state.get("base_id")
        self._sp_state = None
        self._sp_auto_inserted_triggers.clear()
        self._left_stack.setCurrentIndex(0)
        if not silent:
            fade_widget_in(self._left_stack.currentWidget(), duration=170)
        if exited_base_id:
            found = next((i for i, e in enumerate(self._gallery_entries) if e["base"]["id"] == exited_base_id), None)
            if found is not None:
                self._active_gallery_idx = found
        if not silent:
            self._render_gallery()

    def _on_escape(self) -> None:
        if self._sp_state is not None:
            self._exit_second_pass()

    def _set_combo_data(self, combo: QComboBox, val: Any) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == val:
                combo.setCurrentIndex(i)
                return

    def _default_sp_denoise(self, fam: str, method: str) -> float:
        latent = method == "latent"
        scale = float(self._sp_scale.currentData() or 1.0)
        if fam == "krea2":
            if not latent:
                return 0.28
            return 0.32 if scale <= 1.0 else 0.38
        if fam == "moss_image":
            return 0.30 if (latent and scale <= 1.0) else (0.40 if latent else 0.28)
        return 0.32 if (latent and scale <= 1.0) else (0.42 if latent else 0.32)

    def _on_sp_krea_ablit_toggled(self, _checked: bool) -> None:
        if not self._applying_sp_preset:
            self._sp_krea_ablit_manual = True

    def _sync_sp_method_controls(self) -> None:
        pure_up = str(self._sp_method.currentData() or "latent") == "upscale_only"
        for w in (
            self._sp_model,
            self._sp_mode,
            self._sp_sampler,
            self._sp_scheduler,
            self._sp_denoise,
            self._sp_steps,
            self._sp_seed,
            self._sp_count,
            self._sp_post_up,
        ):
            w.setEnabled(not pure_up)
        self._sp_lora_section.setVisible(not pure_up)
        self._sp_prompt_toggle.setVisible(not pure_up)
        self._sp_prompt_box.setVisible((not pure_up) and self._sp_prompt_toggle.isChecked())
        if pure_up:
            self._sp_krea_ablit_chk.setVisible(False)
        else:
            cp = str(self._sp_model.currentData() or "")
            fam = ((self._drawing_opts.get("diffusion_models") or {}).get(cp) or {}).get("family", "")
            self._sp_krea_ablit_chk.setVisible(fam == "krea2" and self._krea_abliterated_available())

    def _on_sp_method_changed(self) -> None:
        if not self._sp_state:
            return
        self._clear_sp_preset_active()
        method = str(self._sp_method.currentData() or "latent")
        if method == "upscale_only" and not self._sp_scale_manual and float(self._sp_scale.currentData() or 1.0) == 1.0:
            self._sp_scale.blockSignals(True)
            self._set_combo_data(self._sp_scale, 1.5)
            self._sp_scale.blockSignals(False)
        elif method != "upscale_only" and not self._applying_sp_preset and not self._sp_denoise_manual:
            cp = str(self._sp_model.currentData() or "")
            fam = ((self._drawing_opts.get("diffusion_models") or {}).get(cp) or {}).get("family", "")
            self._sp_denoise.blockSignals(True)
            self._sp_denoise.setValue(self._default_sp_denoise(fam, method))
            self._sp_denoise.blockSignals(False)
        self._sync_sp_method_controls()
        self._update_sp_hint()

    def _on_sp_scale_changed(self) -> None:
        if not self._applying_sp_preset:
            self._sp_scale_manual = True
            self._clear_sp_preset_active()
        self._update_sp_hint()

    def _on_sp_denoise_changed(self) -> None:
        if not self._applying_sp_preset:
            self._sp_denoise_manual = True
            self._clear_sp_preset_active()
        self._update_sp_hint()

    def _on_sp_param_changed(self) -> None:
        self._clear_sp_preset_active()
        self._update_sp_hint()

    def _on_sp_model_changed(self) -> None:
        if not self._sp_state:
            return
        self._clear_sp_preset_active()
        cp = str(self._sp_model.currentData() or "")
        diff = self._drawing_opts.get("diffusion_models") or {}
        info = diff.get(cp) or {}
        fam = info.get("family", "")
        method = str(self._sp_method.currentData() or "latent")
        prev_applying = self._applying_sp_preset
        self._applying_sp_preset = True
        try:
            if fam == "krea2":
                self._set_combo_data(self._sp_sampler, "euler")
                self._set_combo_data(self._sp_scheduler, "simple")
                self._sp_steps.setValue(8)
                self._sp_denoise.setValue(self._default_sp_denoise(fam, method))
                self._sp_krea_ablit_chk.setVisible(self._krea_abliterated_available() and method != "upscale_only")
                if not self._sp_krea_ablit_manual:
                    self._sp_krea_ablit_chk.blockSignals(True)
                    self._sp_krea_ablit_chk.setChecked(bool(self._sp_state.get("base_abliterated", False)))
                    self._sp_krea_ablit_chk.blockSignals(False)
            elif fam == "moss_image":
                self._sp_krea_ablit_chk.setVisible(False)
                self._set_combo_data(self._sp_sampler, info.get("sampler_name") or "er_sde")
                self._set_combo_data(self._sp_scheduler, info.get("scheduler") or "simple")
                self._sp_steps.setValue(20)
                self._sp_denoise.setValue(self._default_sp_denoise(fam, method))
            else:
                self._sp_krea_ablit_chk.setVisible(False)
                self._set_combo_data(self._sp_sampler, info.get("sampler_name") or "er_sde")
                self._set_combo_data(self._sp_scheduler, info.get("scheduler") or "simple")
                self._sp_steps.setValue(min(24, int(info.get("steps") or 24)))
                self._sp_denoise.setValue(self._default_sp_denoise(fam, method))
        finally:
            self._applying_sp_preset = prev_applying
        self._sp_denoise_manual = False

        # Cross-model prompt auto-expand
        cross = bool(cp and cp != self._sp_state["base_checkpoint"])
        if cross:
            self._sp_prompt_toggle.setChecked(True)
            self._sp_cross_warn.setText("跨模型二采")
            self._sp_cross_warn.setVisible(True)
        else:
            self._sp_cross_warn.setVisible(False)

        # Prune incompatible SP LoRAs
        target_fam, target_dir = checkpoint_family_and_dir(cp, diff)
        insp = self._drawing_opts.get("lora_inspection") or {}
        for f in list(self._sp_selected_loras):
            if not f.startswith(target_dir + "/") or (insp.get(f) or {}).get("family") != target_fam:
                self._sp_selected_loras.pop(f, None)
        self._render_sp_loras()
        self._refresh_sp_lora_results()
        self._sync_sp_method_controls()
        self._update_sp_final_preview()
        self._update_sp_hint()

    def _apply_sp_preset(self, key: str) -> None:
        if not self._sp_state:
            return
        from ..second_pass import recommend_preset

        self._set_sp_preset_active(key)

        ckpts = [self._sp_model.itemData(i) for i in range(self._sp_model.count())]
        ups = [str(self._sp_upscaler.itemData(i) or "") for i in range(self._sp_upscaler.count()) if self._sp_upscaler.itemData(i)]
        rec = recommend_preset(
            key,
            self._sp_state["orig_w"],
            self._sp_state["orig_h"],
            base_checkpoint=self._sp_state.get("base_checkpoint", ""),
            installed_checkpoints=ckpts,
            installed_upscalers=ups,
        )
        installed_loras = set((self._drawing_opts.get("comfy") or {}).get("loras") or [])

        self._applying_sp_preset = True
        try:
            if rec.get("checkpoint"):
                self._set_combo_data(self._sp_model, rec["checkpoint"])
            self._set_combo_data(self._sp_method, rec["method"])
            self._set_combo_data(self._sp_scale, rec["scale"])
            self._set_combo_data(self._sp_mode, rec["mode"])
            self._set_combo_data(self._sp_post_up, rec["post_upscale"])
            self._set_combo_data(self._sp_sampler, rec["sampler_name"])
            self._set_combo_data(self._sp_scheduler, rec["scheduler"])
            self._sp_denoise.setValue(float(rec["denoise"]))
            self._sp_steps.setValue(int(rec["steps"]))
            if rec.get("upscaler"):
                self._set_combo_data(self._sp_upscaler, rec["upscaler"])

            if key == "krea2":
                self._sp_krea_ablit_manual = False
                self._sp_krea_ablit_chk.blockSignals(True)
                self._sp_krea_ablit_chk.setChecked(bool(self._sp_state.get("base_abliterated", False)))
                self._sp_krea_ablit_chk.blockSignals(False)
                self._sp_neg.clear()
                for lf, lw in [
                    ("krea2/画风/Detailer-KREA2.safetensors", 0.30),
                    ("krea2/画风/Krea2Rella_c1-st8000.safetensors", 0.30),
                    ("krea2/画风/v67Lighting_v1.safetensors", 0.25),
                ]:
                    if lf in installed_loras and lf not in self._sp_selected_loras and len(self._sp_selected_loras) < 5:
                        self._sp_selected_loras[lf] = lw
            elif key == "moss":
                self._sp_neg.clear()
                self._sp_selected_loras.clear()
        finally:
            self._applying_sp_preset = False

        self._sync_sp_method_controls()
        self._render_sp_loras()
        self._refresh_sp_lora_results()
        self._update_sp_final_preview()
        self._update_sp_hint()

    def _render_sp_loras(self) -> None:
        while self._sp_lora_box.count():
            w = self._sp_lora_box.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if not self._sp_selected_loras:
            lbl = QLabel("未选 LoRA")
            lbl.setObjectName("Muted")
            self._sp_lora_box.addWidget(lbl)
            return
        close_ic = studio_icon("close", tokens()["muted"], 13)
        cp = str(self._sp_model.currentData() or "")
        label_map = self._lora_label_map_for(cp)
        fav_set = set(self.app.ui_state.favorite_loras())
        for f, w in list(self._sp_selected_loras.items()):
            row = QFrame()
            row.setObjectName("SubCard")
            hl = QHBoxLayout(row)
            hl.setContentsMargins(9, 5, 6, 5)
            hl.setSpacing(6)
            lbl = QLabel(label_map.get(f, lora_choice_name(f)))
            lbl.setStyleSheet("font-weight: 500;")
            lbl.setToolTip(f)
            hl.addWidget(lbl, 1)
            is_fav = f in fav_set
            fav_b = QPushButton()
            fav_b.setProperty("ghost", True)
            fav_b.setProperty("compact", True)
            fav_b.setFixedSize(24, 24)
            fav_b.setIconSize(QSize(13, 13))
            fav_b.setIcon(
                studio_icon("star-filled", LORA_STAR_COLOR, 13)
                if is_fav
                else studio_icon("star", tokens()["muted"], 13)
            )
            fav_b.setToolTip("取消收藏" if is_fav else "收藏此 LoRA（搜索同类型时排第一）")
            fav_b.clicked.connect(lambda _=False, file=f: self._toggle_favorite_lora(file))
            hl.addWidget(fav_b)
            sp = DecimalSpinBox()
            sp.setRange(0.0, 2.0)
            sp.setSingleStep(0.05)
            sp.setValue(w)
            sp.setFixedWidth(78)
            sp.valueChanged.connect(lambda val, file=f: self._set_sp_lora_weight(file, val))
            rm = QPushButton()
            rm.setIcon(close_ic)
            rm.setIconSize(QSize(13, 13))
            rm.setToolTip("移除")
            rm.setProperty("danger", True)
            rm.setProperty("compact", True)
            rm.setFixedSize(24, 24)
            rm.clicked.connect(lambda _=False, file=f: self._remove_sp_lora(file))
            hl.addWidget(sp)
            hl.addWidget(rm)
            self._sp_lora_box.addWidget(row)

    def _set_sp_lora_weight(self, file: str, val: float) -> None:
        self._sp_selected_loras[file] = float(val)
        self.app.ui_state.remember_lora_weight(file, float(val))

    def _update_sp_final_preview(self) -> None:
        pos = self._sp_pos.toPlainText()
        trigs = [t for f in self._sp_selected_loras if (t := self._resolve_lora_trigger(f))]
        final_text = apply_lora_triggers(pos, trigs)
        self._sp_final_preview.setPlainText(final_text)
        unique_trigs: list[str] = []
        seen: set[str] = set()
        for t in trigs:
            low = t.lower()
            if low not in seen:
                seen.add(low)
                unique_trigs.append(t)
        auto_trigs = [t for t in unique_trigs if not trigger_present_in_prompt(pos, t)]
        if auto_trigs:
            self._sp_final_preview_lbl.setText(f"二采最终提示词预览（后台附带 {len(auto_trigs)} 个触发词）")
        else:
            self._sp_final_preview_lbl.setText("二采最终提示词预览")
        if len(auto_trigs) >= 2:
            self._sp_lora_bleed_warn.setText(
                f"二采已选 {len(auto_trigs)} 个带不同触发词的 LoRA，叠加出图易串特征；建议调低权重或分句写入提示词"
            )
            self._sp_lora_bleed_warn.setVisible(True)
        else:
            self._sp_lora_bleed_warn.setVisible(False)

    def _remove_sp_lora(self, file: str) -> None:
        self._sp_selected_loras.pop(file, None)
        self._render_sp_loras()
        self._refresh_sp_lora_results()
        self._update_sp_final_preview()

    def _refresh_sp_lora_results(self) -> None:
        cp = str(self._sp_model.currentData() or "")
        insp = self._drawing_opts.get("lora_inspection") or {}
        q = self._sp_lora_search.text().strip()
        family_loras = self._family_loras_for(cp)
        label_map = lora_disambiguated_names(family_loras)
        favs = self.app.ui_state.favorite_loras()
        fav_set = set(favs)
        matches = [
            f
            for f in family_loras
            if f not in self._sp_selected_loras and lora_matches_search(f, insp.get(f) or {}, q)
        ]
        matches = sort_loras_favorites_first(matches, favs)

        self._sp_lora_results.setUpdatesEnabled(False)
        try:
            self._sp_lora_results.clear()
            plus_ic = studio_icon("plus", tokens()["muted"], 13)
            for f in matches:
                it = QListWidgetItem(plus_ic, label_map.get(f, lora_choice_name(f)))
                it.setData(Qt.ItemDataRole.UserRole, f)
                it.setData(ROLE_LORA_FAV, f in fav_set)
                it.setToolTip(f)
                self._sp_lora_results.addItem(it)
        finally:
            self._sp_lora_results.setUpdatesEnabled(True)

    def _on_pick_sp_lora(self, item: QListWidgetItem) -> None:
        f = item.data(Qt.ItemDataRole.UserRole)
        if not f:
            return
        if len(self._sp_selected_loras) >= 5:
            self.notify_requested.emit("最多选择 5 个 LoRA", True)
            return
        w = self.app.ui_state.lora_weights().get(f, 0.7)
        self._sp_selected_loras[f] = float(w)
        self._sp_lora_search.clear()
        self._render_sp_loras()
        self._refresh_sp_lora_results()
        self._update_sp_final_preview()

    def _update_sp_hint(self) -> None:
        if not self._sp_state:
            return
        from ..second_pass import resolution_tier

        cp = str(self._sp_model.currentData() or "")
        fam = ((self._drawing_opts.get("diffusion_models") or {}).get(cp) or {}).get("family", "")
        align = 16 if fam == "moss_image" else 8
        comfort = {"krea2": 2_850_000, "moss_image": 3_100_000, "anima": 2_400_000, "anima38": 2_400_000, "delta": 2_400_000}.get(fam, 2_000_000)
        tile_size = 1280 if fam in ("krea2", "moss_image") else 1024
        method = str(self._sp_method.currentData() or "latent")
        pure_up = method == "upscale_only"
        scale = float(self._sp_scale.currentData() or 1.0)
        post = 1.0 if pure_up else float(self._sp_post_up.currentData() or 1.0)
        upscaler = str(self._sp_upscaler.currentData() or "")
        ow, oh = self._sp_state["orig_w"], self._sp_state["orig_h"]
        _, tier_label = resolution_tier(ow, oh)
        tw = max(align, int(math.floor(ow * scale / align + 0.5)) * align)
        th = max(align, int(math.floor(oh * scale / align + 0.5)) * align)
        pw = max(2, int(round(tw * post / 2)) * 2) if post > 1.0 else tw
        ph = max(2, int(round(th * post / 2)) * 2) if post > 1.0 else th
        over_limit = tw > 4096 or th > 4096 or pw > 6144 or ph > 6144
        missing_up = pure_up and not upscaler

        mode = str(self._sp_mode.currentData() or "auto")
        can_tile = tw > tile_size or th > tile_size
        tiled = (not pure_up) and (
            (mode == "tiled" and can_tile)
            or (mode == "auto" and tw * th > comfort and can_tile)
        )

        warn = False
        if over_limit:
            warn = True
            msg = f"{ow}×{oh} ({tier_label}) -> {tw}×{th}" + (f" -> 超分 {pw}×{ph}" if post > 1.0 else "") + " · 超过分辨率上限，请降低倍率"
        elif missing_up:
            warn = True
            msg = "未安装超分模型，无法执行纯超分锐化"
        elif pure_up:
            up_stem = Path(upscaler).stem
            msg = f"{ow}×{oh} ({tier_label}) -> {tw}×{th} · 纯超分锐化 ({up_stem})"
        else:
            msg = f"{ow}×{oh} ({tier_label}) -> {tw}×{th}" + (f" -> 超分 {pw}×{ph}" if post > 1.0 else "")
            msg += " · 分块高清" if tiled else " · 整图直出"

        box_name = "WarnBox" if warn else "InfoBox"
        if self._sp_size_box.objectName() != box_name:
            self._sp_size_box.setObjectName(box_name)
            self._sp_size_box.style().unpolish(self._sp_size_box)
            self._sp_size_box.style().polish(self._sp_size_box)
        self._sp_warn_icon.setVisible(warn)
        self._sp_size_hint.setText(msg)
        self._sp_start_btn.setText("开始纯超分锐化" if pure_up else "开始二次采样")
        self._sp_start_btn.setEnabled(not over_limit and not missing_up)

    def _run_second_pass(self) -> None:
        if not self._sp_state:
            return
        method = str(self._sp_method.currentData() or "latent")
        pure_up = method == "upscale_only"
        upscaler = str(self._sp_upscaler.currentData() or "")
        if pure_up and not upscaler:
            self.notify_requested.emit("未安装超分模型，无法执行纯超分锐化", True)
            return
        if not pure_up and not self._sp_pos.toPlainText().strip():
            self.notify_requested.emit("请填写二采正向提示词", True)
            return
        seed_val = None
        if not pure_up:
            seed_txt = self._sp_seed.text().strip()
            if seed_txt:
                try:
                    seed_val = int(seed_txt)
                    if not (0 <= seed_val < 2**53):
                        raise ValueError
                except ValueError:
                    self.notify_requested.emit("种子须为 0 至 9007199254740991 的整数", True)
                    return

        count = 1 if pure_up else int(self._sp_count.value())
        req: dict[str, Any] = {
            "action": "second_pass",
            "project": self._sp_state["pid"],
            "source_image_id": self._sp_state["base_id"],
            "checkpoint": str(self._sp_model.currentData() or ""),
            "scale": float(self._sp_scale.currentData() or 1.0),
            "method": method,
            "mode": "full" if pure_up else str(self._sp_mode.currentData() or "auto"),
            "sampler_name": str(self._sp_sampler.currentData() or "er_sde"),
            "scheduler": str(self._sp_scheduler.currentData() or "simple"),
            "denoise": float(self._sp_denoise.value()),
            "steps": int(self._sp_steps.value()),
            "count": count,
            "post_upscale": 1.0 if pure_up else float(self._sp_post_up.currentData() or 1.0),
            "loras": [] if pure_up else [
                {"file": f, "weight": w, "trigger_words": self._resolve_lora_trigger(f)}
                for f, w in self._sp_selected_loras.items()
            ],
            "positive": self._sp_pos.toPlainText(),
            "negative": self._sp_neg.toPlainText(),
            "krea2_abliterated_encoder": (not pure_up) and self._sp_krea_ablit_chk.isChecked() and not self._sp_krea_ablit_chk.isHidden(),
        }
        if upscaler:
            req["upscale_model"] = upscaler
        if seed_val is not None:
            req["seed"] = seed_val

        entry = self._current_sp_entry()
        prev_ids = {v["id"] for v in (entry["variants"] if entry else [])}
        try:
            res = self.app.action(req)
            self._sp_state["pending_prev_ids"] = prev_ids
            self._sp_state["pending_count"] = count
            self._sp_state["pending_jumped"] = False
            if pure_up:
                self.notify_requested.emit("已开始纯超分锐化，完成后将自动应用", False)
            elif count == 1:
                self.notify_requested.emit("已开始二次采样，完成后将自动应用", False)
            else:
                self.notify_requested.emit(f"已开始二次采样（{count} 张），完成后请在下方挑选并应用", False)
            if res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _select_sp_view(self, image_id: str) -> None:
        if self._sp_state:
            self._sp_state["pending_prev_ids"] = None
            if self._sp_state.get("view_id") != image_id:
                self._sp_state["view_id"] = image_id
                self._render_gallery()

    def _apply_active_sp(self) -> None:
        if not self._sp_state:
            return
        entry = self._current_sp_entry()
        if not entry:
            return
        view_id = self._sp_state.get("view_id")
        if not view_id or view_id == entry["base"]["id"]:
            return
        try:
            self.app.action({
                "action": "apply_second_pass",
                "project": self._sp_state["pid"],
                "source_image_id": self._sp_state["base_id"],
                "image_id": view_id,
            })
            self.notify_requested.emit("已应用该二采图", False)
            self.refresh_gallery()
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _unapply_active_sp(self) -> None:
        entry = self._current_sp_entry() if self._sp_state else (
            self._gallery_entries[self._active_gallery_idx] if self._gallery_entries else None
        )
        pid = self._sp_state["pid"] if self._sp_state else (entry["base"]["pid"] if entry else None)
        base_id = self._sp_state["base_id"] if self._sp_state else (entry["base"]["id"] if entry else None)
        if not pid or not base_id:
            return
        try:
            self.app.action({"action": "unapply_second_pass", "project": pid, "source_image_id": base_id})
            if self._sp_state is not None:
                self._sp_state["view_id"] = base_id
            self.notify_requested.emit("已恢复原图", False)
            self.refresh_gallery()
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def update_jobs(self, jobs: list[dict]) -> dict | None:
        return self._job_banner.update_jobs(jobs)

    def update_drawing_model_state(self, dm: dict, *, busy: bool = False) -> None:
        self._dm_loaded_cp = dm.get("loaded")
        self._dm_running = bool(dm.get("comfy_running"))
        self._dm_busy = bool(busy)
        self._update_inline_preload_btn()

    def _update_inline_preload_btn(self) -> None:
        if not hasattr(self, "_inline_preload_btn"):
            return
        cp = self._current_checkpoint()
        t = tokens(current_theme())
        same_loaded = bool(cp and self._dm_loaded_cp == cp)
        self._inline_preload_btn.setEnabled(not self._dm_busy and bool(cp))
        if same_loaded:
            self._inline_preload_btn.setText("卸载")
            self._inline_preload_btn.setProperty("danger", True)
            self._inline_preload_btn.setProperty("ghost", False)
            self._inline_preload_btn.setIcon(studio_icon("stop", t["danger"], 12))
            self._inline_preload_btn.setToolTip("当前底模已预加载常驻，点击卸载释放显存")
        else:
            self._inline_preload_btn.setText("预加载")
            self._inline_preload_btn.setProperty("danger", False)
            self._inline_preload_btn.setProperty("ghost", True)
            self._inline_preload_btn.setIcon(studio_icon("play", t["muted"], 12))
            self._inline_preload_btn.setToolTip("预加载当前所选底模到显存")
        self._inline_preload_btn.style().unpolish(self._inline_preload_btn)
        self._inline_preload_btn.style().polish(self._inline_preload_btn)

    def _toggle_inline_preload(self) -> None:
        cp = self._current_checkpoint()
        if not cp:
            return
        try:
            if self._dm_loaded_cp == cp:
                res = self.app.action({"action": "unload_drawing_model"})
                self.notify_requested.emit("正在卸载绘图模型并释放显存", False)
            else:
                self.app.remember_checkpoint(cp)
                res = self.app.action({"action": "preload_drawing_model", "checkpoint": cp})
                self.notify_requested.emit(f"正在预加载 {self.model_combo.currentText()}", False)
            if isinstance(res, dict) and res.get("job"):
                self.job_started.emit(res["job"])
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _open_model_folder(self) -> None:
        try:
            self.app.action({"action": "open_drawing_model_folder", "checkpoint": self._current_checkpoint()})
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)

    def _open_lora_folder(self) -> None:
        try:
            self.app.action({"action": "open_lora_folder", "checkpoint": self._current_checkpoint()})
        except Exception as exc:
            self.notify_requested.emit(str(exc), True)
